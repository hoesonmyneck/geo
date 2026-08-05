"""Привязка людей к домам: staging_person → person.building_id + building.stats.

Ключ строится ТОЧНО так же, как в rebuild_building.py — иначе человек не
найдёт свой дом. Любая правка формулы должна идти в оба файла разом.

Дедуп по sicid: в выгрузке 2,79 млн дублей, но это точные копии строк
(проверено: ни rca, ни адрес, ни возраст, ни флаги не отличаются НИ У ОДНОГО).
Поэтому DISTINCT ON (sicid) безопасен — берём любую.

Люди без адреса (57 257 горожан) попадают в person с building_id = NULL:
дома у них нет и взяться неоткуда, но из статистики их терять незачем.

Запуск:
    python backend/worker/link_persons.py
"""
from __future__ import annotations

import sys
import time

import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"

# Тот же ключ, что в rebuild_building.py. Порядок веток важен:
# село -> многоквартирный по building_id -> прочий адрес текстом.
KEY_SQL = """
CASE
  -- ЗАГРАНИЦА: живут вне Казахстана, точки на карте быть не может.
  -- Ключ NULL -> LEFT JOIN не найдёт дом -> building_id NULL.
  -- В person остаются: в базе есть, на карте нет.
  WHEN name_cato0 = 'ЗАГРАНИЦА' THEN NULL
  WHEN type_ = 'СЕЛО' AND name_cato0 IS NOT NULL AND name_cato1 IS NOT NULL THEN
    'V' || name_cato0 || '|' || name_cato1 || '|' || coalesce(
      nullif(name_cato3, ''),
      CASE WHEN r IS NOT NULL THEN
        CASE lower(r[2])
          WHEN 'аул'     THEN 'А.'
          WHEN 'поселок' THEN 'П.'
          WHEN 'посёлок' THEN 'П.'
          WHEN 'станция' THEN 'СТ.'
          WHEN 'разъезд' THEN 'РЗД.'
          ELSE 'С.'
        END || upper(btrim(r[3]))
      END, '')
  -- bld — свой building_id или подтянутый по адресу из addr_building_map
  -- (см. rebuild_building.py: без этого дом с частично заполненными
  -- building_id разъезжался на две точки, а жильцы делились между ними).
  WHEN type_ <> 'СЕЛО' AND bld IS NOT NULL
       AND clean_address_ru IS NOT NULL AND clean_address_ru <> '' THEN
    'B' || bld
  WHEN type_ <> 'СЕЛО' AND clean_address_ru IS NOT NULL AND clean_address_ru <> '' THEN
    'A' || lower(regexp_replace(clean_address_ru, ',\\s*(Квартира|Пәтер)\\s+[^,]*$', ''))
END
"""

LINK = f"""
INSERT INTO person (sicid, building_id, rca, gender_id, vozrast, trud_vozrast,
                    deti_do18, working, lsi, asp, student, pensioners, ip, kandas,
                    regname, rainame)
SELECT DISTINCT ON (s.sicid)
       s.sicid, b.id, s.rca, s.gender_id, s.vozrast, s.trud_vozrast,
       s.deti_do18, s.working, s.lsi, s.asp, s.student, s.pensioners, s.ip, s.kandas,
       s.kato_regname, s.kato_rainame
  FROM (
    SELECT sp.*,
           regexp_match(sp.clean_address_ru,
             '^(.*,\\s*(село|аул|пос[её]лок|станция|разъезд)\\s+([^,]+))', 'i') AS r,
           coalesce(nullif(sp.building_id,''), am.building_id) AS bld
      FROM staging_person sp
      LEFT JOIN addr_building_map am
        ON am.addr_norm = lower(regexp_replace(sp.clean_address_ru,
                                 ',\\s*(Квартира|Пәтер)\\s+[^,]*$', ''))
  ) s
  LEFT JOIN building b ON b.dedup_key = ({KEY_SQL})
 ORDER BY s.sicid
ON CONFLICT (sicid) DO NOTHING
"""

# Одним проходом по person: сколько кого в каждом доме.
# Флаги в выгрузке — 0/1, поэтому просто суммируем.
STATS = """
UPDATE building b SET stats = x.s
  FROM (
    SELECT building_id,
           jsonb_build_object(
             'total',        count(*),
             'men',          count(*) FILTER (WHERE gender_id = 1),
             'women',        count(*) FILTER (WHERE gender_id = 2),
             'children',     coalesce(sum(deti_do18), 0),
             'trud',         coalesce(sum(trud_vozrast), 0),
             'working',      coalesce(sum(working), 0),
             'lsi',          coalesce(sum(lsi), 0),
             'asp',          coalesce(sum(asp), 0),
             'student',      coalesce(sum(student), 0),
             'pensioners',   coalesce(sum(pensioners), 0),
             'ip',           coalesce(sum(ip), 0),
             'kandas',       coalesce(sum(kandas), 0)
           ) AS s
      FROM person WHERE building_id IS NOT NULL
     GROUP BY building_id
  ) x
 WHERE b.id = x.building_id
"""

CHECK = """
SELECT
  (SELECT count(*) FROM person)                                  AS persons,
  (SELECT count(*) FROM person WHERE building_id IS NULL)        AS without_building,
  (SELECT count(DISTINCT sicid) FROM staging_person)             AS uniq_sicid_in_staging,
  (SELECT count(*) FROM building WHERE stats IS NOT NULL)        AS buildings_with_stats,
  (SELECT count(*) FROM building WHERE stats IS NULL)            AS buildings_empty,
  (SELECT coalesce(sum((stats->>'total')::int),0) FROM building) AS people_in_stats
"""


def main() -> None:
    with psycopg.connect(DSN, autocommit=False) as conn:
        conn.execute("SET work_mem = '1GB'")
        # /dev/shm в контейнере всего 64 МБ — параллельные воркеры падают DiskFull
        conn.execute("SET max_parallel_workers_per_gather = 0")

        n = conn.execute("SELECT count(*) FROM person").fetchone()[0]
        if n:
            print(f"person уже содержит {n:,} строк — очищаю")
            conn.execute("TRUNCATE person")
            conn.commit()

        t0 = time.time()
        print("привязываю людей к домам...")
        cur = conn.execute(LINK)
        conn.commit()
        print(f"  person: {cur.rowcount:,} строк за {(time.time()-t0)/60:.1f} мин")

        t1 = time.time()
        print("считаю building.stats...")
        cur = conn.execute(STATS)
        conn.commit()
        print(f"  обновлено домов: {cur.rowcount:,} за {(time.time()-t1)/60:.1f} мин")

        row = conn.execute(CHECK).fetchone()
        names = ["persons", "without_building", "uniq_sicid_in_staging",
                 "buildings_with_stats", "buildings_empty", "people_in_stats"]
        print("\n--- проверка ---")
        for k, v in zip(names, row):
            print(f"  {k:22s} {v:,}")

        persons, without_b, uniq, _, _, in_stats = row
        if persons != uniq:
            print(f"\n! person ({persons:,}) != уникальных sicid ({uniq:,})", file=sys.stderr)
        if in_stats != persons - without_b:
            print(f"\n! в stats {in_stats:,}, а привязанных людей {persons - without_b:,}",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
