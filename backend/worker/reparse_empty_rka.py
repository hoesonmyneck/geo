"""
Перепарсинг РКА с пустым адресом: достраиваем адрес из связанных справочников.

Адрес у "пустых" РКА есть, но вложен в другие сущности:
  - s_pb (квартира) без адреса → s_buildings?_id=s_building_id (адрес дома)
  - дом без адреса → s_geonims?_id=s_geonim_id (улица) + ", дом {number}"
  - если у geonim нет верхнего уровня → дополняем s_ats?_id (город)

Вход: rka_egov_output.jsonl + rka_egov_extra.jsonl (берём записи без адреса).
Выход: rka_egov_reparse.jsonl  {rca, addr, method, s_building_id}
Резюмируемый. Кэш справочников s_geonims/s_ats в памяти.

Запуск:
    python backend/worker/reparse_empty_rka.py [--workers 40] [--limit N]
"""
from __future__ import annotations
import argparse, json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
import urllib.request, urllib.error

ROOT = Path(__file__).resolve().parents[2]
SRC_FILES = [ROOT / "rka_egov_output.jsonl", ROOT / "rka_egov_extra.jsonl"]
OUT_PATH = ROOT / "rka_egov_reparse.jsonl"
BASE = 'http://data.egov.kz/api/detailed/{}?source={}'

_write_lock = threading.Lock()
_counter = {"done": 0, "ok": 0, "fail": 0}
_count_lock = threading.Lock()
# Кэши справочников (потокобезопасны достаточно: dict в CPython под GIL)
_geonim_cache: dict[str, dict] = {}
_ats_cache: dict[str, dict] = {}
_pointer_cache: dict[str, str] = {}   # d_buildings_pointer_id → value_ru (дом/строение/...)


def _query(index: str, field: str, value: str, timeout=12.0, retries=2):
    src = json.dumps({"query": {"term": {field: value}}}, ensure_ascii=False)
    url = BASE.format(index, quote(src, safe=""))
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "geo-rka/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
            items = d.get("data") if isinstance(d, dict) else (d if isinstance(d, list) else [])
            return items[0] if items else None
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last = e
            time.sleep(0.4 * (attempt + 1))
    raise last


def _get_geonim(gid: str):
    if gid in _geonim_cache:
        return _geonim_cache[gid]
    v = _query("s_geonims", "_id", gid)
    _geonim_cache[gid] = v
    return v


def _get_ats(aid: str):
    if aid in _ats_cache:
        return _ats_cache[aid]
    v = _query("s_ats", "_id", aid)
    _ats_cache[aid] = v
    return v


def _get_pointer(pid: str) -> str:
    """Тип строения (дом/строение/...) по d_buildings_pointer_id. Дефолт 'дом'."""
    if not pid:
        return "дом"
    if pid in _pointer_cache:
        return _pointer_cache[pid]
    try:
        v = _query("d_buildings_pointers", "_id", pid)
    except Exception:
        v = None
    word = (v.get("value_ru") if v else None) or "дом"
    word = word.strip().lower()
    _pointer_cache[pid] = word
    return word


# Кэш справочников типов: (index, id) → запись (value_ru / short_value_ru)
_type_cache: dict[tuple, dict] = {}

def _get_type(index: str, tid: str) -> dict:
    if not tid:
        return {}
    key = (index, tid)
    if key in _type_cache:
        return _type_cache[key]
    try:
        v = _query(index, "_id", tid) or {}
    except Exception:
        v = {}
    _type_cache[key] = v
    return v


# ─── Атомарная сборка адреса (фоллбэк, логика сайта data.egov.kz/services/rka) ──
# Используется только когда готового full_path_rus нет ни в одном справочнике.
# Собираем АДРЕС ДОМА (без квартиры): {ats-цепочка} {geonim-цепочка} {дом}.

def _ats_chain(ats_id: str, kaz: bool = False) -> str:
    """Рекурсивно вверх по parent_id для города/области/республики.
    рус: '{тип} {name}'  |  каз: '{name} {тип}' (обратный порядок)."""
    parts = []
    aid, guard = ats_id, 0
    while aid and guard < 12:
        guard += 1
        a = _get_ats(aid)
        if not a:
            break
        t = _get_type("d_ats_types", a.get("d_ats_type_id"))
        if kaz:
            seg = f"{(a.get('name_kaz') or '').strip()} {(t.get('value_kz') or '').strip()}".strip()
        else:
            seg = f"{(t.get('value_ru') or '').strip()} {(a.get('name_rus') or '').strip()}".strip()
        if seg:
            parts.append(seg)
        aid = a.get("parent_id") or None
    return " ".join(reversed(parts))   # республика → область → город


