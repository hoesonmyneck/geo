"""Тест геокода населения 2ГИС с классификацией точности — БЕЗ записи в БД.

Берёт смешанную выборку (город + сёла), геокодит, для каждого сверяет ответ
2ГИС с адресом и метит точность:
  город:  дом+город совпал -> высокая;  город найден, но adm_div/улица -> средняя;
          вернулся другой город -> низкая.
  село:   само село найдено -> высокая;  упал в район -> средняя;  иначе -> низкая.
Печатает сводку + примеры каждого уровня.

Запуск (ХОСТ): python backend/worker/geocode_pop_test.py <2GIS_KEY> [N_на_тип]
"""
from __future__ import annotations
import asyncio, re, sys
from collections import defaultdict
import httpx, psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"
API = "https://catalog.api.2gis.com/3.0/items/geocode"
SEM = 10


def _deep_place(addr):
    m = re.findall(r'(?:город(?:\s+\S+\s+значения)?|село|аул|пос[её]лок|станция|разъезд|кент)\s+([^,]+)', addr, re.I)
    return m[-1].strip() if m else None

def _raion(addr):
    m = re.search(r'район\s+([^,]+)', addr, re.I)
    return m.group(1).strip() if m else None

# Казахские буквы → ближайшие русские (для сравнения адрес↔ответ 2ГИС, где
# один по-казахски, другой по-русски: Үштөбе≈Уштобе, Еркінқала≈Еркинкала).
_KZ = str.maketrans({
    'ә': 'а', 'ө': 'о', 'ү': 'у', 'ұ': 'у', 'қ': 'к', 'ғ': 'г',
    'ң': 'н', 'һ': 'х', 'і': 'и', 'ё': 'е',
})

def _norm(s):
    return (s or "").lower().translate(_KZ)

def _hit(name, hay):
    """Совпало ли название (нормализуем каз↔рус + матч по основе слова)."""
    if not name:
        return False
    n = _norm(name).strip()
    h = _norm(hay)
    if n and n in h:
        return True
    for w in re.split(r'[\s\-.,]+', n):
        if len(w) < 4:
            continue
        if w in h:
            return True
        # обрезаем окончание (Зачаганск-ий≈Зачаганск, -ое, -ая)
        stem = w[:-2] if len(w) >= 7 else w
        if len(stem) >= 5 and stem in h:
            return True
    return False


async def geocode(client, key, addr):
    params = {"q": addr, "fields": "items.point,items.adm_div,items.full_name", "key": key}
    for attempt in range(4):
        try:
            r = await client.get(API, params=params, timeout=15.0)
        except (httpx.TimeoutException, httpx.NetworkError):
            await asyncio.sleep(1.0 * (attempt + 1)); continue
        if r.status_code != 200:
            if r.status_code == 429:
                await asyncio.sleep(4); continue
            return {"err": f"HTTP {r.status_code}"}
        data = r.json(); meta = data.get("meta") or {}
        if meta.get("code") == 403:
            return {"err": "403 " + str(((meta.get("error") or {}).get("message")))}
        if meta.get("code") == 429:
            await asyncio.sleep(4); continue
        items = (data.get("result") or {}).get("items") or []
        if not items:
            return {"miss": True}
        it = items[0]; pt = it.get("point")
        adm = " ".join(a.get("name", "") for a in (it.get("adm_div") or []))
        return {"lat": pt and pt.get("lat"), "lon": pt and pt.get("lon"),
                "type": it.get("type"), "full": it.get("full_name") or "", "adm": adm}
    return {"err": "retries"}


def classify(kind, addr, res):
    hay = (res.get("full", "") + " " + res.get("adm", "")).lower()
    place_hit = _hit(_deep_place(addr), hay)
    raion_hit = _hit(_raion(addr), hay)
    if kind == "city_apt":
        if res.get("type") == "building" and place_hit: return "высокая"
        if place_hit: return "средняя"
        return "низкая"
    else:  # village
        if place_hit: return "высокая"
        if raion_hit: return "средняя"
        return "низкая"


async def main():
    if len(sys.argv) < 2:
        print("нужен ключ 2ГИС", file=sys.stderr); sys.exit(1)
    key = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 150

    conn = psycopg.connect(DSN)
    rows = []
    for kind in ("city_apt", "village"):
        rows += conn.execute(
            "SELECT kind, geocode_addr FROM pop_dwelling WHERE kind=%s AND lat IS NULL "
            "AND geocode_addr IS NOT NULL AND geocode_addr<>'' ORDER BY random() LIMIT %s",
            (kind, n)).fetchall()
    conn.close()
    print(f"тест: {len(rows)} точек (по {n} на тип), БЕЗ записи в БД\n")

    sem = asyncio.Semaphore(SEM)
    stats = defaultdict(lambda: defaultdict(int))
    examples = defaultdict(list)
    miss = err = 0

    async with httpx.AsyncClient(headers={"User-Agent": "geo-pop-test/1.0"}, verify=False) as client:
        async def work(kind, addr):
            nonlocal miss, err
            async with sem:
                res = await geocode(client, key, addr)
            if res.get("err"):
                err += 1; return
            if res.get("miss"):
                miss += 1; stats[kind]["не найдено"] += 1; return
            lvl = classify(kind, addr, res)
            stats[kind][lvl] += 1
            if len(examples[(kind, lvl)]) < 4:
                examples[(kind, lvl)].append((addr, res))
        await asyncio.gather(*(work(k, a) for k, a in rows))

    print("=== СВОДКА ТОЧНОСТИ ===")
    for kind in ("city_apt", "village"):
        tot = sum(stats[kind].values())
        parts = "  ".join(f"{lvl}={c} ({c/tot*100:.0f}%)" for lvl, c in
                          sorted(stats[kind].items(), key=lambda x: -x[1]))
        print(f"  {kind:10} всего={tot}:  {parts}")
    print(f"  ошибок сети/ключа: {err}")

    print("\n=== ПРИМЕРЫ ===")
    for kind in ("city_apt", "village"):
        for lvl in ("высокая", "средняя", "низкая", "не найдено"):
            exs = examples.get((kind, lvl), [])
            if not exs:
                continue
            print(f"\n--- {kind} / {lvl} ---")
            for addr, res in exs:
                c = f"{res['lat']:.4f},{res['lon']:.4f}" if res.get("lat") else "-"
                print(f"  адрес: {addr[:74]}")
                print(f"  2ГИС:  {c} тип={res.get('type')}  «{res.get('full','')[:60]}»")


if __name__ == "__main__":
    import urllib3
    try: urllib3.disable_warnings()
    except Exception: pass
    asyncio.run(main())
