"""Сводный реестр координат по РКА — `rca_coord`.

ЗАЧЕМ. Координаты у нас лежат в двух местах и по РАЗНЫМ ключам:
  pop_dwelling    (население, dwelling_id) <- pop_rca_dwelling    (rca -> dwelling_id)
  corpus_dwelling (все РКА,   building_id) <- corpus_rca_dwelling (rca -> building_id)
Ключи домов у них несовместимы (s_building_id egov != building_id выгрузки
населения), поэтому единственный общий ключ — сам РКА.

`rca_coord` схлопывает оба источника в одну плоскую таблицу rca -> координата.
Тогда при СЛЕДУЮЩЕЙ выгрузке базиста (даже если он снова отдаст все 20М человек,
а не только новые адреса) отбор на геокод — один антиджойн:

    SELECT n.rca FROM new_rca n
     LEFT JOIN rca_coord r ON r.rca = n.rca AND r.lat IS NOT NULL
     WHERE r.rca IS NULL;

Приоритет источников (выше = важнее, перетирает нижние):
  1 pop + coord_source='manual'  — руками правленые точки, самые точные
  2 pop + 2gis                   — прогон населения
  3 corpus                       — добивка адресов без прописанных
  4 inherit                      — координата дома, унаследованная от
                                   «братьев» по тому же жилищу (пустые
                                   квартиры в жилом доме)

Запуск (ХОСТ): python backend/worker/build_rca_coord.py [--stats]
"""
from __future__ import annotations
import argparse, time
import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"

DDL = """
DROP TABLE IF EXISTS rca_coord;
CREATE TABLE rca_coord (
    rca          text PRIMARY KEY,
    lat          double precision,
    lon          double precision,
    precision    varchar(16),
    coord_source varchar(16),
    origin       varchar(8),      -- pop | corpus | inherit
    ref_id       bigint,          -- dwelling_id (pop) | building_id (corpus)
    geocode_addr text
);
"""

# Сначала corpus (низший приоритет), потом население поверх — ON CONFLICT
# перетирает. Так один проход, без оконных функций по 20М строк.
STEPS = [
    ("ddl", DDL),
    ("corpus", """
        INSERT INTO rca_coord (rca, lat, lon, precision, coord_source, origin, ref_id, geocode_addr)
        SELECT m.rca, d.lat, d.lon, d.precision, d.coord_source, 'corpus', d.building_id, d.geocode_addr
          FROM corpus_rca_dwelling m
          JOIN corpus_dwelling d ON d.building_id = m.building_id
         WHERE d.lat IS NOT NULL
        ON CONFLICT (rca) DO NOTHING
    """),
    ("pop_2gis", """
        INSERT INTO rca_coord (rca, lat, lon, precision, coord_source, origin, ref_id, geocode_addr)
        SELECT DISTINCT ON (m.rca)
               m.rca, d.lat, d.lon, d.precision, d.coord_source, 'pop', d.dwelling_id, d.geocode_addr
          FROM pop_rca_dwelling m
          JOIN pop_dwelling d ON d.dwelling_id = m.dwelling_id
         WHERE d.lat IS NOT NULL
         ORDER BY m.rca, (d.coord_source = 'manual') DESC, d.dwelling_id
        ON CONFLICT (rca) DO UPDATE SET
            lat=EXCLUDED.lat, lon=EXCLUDED.lon, precision=EXCLUDED.precision,
            coord_source=EXCLUDED.coord_source, origin=EXCLUDED.origin,
            ref_id=EXCLUDED.ref_id, geocode_addr=EXCLUDED.geocode_addr
    """),
    # 3) НАСЛЕДОВАНИЕ. Пустая квартира в жилом доме своей строки в
    #    pop_rca_dwelling не имеет, но координата ДОМА известна — по «братьям»
    #    из того же corpus-жилища. Раздаём её всем остальным РКА дома.
    #    Представителя берём: ручная правка > высокая точность > прочее.
    ("bcoord", """
        DROP TABLE IF EXISTS corpus_bcoord;
        CREATE UNLOGGED TABLE corpus_bcoord AS
        SELECT DISTINCT ON (m.building_id)
               m.building_id, r.lat, r.lon, r.precision, r.coord_source, r.ref_id, r.origin
          FROM corpus_rca_dwelling m
          JOIN rca_coord r ON r.rca = m.rca AND r.lat IS NOT NULL
         ORDER BY m.building_id,
                  (r.coord_source = 'manual') DESC,
                  (r.precision = 'высокая') DESC,
                  (r.precision = 'средняя') DESC
    """),
    ("bcoord_pk", "ALTER TABLE corpus_bcoord ADD PRIMARY KEY (building_id)"),
    ("inherit", """
        INSERT INTO rca_coord (rca, lat, lon, precision, coord_source, origin, ref_id, geocode_addr)
        SELECT m.rca, b.lat, b.lon, b.precision, b.coord_source, 'inherit', b.ref_id, d.geocode_addr
          FROM corpus_rca_dwelling m
          JOIN corpus_bcoord b ON b.building_id = m.building_id
          JOIN corpus_dwelling d ON d.building_id = m.building_id
        ON CONFLICT (rca) DO NOTHING
    """),
    ("idx_src", "CREATE INDEX ON rca_coord (origin, precision)"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="только показать сводку, не пересобирать")
    a = ap.parse_args()

    c = psycopg.connect(DSN, autocommit=True)
    if not a.stats:
        for name, sql in STEPS:
            t0 = time.time()
            n = c.execute(sql).rowcount
            print(f"  [{name}] {n if n and n > 0 else 'ok'}  ({time.time()-t0:.0f}с)", flush=True)

    print("\n--- сводка rca_coord ---", flush=True)
    for row in c.execute("""
        SELECT origin, coalesce(precision,'—') AS precision, count(*)
          FROM rca_coord GROUP BY 1,2 ORDER BY 1, 3 DESC""").fetchall():
        print(f"  {row[0]:<7} {row[1]:<12} {row[2]:,}", flush=True)
    total = c.execute("SELECT count(*) FROM rca_coord").fetchone()[0]
    known = c.execute("SELECT count(*) FROM corpus_rca_dwelling").fetchone()[0]
    print(f"  ИТОГО с координатой: {total:,}  из {known:,} известных РКА corpus", flush=True)
    c.close()


if __name__ == "__main__":
    main()
