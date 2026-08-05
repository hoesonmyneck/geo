"""Чинит испорченные адреса РКА из broken_rca.txt.

Два класса порчи (см. классификацию — они лечатся по-разному):

  A. Префикс обрублен (адрес начинается с запятой). Кэш full_path_* в их
     индексе протух: когда-то обход связей зациклился и намотал повторы,
     не дойдя до "Республика Казахстан". В ЖИВЫХ данных цикла уже нет.
     Лечение: пересобрать через API из чистого s_ats.full_path_rus
     + улица (тип из словаря + название) + хвост (дом/квартира) из
     исходной строки — хвост порчей не задет.

  B. Префикс цел, но сегмент задублирован. Это НЕ протухший кэш: в реестре
     реально лежат два геонима с одним именем, вложенных друг в друга
     (напр. "МИКРОРАЙОН Мирас" → родитель "МИКРОРАЙОН Мирас"). Пересборка
     через API дала бы тот же дубль, поэтому API не трогаем — схлопываем
     повтор в тексте.

Результат: rka_fixed.jsonl — {rca, full_path_rus, full_path_kaz, class, ...}.
Резюмируемо: при повторном запуске уже починенные пропускаются.

Запуск:
    python backend/worker/fix_broken_rka.py [--workers 30] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BROKEN = ROOT / "broken_rca.txt"
SRC = ["rka_egov_output.jsonl", "rka_egov_extra.jsonl", "rka_egov_reparse.jsonl"]
OUT = ROOT / "rka_fixed.jsonl"

API = "http://data.egov.kz/api/detailed"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

TAIL = ("дом", "Квартира", "үй", "Пәтер", "строение", "здание", "д.", "кв.")

# В их словаре казахские типы записаны с ЛАТИНСКИМИ двойниками:
# "КӨШЕCI" -> C=U+0043, I=U+0049 вместо с=U+0441, і=U+0456.
# Выглядит одинаково, байты разные -> адрес становится непоискуемым.
_HOMOGLYPH = str.maketrans({
    "c": "с", "i": "і", "a": "а", "e": "е", "o": "о", "p": "р",
    "x": "х", "y": "у", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
})

_lock = threading.Lock()
_cache: dict[str, dict | None] = {}


def _api(index: str, field: str, value: str) -> dict | None:
    """Один объект из индекса по точному совпадению поля (с кэшем)."""
    ck = f"{index}|{field}|{value}"
    with _lock:
        if ck in _cache:
            return _cache[ck]
    body = json.dumps({"query": {"term": {field: value}}})
    url = f"{API}/{index}?source={urllib.parse.quote(body)}"
    res = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30, context=_CTX) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            data = d.get("data") if isinstance(d, dict) else d
            res = data[0] if data else None
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    with _lock:
        _cache[ck] = res
    return res


def _kz_type(word: str) -> str:
    """Казахский тип из словаря + починка латинских двойников.

    Регистр понижаем ТОЛЬКО у значений, записанных целиком капсом ("КӨШЕCI"
    -> "көшесі"). Часть словаря уже в нормальном регистре, и слепой .lower()
    её ломает: у корня тип "Республикасы" превращался в "республикасы",
    т.е. "Қазақстан республикасы" вместо канонического "Қазақстан Республикасы".
    """
    w = (word or "").strip()
    if w.isupper():
        w = w.lower()
    return w.translate(_HOMOGLYPH)


def _segs(addr: str) -> list[str]:
    return [s.strip() for s in (addr or "").split(",") if s.strip()]


def dedup(addr: str) -> str:
    """Схлопывает повторы сегментов, сохраняя первое вхождение и порядок.
    Хвост (дом/квартира) не трогаем — там повтор законен."""
    out, seen = [], set()
    for t in _segs(addr):
        if t.startswith(TAIL):
            out.append(t)
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return ", ".join(out)


def has_repeat(addr: str) -> bool:
    seen = set()
    for t in _segs(addr):
        if t.startswith(TAIL):
            continue
        if t in seen:
            return True
        seen.add(t)
    return False


def tail_of(addr: str) -> list[str]:
    """Хвостовые сегменты ('дом 18', 'Квартира 1') — они порчей не задеты."""
    return [t for t in _segs(addr) if t.startswith(TAIL)]


def api_tail(item: dict, bld: dict) -> tuple[list[str], list[str]]:
    """Хвост (дом + квартира) из API, а не из старой строки.

    По строке ловить нельзя: список хвостовых слов не закрыт — например
    казахское 'құрылыс 33/1' (строение) в него не попадало, и номер дома
    молча терялся. У API слово всегда берётся из словаря.
    """
    ru: list[str] = []
    kz: list[str] = []

    num = (bld.get("number") or "").strip()
    if num:
        p = _api("d_buildings_pointers", "id", bld.get("d_buildings_pointer_id") or "") or {}
        w_ru = (p.get("value_ru") or "дом").strip().lower()
        w_kz = _kz_type(p.get("value_kz") or "үй")
        ru.append(f"{w_ru} {num}")
        kz.append(f"{w_kz} {num}")

    # Квартира — только если исходная запись из s_pb
    if item.get("_index") == "s_pb":
        rnum = (item.get("number") or "").strip()
        if rnum:
            t = _api("d_rooms_types", "id", item.get("d_room_type_id") or "") or {}
            w_ru = (t.get("value_ru") or "Квартира").strip().capitalize()
            w_kz = _kz_type(t.get("value_kz") or "пәтер").capitalize()
            ru.append(f"{w_ru} {rnum}")
            kz.append(f"{w_kz} {rnum}")
    return ru, kz


def valid(addr: str, kaz: bool = False) -> bool:
    if not addr:
        return False
    head = "Қазақстан Республикасы" if kaz else "Республика Казахстан"
    return addr.startswith(head) and not has_repeat(addr)


def _ats_path(ats_id: str) -> tuple[str, str] | None:
    """Чистый путь АТЕ. Сначала готовое поле, а если и оно с повтором —
    собираем сами по parent_id (со списком посещённых: лимит прыжков как
    защита от цикла — ровно то, что сломало их же данные)."""
    a = _api("s_ats", "id", ats_id)
    if not a:
        return None
    ru, kz = (a.get("full_path_rus") or "").strip(), (a.get("full_path_kaz") or "").strip()
    if valid(ru) and not has_repeat(kz):
        return ru, kz

    parts_ru, parts_kz, seen = [], [], set()
    aid = ats_id
    while aid and aid not in seen:
        seen.add(aid)
        node = _api("s_ats", "id", aid)
        if not node:
            break
        t = _api("d_ats_types", "id", node.get("d_ats_type_id") or "") or {}
        n_ru, n_kz = (node.get("name_rus") or "").strip(), (node.get("name_kaz") or "").strip()
        if n_ru:
            parts_ru.append(f"{(t.get('value_ru') or '').strip().lower()} {n_ru}".strip())
        if n_kz:
            parts_kz.append(f"{n_kz} {_kz_type(t.get('value_kz'))}".strip())
        aid = node.get("parent_id") or None
    if not parts_ru:
        return None
    # Тип верхнего узла из словаря — "Республика", а .lower() давал
    # "республика Казахстан", что валидатор (справедливо) отвергал.
    ru_out = ", ".join(reversed(parts_ru))
    kz_out = ", ".join(reversed(parts_kz))
    return ru_out[:1].upper() + ru_out[1:], (kz_out[:1].upper() + kz_out[1:]) if kz_out else ""


def fix_a(rca: str, old_ru: str, old_kz: str) -> dict:
    """Класс A: пересборка через API."""
    item = _api("s_buildings,s_grounds_new,s_pb", "rca", rca)
    if not item:
        return {"rca": rca, "status": "failed", "reason": "no-record"}

    bld = item
    if item.get("_index") == "s_pb" and item.get("s_building_id"):
        b = _api("s_buildings", "id", item["s_building_id"])
        if b:
            bld = b

    gid, aid = bld.get("s_geonim_id"), bld.get("s_ats_id")
    street_ru = street_kz = ""
    if gid:
        g = _api("s_geonims", "id", gid)
        if g:
            gt = _api("d_geonims_types", "id", g.get("d_geonims_type_id") or "") or {}
            street_ru = f"{(gt.get('value_ru') or '').strip().lower()} {(g.get('name_rus') or '').strip()}".strip()
            street_kz = f"{(g.get('name_kaz') or '').strip()} {_kz_type(gt.get('value_kz'))}".strip()
            aid = g.get("s_ats_id") or aid
    if not aid:
        return {"rca": rca, "status": "failed", "reason": "no-ats"}

    ap = _ats_path(aid)
    if not ap:
        return {"rca": rca, "status": "failed", "reason": "no-ats-path"}
    base_ru, base_kz = ap

    tail, tail_kz = api_tail(item, bld)
    new_ru = ", ".join([p for p in [base_ru, street_ru] if p] + tail)
    new_kz = ", ".join([p for p in [base_kz, street_kz] if p] + tail_kz) if base_kz else ""

    if not valid(new_ru):
        return {"rca": rca, "status": "failed", "reason": "invalid-result", "got": new_ru[:120]}
    return {
        "rca": rca, "status": "ok", "class": "A",
        "full_path_rus": new_ru,
        "full_path_kaz": new_kz if valid(new_kz, kaz=True) else None,
        "s_building_id": item.get("s_building_id") or bld.get("id"),
        "index": item.get("_index"),
    }


def fix_b(rca: str, old_ru: str, old_kz: str, sbid, index) -> dict:
    """Класс B: схлопывание дубля, без API.

    Префикс "Республика Казахстан" здесь НЕ требуем, в отличие от класса A.
    Схлопывание голову строки не трогает: какой префикс был у источника,
    такой и останется. А часть их записей (напр. гаражи) изначально лежит
    в капс-формате вообще без префикса — это их стиль, а не наша порча.
    """
    new_ru, new_kz = dedup(old_ru), dedup(old_kz)
    if not new_ru or has_repeat(new_ru):
        return {"rca": rca, "status": "failed", "reason": "dedup-invalid", "got": new_ru[:120]}
    return {
        "rca": rca, "status": "ok", "class": "B",
        "full_path_rus": new_ru,
        "full_path_kaz": new_kz or None,
        "s_building_id": sbid, "index": index,
    }


def load_source(broken: set[str]) -> dict[str, dict]:
    """Собирает по битым РКА последнюю версию записи из выгрузок."""
    KEYS = ('"rca": "', '"full_path_rus": "', '"full_path_kaz": "',
            '"s_building_id": "', '"index": "')

    def field(line, key):
        i = line.find(key)
        if i < 0:
            return None
        i += len(key)
        j = line.find('", "', i)
        if j < 0:
            j = line.find('"}', i)
        return line[i:j] if j > 0 else None

    out: dict[str, dict] = {}
    for name in SRC:
        p = ROOT / name
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if '"status": "ok"' not in line:
                    continue
                rca = field(line, KEYS[0])
                if rca not in broken:
                    continue
                out[rca] = {
                    "rus": field(line, KEYS[1]) or "",
                    "kaz": field(line, KEYS[2]) or "",
                    "sbid": field(line, KEYS[3]),
                    "index": field(line, KEYS[4]),
                }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    broken = set(BROKEN.read_text(encoding="utf-8").split())
    done: set[str] = set()
    if OUT.exists():
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("status") == "ok":
                    done.add(r["rca"])
    print(f"битых: {len(broken):,} | уже починено: {len(done):,}")

    src = load_source(broken)
    todo = [r for r in sorted(broken) if r not in done and r in src]
    if args.limit:
        todo = todo[: args.limit]
    cls_a = [r for r in todo if src[r]["rus"].startswith(", ")]
    cls_b = [r for r in todo if not src[r]["rus"].startswith(", ")]
    print(f"к работе: {len(todo):,} (A={len(cls_a):,} через API, B={len(cls_b):,} текстом)\n")

    out = open(OUT, "a", encoding="utf-8")
    stat = {"ok": 0, "failed": 0}

    def write(rec):
        with _lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stat["ok" if rec["status"] == "ok" else "failed"] += 1
            n = stat["ok"] + stat["failed"]
            if n % 200 == 0:
                out.flush()
                print(f"  {n:,}/{len(todo):,} ok={stat['ok']:,} failed={stat['failed']:,}",
                      file=sys.stderr, flush=True)

    # B — без сети, быстро
    for rca in cls_b:
        s = src[rca]
        write(fix_b(rca, s["rus"], s["kaz"], s["sbid"], s["index"]))
    print(f"класс B готов: {stat['ok']:,} ok, {stat['failed']:,} failed")

    # A — через API
    t0 = time.time()
    if cls_a:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for rec in ex.map(lambda r: _safe(r, src[r]), cls_a):
                write(rec)
    out.close()
    print(f"\nГотово за {(time.time()-t0)/60:.1f} мин. ok={stat['ok']:,} failed={stat['failed']:,}")
    print(f"Записано: {OUT}")


def _safe(rca: str, s: dict) -> dict:
    try:
        return fix_a(rca, s["rus"], s["kaz"])
    except Exception as e:
        return {"rca": rca, "status": "failed", "reason": f"{type(e).__name__}: {e}"[:120]}


if __name__ == "__main__":
    main()
