"""
Генерирует /srv/data/places.json.gz — статический снимок всех домов с агрегированной статистикой.
Caddy отдаёт его напрямую браузеру без участия Python/FastAPI.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import orjson
import psycopg

sys.path.insert(0, "/app")
from app.core.config import settings

logger = logging.getLogger("gen_static")

OUTPUT_DIR = Path(os.environ.get("STATIC_DATA_DIR", "/geo-data"))
OUTPUT_FILE = OUTPUT_DIR / "places.json"
OUTPUT_GZ   = OUTPUT_DIR / "places.json.gz"


def _dsn() -> str:
    s = settings
    return (
        f"host={s.POSTGRES_HOST} port={s.POSTGRES_PORT} "
        f"dbname={s.POSTGRES_DB} user={s.POSTGRES_USER} password={s.POSTGRES_PASSWORD}"
    )


def generate() -> int:
    """
    Читает все дома с stats, сериализует через orjson, сжимает gzip.
    Пишет атомарно через tmp-файл чтобы браузер никогда не получил неполный файл.
    Возвращает количество записанных домов.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, city, street_name, house,
                       lat, lon, confidence,
                       stats::text AS stats_raw
                FROM place
                WHERE lat IS NOT NULL
                  AND stats IS NOT NULL
                  AND (stats->>'total')::int > 0
                ORDER BY id
            """)
            rows = cur.fetchall()

    # Собираем JSON: stats_raw вставляем без парсинга (экономим CPU)
    parts: list[bytes] = []
    for row in rows:
        id_, city, street, house, lat, lon, conf, stats_raw = row
        parts.append(
            b'{"id":' + orjson.dumps(id_) +
            b',"city":' + orjson.dumps(city) +
            b',"street_name":' + orjson.dumps(street) +
            b',"house":' + orjson.dumps(house) +
            b',"lat":' + orjson.dumps(lat) +
            b',"lon":' + orjson.dumps(lon) +
            b',"confidence":' + orjson.dumps(conf) +
            b',"stats":' + (stats_raw or "null").encode() +
            b'}'
        )

    body = b'[' + b','.join(parts) + b']'

    import gzip as gzip_mod

    # Атомарная запись JSON (без сжатия — для совместимости)
    tmp_plain = OUTPUT_FILE.with_suffix(".tmp")
    tmp_plain.write_bytes(body)
    tmp_plain.replace(OUTPUT_FILE)

    # Предсжатый .gz — Caddy отдаёт его напрямую без runtime сжатия
    tmp_gz = OUTPUT_GZ.with_suffix(".tmp.gz")
    with gzip_mod.open(tmp_gz, "wb", compresslevel=6) as f:
        f.write(body)
    tmp_gz.replace(OUTPUT_GZ)

    count = len(parts)
    logger.info(
        "gen_static: wrote %d places → %s (%.1f MB plain, %.1f MB gzip)",
        count, OUTPUT_DIR,
        OUTPUT_FILE.stat().st_size / 1_048_576,
        OUTPUT_GZ.stat().st_size / 1_048_576,
    )
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate()
