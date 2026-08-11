"""Агрегирует демографию pop_v3 по жилищам → pop_dwelling.stats (JSONB).

Ключи stats (что ждёт фронт): total, male, female, trud_vozrast, deti_do18,
working, lsi, asp, student, pensioners, ip, kandas, regname, rainame.

bkey пересчитываем над pop_v3 той же логикой, что при дедупе, и джойним к
pop_dwelling по bkey (без взрыва: pop_v3 20М → group → 1.13М жилищ).

Запуск (ХОСТ): python backend/worker/build_pop_stats.py
"""
from __future__ import annotations
import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"
APT = r',(Квартира|Пәтер|Пјтер)[^,]*$'
VCUT = r'^(.*,\s*(село|аул|посёлок|поселок|станция|разъезд)\s+[^,]+)'
BKEY = f"""CASE
  WHEN type_='ГОРОД' OR clean_address_ru ~* '(^|,)\\s*город\\s' THEN
    CASE WHEN building_id IS NOT NULL AND building_id NOT IN ('','0') THEN 'B' || building_id
         ELSE 'A' || lower(regexp_replace(coalesce(clean_address_ru,''), '{APT}', '')) END
  ELSE 'V' || coalesce(nullif(code_cato3,''), nullif(code_cato2,''),
        nullif(lower(btrim((regexp_match(clean_address_ru, '{VCUT}', 'i'))[1])),''),
        nullif(code_cato1,''), '?')
END"""

FLAGS = ["trud_vozrast", "deti_do18", "working", "lsi", "asp",
         "student", "pensioners", "ip", "kandas",
         # добавленные статусы (были в выгрузке, но не клали на карту)
         "mnogodetnyi", "woman_uhod_do3", "rt_unemployed",
         "foreigners", "uhod_inv", "cbd", "berem"]


def main() -> None:
    conn = psycopg.connect(DSN, autocommit=False)
    conn.execute("SET work_mem='2GB'")
    conn.execute("SET max_parallel_workers_per_gather=0")

    conn.execute("DROP TABLE IF EXISTS pop_stats")
    flag_sums = ",\n".join(f"sum((pv.{f}='1')::int) AS {f}" for f in FLAGS)
    print("агрегирую pop_v3 по жилищам...", flush=True)
    conn.execute(f"""
        CREATE UNLOGGED TABLE pop_stats AS
        WITH pv AS (
          SELECT {BKEY} AS bkey, gender_id, name_cato0, name_cato1,
                 {', '.join(FLAGS)}
            FROM pop_v3
        )
        SELECT d.dwelling_id,
               count(*)                                     AS total,
               count(*) FILTER (WHERE pv.gender_id='1')     AS male,
               count(*) FILTER (WHERE pv.gender_id='2')     AS female,
               {flag_sums},
               mode() WITHIN GROUP (ORDER BY pv.name_cato0) AS regname,
               mode() WITHIN GROUP (ORDER BY pv.name_cato1) AS rainame
          FROM pv JOIN pop_dwelling d ON d.bkey = pv.bkey
         GROUP BY d.dwelling_id
    """)
    conn.commit()
    n = conn.execute("SELECT count(*) FROM pop_stats").fetchone()[0]
    print(f"  жилищ со статистикой: {n:,}", flush=True)

    conn.execute("ALTER TABLE pop_dwelling ADD COLUMN IF NOT EXISTS stats jsonb")
    conn.execute("ALTER TABLE pop_stats ADD PRIMARY KEY (dwelling_id)")
    print("пишу stats в pop_dwelling...", flush=True)
    flag_obj = ",\n".join(f"'{f}', s.{f}" for f in FLAGS)
    cur = conn.execute(f"""
        UPDATE pop_dwelling d SET stats = jsonb_build_object(
            'total', s.total, 'male', s.male, 'female', s.female,
            {flag_obj},
            'regname', s.regname, 'rainame', s.rainame)
          FROM pop_stats s WHERE s.dwelling_id = d.dwelling_id
    """)
    conn.commit()
    print(f"  обновлено жилищ: {cur.rowcount:,}", flush=True)

    conn.execute("DROP TABLE pop_stats")
    conn.commit()

    print("\n--- пример stats ---", flush=True)
    for kind in ("city_apt", "village"):
        r = conn.execute(
            "SELECT geocode_addr, stats FROM pop_dwelling WHERE kind=%s AND stats IS NOT NULL "
            "ORDER BY (stats->>'total')::int DESC LIMIT 1", (kind,)).fetchone()
        print(f"  {kind}: {str(r[0])[:50]}\n     {r[1]}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
