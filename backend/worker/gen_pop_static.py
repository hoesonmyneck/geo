"""Генерит places.json (+ .gz) из pop_dwelling для карты населения.

Формат под фронт: [{id, city, street_name, house, lat, lon, confidence, stats}].
confidence ← precision (высокая→high, средняя→medium, низкая→low, не_найдено→miss).
Пишет в локальные файлы; их потом docker cp в volume geo_static (/geo-data),
откуда Caddy отдаёт /data/places.json.

Запуск (ХОСТ): python backend/worker/gen_pop_static.py [out_dir]
"""
from __future__ import annotations
import gzip, re, sys
from pathlib import Path
import orjson, psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"
CONF = {"высокая": "high", "средняя": "medium", "низкая": "low", "не_найдено": "miss"}
NP = re.compile(r'(?:город(?:\s+\S+\s+значения)?|село|аул|пос[её]лок|станция|разъезд|кент)\s+([^,]+)', re.I)
PREFIX = re.compile(r'^(?:республика казахстан|қазақстан республикасы|область[ьи]?\s+[^,]+|ОБЛАСТЬ\s+[^,]+)\s*,\s*', re.I)


def parse_addr(addr: str):
    a = addr or ""
    m = NP.search(a)
    city = m.group(1).strip() if m else ""
    street = PREFIX.sub("", a).strip()
    return city, street


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./_static_out")
    out.mkdir(parents=True, exist_ok=True)

    conn = psycopg.connect(DSN)
    cur = conn.cursor(name="gen")
    cur.itersize = 50000
    cur.execute("""
        SELECT dwelling_id, geocode_addr, lat, lon, precision, stats::text
          FROM pop_dwelling
         WHERE lat IS NOT NULL AND stats IS NOT NULL AND (stats->>'total')::int > 0
         ORDER BY dwelling_id
    """)
    parts: list[bytes] = []
    for did, addr, lat, lon, prec, stats_raw in cur:
        city, street = parse_addr(addr)
        parts.append(
            b'{"id":' + orjson.dumps(did) +
            b',"city":' + orjson.dumps(city) +
            b',"street_name":' + orjson.dumps(street) +
            b',"house":""' +
            b',"lat":' + orjson.dumps(lat) +
            b',"lon":' + orjson.dumps(lon) +
            b',"confidence":' + orjson.dumps(CONF.get(prec, "miss")) +
            b',"stats":' + (stats_raw or "null").encode() +
            b'}'
        )
    cur.close(); conn.close()

    body = b'[' + b','.join(parts) + b']'
    (out / "places.json").write_bytes(body)
    with gzip.open(out / "places.json.gz", "wb", compresslevel=6) as f:
        f.write(body)
    mb = len(body) / 1_048_576
    gz = (out / "places.json.gz").stat().st_size / 1_048_576
    print(f"записано точек: {len(parts):,}  →  {out}/places.json  ({mb:.0f} МБ, gz {gz:.0f} МБ)")


if __name__ == "__main__":
    main()