def _geonim_chain(geonim_id: str, kaz: bool = False) -> tuple[str, str | None]:
    """Цепочка геонимов. рус: '{тип} {name}' | каз: '{name} {тип}'.
    Возвращает (текст, s_ats_id для продолжения)."""
    parts = []
    gid, last_ats, guard = geonim_id, None, 0
    while gid and guard < 8:
        guard += 1
        g = _get_geonim(gid)
        if not g:
            break
        t = _get_type("d_geonims_types", g.get("d_geonims_type_id"))
        if kaz:
            seg = f"{(g.get('name_kaz') or '').strip()} {(t.get('value_kz') or '').strip()}".strip()
        else:
            seg = f"{(t.get('value_ru') or '').strip()} {(g.get('name_rus') or '').strip()}".strip()
        if seg:
            parts.append(seg)
        last_ats = g.get("s_ats_id") or last_ats
        gid = g.get("parent_id") or None
    return " ".join(reversed(parts)), last_ats


def _assemble_house(bld: dict, kaz: bool = False) -> str | None:
    """Собирает адрес дома из атомов записи s_buildings (name + типы + parent)."""
    num = (bld.get("number") or "").strip()
    ptr = _get_type("d_buildings_pointers", bld.get("d_buildings_pointer_id"))
    if kaz:
        word = (ptr.get("value_kz") or "үй").strip()   # "үй 15"
        txt_house = f"{word} {num}" if num else ""
    else:
        short = (ptr.get("short_value_ru") or "д.").strip()
        txt_house = f"{short}{num}" if num else ""     # "д.15"

    geo_txt, geo_ats = "", None
    gid = bld.get("s_geonim_id")
    if gid:
        geo_txt, geo_ats = _geonim_chain(gid, kaz)
    aid = geo_ats or bld.get("s_ats_id")
    ats_txt = _ats_chain(aid, kaz) if aid else ""

    full = " ".join(p for p in (ats_txt, geo_txt, txt_house) if p).strip()
    return full or None


def _clean(addr: str) -> str:
    return addr.strip().lstrip(",").strip()


def reparse(rca: str) -> dict:
    item = _query("s_buildings,s_grounds_new,s_pb", "rca", rca)
    if not item:
        return {"rca": rca, "status": "empty", "method": "no-record"}
    if item.get("full_path_rus"):
        return {"rca": rca, "status": "ok", "method": "direct",
                "full_path_rus": item["full_path_rus"], "full_path_kaz": item.get("full_path_kaz"),
                "s_building_id": item.get("s_building_id")}

    # Запись дома
    bld = item
    if item.get("_index") == "s_pb" and item.get("s_building_id"):
        b = _query("s_buildings", "_id", item["s_building_id"])
        if b:
            bld = b
    sbid = item.get("s_building_id") or bld.get("_id")
    if bld.get("full_path_rus"):
        return {"rca": rca, "status": "ok", "method": "house",
                "full_path_rus": bld["full_path_rus"], "full_path_kaz": bld.get("full_path_kaz"),
                "s_building_id": sbid}

    num = bld.get("number")
    gid = bld.get("s_geonim_id")
    aid = bld.get("s_ats_id")
    grid = bld.get("s_ground_id")
    ptr = _get_type("d_buildings_pointers", bld.get("d_buildings_pointer_id"))
    ptr_ru = (ptr.get("value_ru") or "дом").strip().lower()
    ptr_kz = (ptr.get("value_kz") or "үй").strip().lower()

    def _wn(base, word):   # добавить ", {word} {num}"
        base = _clean(base)
        if not base:
            return base
        return f"{base}, {word} {num}" if num else base

    # Сборка через улицу (s_geonims) — берём готовые рус+каз
    if gid:
        geo = _get_geonim(gid)
        if geo and geo.get("full_path_rus") is not None:
            s_ru = geo["full_path_rus"].strip()
            s_kz = (geo.get("full_path_kaz") or "").strip()
            # geonim без верхнего уровня → дополняем городом
            if s_ru.startswith(",") and (aid or geo.get("s_ats_id")):
                ats = _get_ats(aid or geo.get("s_ats_id"))
                if ats and ats.get("full_path_rus"):
                    s_ru = ats["full_path_rus"].rstrip() + s_ru
                    if ats.get("full_path_kaz") and s_kz.startswith(","):
                        s_kz = ats["full_path_kaz"].rstrip() + s_kz
            addr_ru = _wn(s_ru, ptr_ru)
            if addr_ru:
                return {"rca": rca, "status": "ok", "method": "geonim",
                        "full_path_rus": addr_ru,
                        "full_path_kaz": _wn(s_kz, ptr_kz) if s_kz else None,
                        "s_building_id": sbid}

    # Фоллбэк: земельный участок (s_grounds)
    if grid:
        g = None
        try:
            g = _query("s_grounds_new", "_id", grid) or _query("s_grounds", "_id", grid)
        except Exception:
            g = None
        if g and g.get("full_path_rus"):
            return {"rca": rca, "status": "ok", "method": "ground",
                    "full_path_rus": _clean(g["full_path_rus"]),
                    "full_path_kaz": _clean(g["full_path_kaz"]) if g.get("full_path_kaz") else None,
                    "s_building_id": sbid}

    # Фоллбэк: только город + дом
    if aid:
        ats = _get_ats(aid)
        if ats and ats.get("full_path_rus"):
            return {"rca": rca, "status": "ok", "method": "ats-only",
                    "full_path_rus": _wn(ats["full_path_rus"], ptr_ru),
                    "full_path_kaz": _wn(ats["full_path_kaz"], ptr_kz) if ats.get("full_path_kaz") else None,
                    "s_building_id": sbid}

    # Последний фоллбэк: атомарная сборка (рус + каз)
    a_ru = _assemble_house(bld, kaz=False)
    if a_ru:
        return {"rca": rca, "status": "ok", "method": "assembled",
                "full_path_rus": a_ru, "full_path_kaz": _assemble_house(bld, kaz=True),
                "s_building_id": sbid}

    return {"rca": rca, "status": "failed", "method": "failed", "s_building_id": sbid}


