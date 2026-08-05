"""Эксперимент: какой ФОРМАТ запроса лучше находит сёла в 2ГИС.

Лишний префикс (район, сельский округ) сбивает 2ГИС на центр района. Пробуем
разные формулировки и сравниваем % точных попаданий в само село.
БЕЗ записи в БД.

Запуск (ХОСТ): python backend/worker/geocode_village_variants.py <KEY> [N]
"""
from __future__ import annotations
import asyncio, re, sys
from collections import defaultdict
import httpx, psycopg
from importlib.machinery import SourceFileLoader

t = SourceFileLoader("t", "backend/worker/geocode_pop_test.py").load_module()
DSN, geocode, _hit = t.DSN, t.geocode, t._hit
SEM = 10


def _oblast(addr):
    m = re.search(r'област[ьи]\s+([^,]+)', addr, re.I)
    return m.group(1).strip() if m else None

def _place(addr):
    return t._deep_place(addr)          # имя НП после село/аул/поселок/...

def variants(addr):
    W, O, R = _place(addr), _oblast(addr), t._raion(addr)
    if not W:
        return None
    v = {"A_полный": addr}
    if O: v["B_село+обл"] = f"село {W}, область {O}"
    v["C_село"] = f"село {W}"
    if O and R: v["D_село+рай+обл"] = f"село {W}, район {R}, область {O}"
    return W, O, v


def grade(W, O, res):
    if res.get("err") or res.get("miss"):
        return "miss"
    hay = (res.get("full", "") + " " + res.get("adm", "")).lower()
    place = _hit(W, hay)
    obl   = _hit(O, hay) if O else True
    if place and obl:
        return "точно_в_село"
    if place and not obl:
        return "чужая_область"        # то же имя, другой регион
    if _hit("район", hay) or res.get("type") == "adm_div":
        return "район/коарс"
    return "мимо"


async def main():
    key = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    conn = psycopg.connect(DSN)
    rows = conn.execute(
        "SELECT geocode_addr FROM pop_dwelling WHERE kind='village' AND lat IS NULL "
        "AND geocode_addr<>'' ORDER BY random() LIMIT %s", (n,)).fetchall()
    conn.close()
    samples = [variants(a) for (a,) in rows]
    samples = [s for s in samples if s]
    print(f"сёл в тесте: {len(samples)}\n")

    sem = asyncio.Semaphore(SEM)
    tally = defaultdict(lambda: defaultdict(int))
    async with httpx.AsyncClient(headers={"User-Agent": "vexp/1"}, verify=False) as client:
        async def work(W, O, name, q):
            async with sem:
                res = await geocode(client, key, q)
            tally[name][grade(W, O, res)] += 1
        tasks = []
        for W, O, vs in samples:
            for name, q in vs.items():
                tasks.append(work(W, O, name, q))
        await asyncio.gather(*tasks)

    print("=== формат запроса → распределение (по сёлам) ===")
    order = ["точно_в_село", "чужая_область", "район/коарс", "мимо", "miss"]
    for name in ["A_полный", "B_село+обл", "C_село", "D_село+рай+обл"]:
        d = tally.get(name)
        if not d: continue
        tot = sum(d.values())
        parts = "  ".join(f"{k}={d.get(k,0)} ({d.get(k,0)/tot*100:.0f}%)" for k in order if d.get(k))
        print(f"  {name:16} {parts}")


if __name__ == "__main__":
    import urllib3
    try: urllib3.disable_warnings()
    except Exception: pass
    asyncio.run(main())
