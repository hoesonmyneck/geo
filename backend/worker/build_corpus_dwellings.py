"""Дедуп corpus (9,2М РКА базиста) на «жилища» для последующего геокода 2GIS.

Отличие от прошлой выгрузки (rka_building_ids.csv): там сельские дома шли
каждый своим building_id. Здесь ДОБАВЛЕН слой село/деревня — все РКА одного
населённого пункта схлопываются в ОДНУ точку (как в карте населения,
rebuild_building.py, уровень V). Город остаётся с точностью до дома.

Ключ дома bkey (приоритет сверху вниз):
  V  село/аул/посёлок/станция/разъезд — по адресу, обрезанному до НП.
     Все дома и квартиры НП = одна точка. Село-детект ВЫШЕ s_building_id:
     даже многоэтажка в селе схлопывается в точку села.
  B  город, есть s_building_id — по нему. Один building_id egov = один дом
     (вместе с его парковками/нежилыми помещениями — у них тот же id).
  A  город, s_building_id нет (частный сектор) — по нормализованному адресу
     без квартиры.

Итог — две таблицы:
  corpus_dwelling      одна строка на уникальное жилище (building_id, kind,
                       geocode_addr, образец адреса, регион). lat/lon пусты —
                       заполнит геокод 2GIS позже.
  corpus_rca_dwelling  карта rca -> building_id (для выгрузки базисту).

Запуск:
    python backend/worker/build_corpus_dwellings.py
"""
from __future__ import annotations

import sys
import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"

# Ключевые слова сельского НП. Требуем запятую перед словом и пробел после,
# чтобы не ловить "улица Станционная". Регистр игнорируем (~*): адреса егова
# бывают и строчные ("село Асан"), и заглавные ("Село Пешковка").
VILLAGE_RE = r',\s*(село|аул|посёлок|поселок|станция|разъезд)\s'
# Жадный ^.* доводит до ПОСЛЕДНЕГО (самого глубокого) НП в цепочке:
# "город Риддер, поселок Ульба" -> обрезаем по "поселок Ульба".
VILLAGE_CUT = r'^(.*,\s*(село|аул|посёлок|поселок|станция|разъезд)\s+[^,]+)'
APT_RE = r',\s*(Квартира|Пәтер)\s+[^,]*$'

