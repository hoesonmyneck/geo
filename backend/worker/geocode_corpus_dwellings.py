"""Боевой геокод corpus_dwelling (адреса БЕЗ прописанных) через 2ГИС.

Отличие от geocode_pop_dwellings.py: очередь берём из corpus_dwelling по
метке geo_cat, а не подряд. Население уже геокодено — те дома помечены
'covered' и в очередь не попадают.

geo_cat (проставляет prep-SQL, см. project_corpus_dwellings в памяти):
  covered  — в доме есть прописанные, координата уже есть в pop_dwelling
  street   — город, обычный уличный адрес   <- ЭТО гоним в первую очередь
  dacha    — ПКСГ/дачи/гаражи/садоводства   <- 2ГИС их почти не находит
  city_apt — город с s_building_id
  village  — село
  other    — прочее

Классификатор точности общий с населением (geocode_pop_test.classify);
city_house/other для него — это город, поэтому kind подменяем на 'city_apt'.

Запуск (ХОСТ): python backend/worker/geocode_corpus_dwellings.py <2GIS_KEY>
               [--cat street] [--concurrency 30] [--chunk 8000] [--limit N]
"""
from __future__ import annotations
import argparse, asyncio, time
from importlib.machinery import SourceFileLoader

import httpx, psycopg

t = SourceFileLoader("t", "backend/worker/geocode_pop_test.py").load_module()
DSN = t.DSN


async def run(key, cats, conc, chunk, limit):
    sync = psycopg.connect(DSN, autocommit=False)
    sync.execute("ALTER TABLE corpus_dwelling ADD COLUMN IF NOT EXISTS precision varchar(16)")
    sync.commit()

    q = ("SELECT building_id, kind, geocode_addr FROM corpus_dwelling "
         "WHERE lat IS NULL AND precision IS NULL "
         "AND geocode_addr IS NOT NULL AND geocode_addr<>'' "
         "AND geo_cat = ANY(%s) ORDER BY building_id")
    if limit:
        q += f" LIMIT {int(limit)}"
    pending = sync.execute(q, (cats,)).fetchall()
    total = len(pending)
    print(f"к геокоду: {total:,}  cat={cats}  (concurrency={conc}, chunk={chunk})", flush=True)
    if not total:
        sync.close(); return

    sem = asyncio.Semaphore(conc)
    tally = {"высокая": 0, "средняя": 0, "низкая": 0, "не_найдено": 0}
    t0 = time.time(); done = 0; stop = {"v": False}

    async with httpx.AsyncClient(headers={"User-Agent": "geo-corpus/1.0"}, verify=False) as client:
        for i in range(0, total, chunk):
            if stop["v"]:
                break
            batch = pending[i:i + chunk]
            results = [None] * len(batch)

            async def work(j, bid, kind, addr):
                async with sem:
                    res = await t.geocode(client, key, addr)
                if res.get("err"):
                    if "403" in str(res["err"]):
                        stop["v"] = True
                    return                      # transient/403 — не отмечаем, добьём при резюме
                if res.get("miss"):
                    results[j] = (None, None, None, "не_найдено", bid)
                else:
                    # для классификатора всё городское = city_apt
                    k = "village" if kind == "village" else "city_apt"
                    lvl = t.classify(k, addr, res)
                    results[j] = (res["lat"], res["lon"], "2gis", lvl, bid)

            await asyncio.gather(*(work(j, *b) for j, b in enumerate(batch)))
            ups = [r for r in results if r]
            if ups:
                with sync.cursor() as cur:
                    cur.executemany(
                        "UPDATE corpus_dwelling SET lat=%s, lon=%s, coord_source=%s, precision=%s "
                        "WHERE building_id=%s", ups)
                sync.commit()
                for r in ups:
                    tally[r[3]] += 1
            done += len(batch)
            rate = done / max(time.time() - t0, 1e-6)
            eta = (total - done) / max(rate, 1e-6)
            print(f"  {done:,}/{total:,}  {rate:.0f}/с  ETA {eta/3600:.1f}ч  | "
                  + "  ".join(f"{k}={v:,}" for k, v in tally.items()), flush=True)
            if stop["v"]:
                print("!!! 403 от 2ГИС (лимит ключа выбран) — стоп. Прогон резюмируемый: "
                      "перезапуск продолжит с необработанных.", flush=True)
                break

    sync.close()
    print(f"\nобработано за проход: {done:,} за {(time.time()-t0)/3600:.2f}ч", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--cat", default="street",
                    help="геокатегории через запятую (street,dacha,city_apt,village,other)")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--chunk", type=int, default=8000)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    import urllib3
    try: urllib3.disable_warnings()
    except Exception: pass
    cats = [c.strip() for c in a.cat.split(",") if c.strip()]
    asyncio.run(run(a.key, cats, a.concurrency, a.chunk, a.limit))


if __name__ == "__main__":
    main()
