"""Загружает границы областей (L4) и районов (L68) в PostGIS и предпосчитывает
демографию внутри каждого полигона (ST_Contains по pop_dwelling). Для попапа
статистики при клике по границе — без сопоставления имён, чисто пространственно.

Ключи: область — id_reg, район — id_rai (из geojson).

Запуск (ХОСТ): python backend/worker/build_area_stats.py <l4.geojson> <l68.geojson>
"""
from __future__ import annotations
import json, os, sys
import psycopg

# DSN из окружения (чтобы гонять и с хоста, и внутри backend-контейнера, где
# postgres доступен по хосту 'postgres'). Фолбэк — локальный проброс.
DSN = (
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')} "
    f"dbname={os.getenv('POSTGRES_DB', 'geo')} "
    f"user={os.getenv('POSTGRES_USER', 'geo')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'geopassword123')}"
)
FLAGS = ["total", "male", "female", "lsi", "asp", "deti_do18", "trud_vozrast",
         "working", "student", "pensioners", "ip", "kandas",
         "mnogodetnyi", "woman_uhod_do3", "rt_unemployed",
         "foreigners", "uhod_inv", "cbd", "berem"]


def load(conn, path, level):
    data = json.load(open(path, encoding="utf-8"))
    n = 0
    with conn.cursor() as cur:
        for f in data.get("features", []):
            p = f.get("properties") or {}
            cur.execute(
                "INSERT INTO map_district(level,id_reg,id_rai,name,geom) "
                "VALUES (%s,%s,%s,%s, ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))",
                (level, p.get("id_reg"), p.get("id_rai"), p.get("name"),
                 json.dumps(f["geometry"])))
            n += 1
    print(f"  {level}: {n} границ", flush=True)


def main():
    l4, l68 = sys.argv[1], sys.argv[2]
    conn = psycopg.connect(DSN, autocommit=False)
    conn.execute("SET max_parallel_workers_per_gather=0")
    conn.execute("DROP TABLE IF EXISTS map_district")
    conn.execute("""CREATE TABLE map_district(
        id serial primary key, level text, id_reg int, id_rai int,
        name text, geom geometry(Geometry,4326))""")
    load(conn, l4, "oblast")
    load(conn, l68, "raion")
    conn.execute("CREATE INDEX ON map_district USING gist(geom)")
    conn.execute("ANALYZE map_district")
    conn.commit()

    print("пространственная агрегация (ST_Contains)...", flush=True)
    sums = ",\n".join(f"'{k}', sum((p.stats->>'{k}')::int)" for k in FLAGS)
    conn.execute("DROP TABLE IF EXISTS area_stats")
    conn.execute(f"""
        CREATE TABLE area_stats AS
        SELECT d.level, d.id_reg, d.id_rai, d.name,
               count(*) AS n,
               jsonb_build_object({sums}) AS stats
          FROM map_district d
          JOIN pop_dwelling p ON p.geom && d.geom AND ST_Contains(d.geom, p.geom)
         WHERE p.stats IS NOT NULL
         GROUP BY d.level, d.id_reg, d.id_rai, d.name
    """)
    conn.execute("CREATE INDEX ON area_stats(id_rai)")
    conn.execute("CREATE INDEX ON area_stats(id_reg) WHERE level='oblast'")
    conn.commit()
    n = conn.execute("SELECT count(*) FROM area_stats").fetchone()[0]
    print(f"  агрегатов: {n}", flush=True)
    print("\n--- пример (Астана районы) ---", flush=True)
    for r in conn.execute("""
        SELECT name, n, stats->>'total' FROM area_stats
         WHERE level='raion' AND id_reg=71 ORDER BY (stats->>'total')::int DESC
    """).fetchall():
        print(f"  {r[0]}: {r[2]} чел., {r[1]} точек", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