STEPS = [
    ("drop", "DROP TABLE IF EXISTS corpus_bkey"),
    # 1) bkey для каждой rca. Материализуем ОДИН раз — регекс по 9,2М дорогой,
    #    гонять его в каждом последующем запросе нельзя.
    ("bkey", f"""
        CREATE UNLOGGED TABLE corpus_bkey AS
        SELECT rca,
               full_path_rus,
               full_path_kaz,
               CASE
                 WHEN full_path_rus ~* '{VILLAGE_RE}'
                   THEN 'V' || lower(btrim((regexp_match(full_path_rus, '{VILLAGE_CUT}', 'i'))[1]))
                 WHEN s_building_id IS NOT NULL AND s_building_id <> ''
                   THEN 'B' || s_building_id
                 ELSE 'A' || lower(regexp_replace(full_path_rus, '{APT_RE}', ''))
               END AS bkey
          FROM corpus
    """),
    ("idx_bkey", "CREATE INDEX ON corpus_bkey (bkey)"),
    ("drop2", "DROP TABLE IF EXISTS corpus_dwelling"),
    # 2) Уникальные жилища. building_id — сквозная нумерация 1..N по bkey.
    #    geocode_addr:
    #      V -> адрес, обрезанный до НП (без улицы/дома) — точка на село.
    #      B/A -> адрес дома без квартиры.
    #    Берём представителя через min(rca) детерминированно.
    ("dwelling", f"""
        CREATE TABLE corpus_dwelling AS
        WITH g AS (
          SELECT bkey,
                 left(bkey, 1) AS tag,
                 (array_agg(full_path_rus ORDER BY rca))[1] AS sample_rus,
                 (array_agg(full_path_kaz ORDER BY rca))[1] AS sample_kaz,
                 count(*) AS rca_count
            FROM corpus_bkey
           GROUP BY bkey
        )
        SELECT row_number() OVER (ORDER BY bkey) AS building_id,
               bkey,
               CASE tag WHEN 'V' THEN 'village'
                        WHEN 'B' THEN 'city_apt'
                        ELSE 'city_house' END AS kind,
               CASE tag
                 WHEN 'V' THEN btrim((regexp_match(sample_rus, '{VILLAGE_CUT}', 'i'))[1])
                 ELSE regexp_replace(sample_rus, '{APT_RE}', '')
               END AS geocode_addr,
               sample_rus AS sample_addr_rus,
               sample_kaz AS sample_addr_kaz,
               rca_count
          FROM g
    """),
    ("pk", "ALTER TABLE corpus_dwelling ADD PRIMARY KEY (building_id)"),
    ("uq", "CREATE UNIQUE INDEX ON corpus_dwelling (bkey)"),
    # координаты заполнит геокод позже
    ("cols", """
        ALTER TABLE corpus_dwelling
          ADD COLUMN lat double precision,
          ADD COLUMN lon double precision,
          ADD COLUMN coord_source varchar(16)
    """),
    ("drop3", "DROP TABLE IF EXISTS corpus_rca_dwelling"),
    # 3) Карта rca -> building_id.
    ("map", """
        CREATE TABLE corpus_rca_dwelling AS
        SELECT b.rca, d.building_id
          FROM corpus_bkey b
          JOIN corpus_dwelling d USING (bkey)
    """),
    ("map_pk", "ALTER TABLE corpus_rca_dwelling ADD PRIMARY KEY (rca)"),
    ("map_idx", "CREATE INDEX ON corpus_rca_dwelling (building_id)"),
]

CHECK = """
SELECT
  (SELECT count(*) FROM corpus)                                    AS corpus_rca,
  (SELECT count(*) FROM corpus_rca_dwelling)                       AS mapped_rca,
  (SELECT count(*) FROM corpus_dwelling)                           AS dwellings,
  (SELECT count(*) FROM corpus_dwelling WHERE kind='village')      AS villages,
  (SELECT count(*) FROM corpus_dwelling WHERE kind='city_apt')     AS city_apt,
  (SELECT count(*) FROM corpus_dwelling WHERE kind='city_house')   AS city_house,
  (SELECT count(*) FROM corpus_dwelling WHERE geocode_addr IS NULL OR btrim(geocode_addr)='') AS empty_geocode
"""


def main() -> None:
    with psycopg.connect(DSN, autocommit=False) as conn:
        conn.execute("SET work_mem = '1GB'")
        conn.execute("SET max_parallel_workers_per_gather = 0")
        n = conn.execute("SELECT count(*) FROM corpus").fetchone()[0]
        print(f"corpus: {n:,} rca")
        if n == 0:
            print("corpus пуст", file=sys.stderr); sys.exit(1)

        for name, sql in STEPS:
            cur = conn.execute(sql)
            tag = f"[{name}]"
            if name in ("bkey", "dwelling", "map"):
                print(f"  {tag:12} {cur.rowcount:,} строк")
            else:
                print(f"  {tag:12} ok")
            conn.commit()

        row = conn.execute(CHECK).fetchone()
        cols = ["corpus_rca", "mapped_rca", "dwellings", "villages",
                "city_apt", "city_house", "empty_geocode"]
        print("\n--- сверка ---")
        for c, v in zip(cols, row):
            print(f"  {c:14} {v:>12,}")

        conn.execute("DROP TABLE IF EXISTS corpus_bkey")
        conn.commit()
        print("\nготово. corpus_dwelling + corpus_rca_dwelling собраны.")


if __name__ == "__main__":
    main()