def load_todo() -> list[str]:
    todo = set()
    for fn in SRC_FILES:
        if not fn.exists():
            continue
        for line in fn.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = r.get("status")
            if s == "empty" or (s == "ok" and not r.get("full_path_rus")):
                todo.add(r.get("rca"))
    return list(todo)


def load_done(path: Path) -> set[str]:
    done = set()
    if not path.exists():
        return done
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("status") in ("ok", "empty"):   # error/failed перезапрашиваем
            done.add(r.get("rca"))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=str, default=str(OUT_PATH))
    args = ap.parse_args()
    out_path = Path(args.out)

    print("Собираю пустые RCA ...", file=sys.stderr)
    todo = load_todo()
    print(f"Пустых всего: {len(todo):,}", file=sys.stderr)
    done = load_done(out_path)
    if done:
        print(f"Уже сделано (резюм): {len(done):,}", file=sys.stderr)
    todo = [c for c in todo if c not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"К обработке: {len(todo):,} (потоков {args.workers})", file=sys.stderr)
    if not todo:
        return

    t0 = time.time()
    fout = out_path.open("a", encoding="utf-8")

    def handle(res: dict):
        # method не пишем в файл (только для статистики в логе)
        rec = {k: v for k, v in res.items() if k != "method"}
        with _write_lock:
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with _count_lock:
            _counter["done"] += 1
            if res.get("status") == "ok":
                _counter["ok"] += 1
            elif res.get("status") in ("failed", "error"):
                _counter["fail"] += 1
            n = _counter["done"]
        if n % 2000 == 0:
            with _write_lock:
                fout.flush()
            rate = n / max(time.time() - t0, 1e-6)
            eta = (len(todo) - n) / max(rate, 1e-6)
            print(f"  {n:,}/{len(todo):,} ok={_counter['ok']:,} fail={_counter['fail']:,} "
                  f"{rate:.0f}/s ETA {eta/3600:.1f}ч cache(geo={len(_geonim_cache)},ats={len(_ats_cache)})",
                  file=sys.stderr)

    def work(rca):
        try:
            return reparse(rca)
        except Exception as e:
            return {"rca": rca, "status": "error", "method": "exc", "error": str(e)}

    CHUNK = 20_000
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for i in range(0, len(todo), CHUNK):
                batch = todo[i:i + CHUNK]
                futs = [ex.submit(work, c) for c in batch]
                for f in as_completed(futs):
                    handle(f.result())
                with _write_lock:
                    fout.flush()
    finally:
        fout.flush()
        fout.close()

    dt = time.time() - t0
    print(f"\nГотово за {dt/3600:.2f}ч. ok={_counter['ok']:,} fail={_counter['fail']:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
