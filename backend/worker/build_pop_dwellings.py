"""Дедуп населения v3 (pop_v3, ~20М) на «жилища» для геокода 2ГИС.

Отличие от corpus-версии: город/село берём из готовой метки TYPE_ (ground-truth
из KARTA), а село схлопываем по KATO-коду НП, а не по тексту адреса.

Ключ жилища dwelling_key (приоритет сверху вниз):
  V  СЕЛО — по KATO НП: code_cato3 → code_cato2 → адрес-до-НП → code_cato1.
     Все дома/квартиры села = одна точка.
  B  ГОРОД + есть building_id — по нему (id = дом вместе с парковками/нежилыми).
  A  ГОРОД без building_id — по нормализованному адресу без квартиры.

Итог: pop_dwelling (уникальные жилища) + pop_rca_dwelling (rca -> dwelling_id).
geocode_addr пока из v3 (адрес v3; казахские улицы бывают побиты — уточним из
corpus на этапе геокода). lat/lon пусты — заполнит геокод.

Запуск (на ХОСТЕ): python backend/worker/build_pop_dwellings.py
"""
from __future__ import annotations

import sys
import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"

APT_RE = r',(Квартира|Пәтер|Пјтер)[^,]*$'
VCUT = r'^(.*,\s*(село|аул|посёлок|поселок|станция|разъезд)\s+[^,]+)'

STEPS = [
    ("drop", "DROP TABLE IF EXISTS pop_bkey"),
    ("bkey", f"""
        CREATE UNLOGGED TABLE pop_bkey AS
        SELECT rca,
               clean_address_ru,
               CASE
                 -- Город: TYPE_='ГОРОД' ЛИБО в адресе есть "город N" (часть
                 -- городских адресов помечена в источнике как СЕЛО — тогда
                 -- ориентируемся на адрес, иначе целый город схлопнулся бы в
                 -- одну точку). Дом-уровень: по building_id, иначе по адресу.
                 WHEN type_='ГОРОД' OR clean_address_ru ~* '(^|,)\\s*город\\s' THEN
                   CASE WHEN building_id IS NOT NULL AND building_id NOT IN ('','0')
                        THEN 'B' || building_id
                        ELSE 'A' || lower(regexp_replace(coalesce(clean_address_ru,''), '{APT_RE}', '')) END
                 -- Настоящее село: схлопываем по KATO НП.
                 ELSE 'V' || coalesce(
                        nullif(code_cato3,''),
                        nullif(code_cato2,''),
                        nullif(lower(btrim((regexp_match(clean_address_ru, '{VCUT}', 'i'))[1])),''),
                        nullif(code_cato1,''),
                        '?')
               END AS bkey
          FROM pop_v3
    """),
    ("idx", "CREATE INDEX ON pop_bkey (bkey)"),
    ("drop2", "DROP TABLE IF EXISTS pop_dwelling"),
    ("dwelling", f"""
        CREATE TABLE pop_dwelling AS
        WITH g AS (
          SELECT bkey, left(bkey,1) AS tag,
                 (array_agg(clean_address_ru ORDER BY rca))[1] AS sample_ru,
                 count(*) AS rca_count
            FROM pop_bkey GROUP BY bkey
        )
        SELECT row_number() OVER (ORDER BY bkey) AS dwelling_id,
               bkey,
               CASE tag WHEN 'V' THEN 'village' WHEN 'B' THEN 'city_apt' ELSE 'city_house' END AS kind,
               CASE tag
                 WHEN 'V' THEN coalesce(btrim((regexp_match(sample_ru, '{VCUT}', 'i'))[1]), sample_ru)
                 ELSE regexp_replace(coalesce(sample_ru,''), '{APT_RE}', '')
               END AS geocode_addr,
               sample_ru AS sample_addr_ru,
               rca_count
          FROM g
    """),
    ("pk", "ALTER TABLE pop_dwelling ADD PRIMARY KEY (dwelling_id)"),
    ("uq", "CREATE UNIQUE INDEX ON pop_dwelling (bkey)"),
    ("cols", "ALTER TABLE pop_dwelling ADD COLUMN lat double precision, ADD COLUMN lon double precision, ADD COLUMN coord_source varchar(16)"),
    ("drop3", "DROP TABLE IF EXISTS pop_rca_dwelling"),
    ("map", """
        CREATE TABLE pop_rca_dwelling AS
        SELECT b.rca, d.dwelling_id
          FROM pop_bkey b JOIN pop_dwelling d USING (bkey)
    """),
    # rca в населении НЕ уникален (строка = человек), поэтому индексы, не PK
    ("map_idx1", "CREATE INDEX ON pop_rca_dwelling (rca)"),
    ("map_idx2", "CREATE INDEX ON pop_rca_dwelling (dwelling_id)"),
]

CHECK = """
SELECT
  (SELECT count(*) FROM pop_v3)                                     AS pop_rows,
  (SELECT count(*) FROM pop_rca_dwelling)                           AS mapped,
  (SELECT count(*) FROM pop_dwelling)                               AS dwellings,
  (SELECT count(*) FROM pop_dwelling WHERE kind='village')          AS villages,
  (SELECT count(*) FROM pop_dwelling WHERE kind='city_apt')         AS city_apt,
  (SELECT count(*) FROM pop_dwelling WHERE kind='city_house')       AS city_house,
  (SELECT count(*) FROM pop_dwelling WHERE geocode_addr IS NULL OR btrim(geocode_addr)='') AS empty_geocode
"""


def main() -> None:
    with psycopg.connect(DSN, autocommit=False) as conn:
        conn.execute("SET work_mem='1GB'")
        conn.execute("SET max_parallel_workers_per_gather=4")
        n = conn.execute("SELECT count(*) FROM pop_v3").fetchone()[0]
        print(f"pop_v3: {n:,} строк")
        if n == 0:
            print("pop_v3 пуст", file=sys.stderr); sys.exit(1)
        for name, sql in STEPS:
            cur = conn.execute(sql)
            if name in ("bkey", "dwelling", "map"):
                print(f"  [{name}] {cur.rowcount:,}")
            else:
                print(f"  [{name}] ok")
            conn.commit()
        row = conn.execute(CHECK).fetchone()
        cols = ["pop_rows","mapped","dwellings","villages","city_apt","city_house","empty_geocode"]
        print("\n--- сверка ---")
        for c, v in zip(cols, row):
            print(f"  {c:12} {v:>12,}")
        conn.execute("DROP TABLE IF EXISTS pop_bkey")
        conn.commit()
        print("\nготово: pop_dwelling + pop_rca_dwelling")


if __name__ == "__main__":
    main()
