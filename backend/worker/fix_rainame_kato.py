"""Чистый и консистентный rainame: район привязываем к KATO-коду района
(code_cato1) через справочник чистых имён из corpus. Убирает и битые казахские
буквы, и рассинхрон (чужие районы под регионом), и ЗАГРАНИЦА.

Запуск (ХОСТ): python backend/worker/fix_rainame_kato.py
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

# чистка перехвата: убрать "в городе", обрезать по street-словам
CLEAN = r"""btrim(regexp_replace(regexp_replace(regexp_replace(raion,
  '\s+(улица|проспект|микрорайон|мкр|переулок|шоссе|бульвар|проезд|тупик|жилой|массив|дом|д\.|село|аул|поселок|станция|квартира).*$','', 'i'),
  '\s*в\s+городе\s+',' ','i'), '\s+',' ','g'))"""


def main() -> None:
    conn = psycopg.connect(DSN, autocommit=False)
    conn.execute("SET work_mem='2GB'")
    conn.execute("SET max_parallel_workers_per_gather=0")

    # 1) справочник code_cato1 -> чистое имя района (по distinct rca из corpus)
    print("справочник KATO-район...", flush=True)
    conn.execute("DROP TABLE IF EXISTS kato_raion")
    conn.execute(f"""
        CREATE UNLOGGED TABLE kato_raion AS
        WITH pairs AS (
          SELECT DISTINCT v.rca, v.code_cato1,
                 btrim((regexp_match(c.full_path_rus, 'район\\s+([^,]+)', 'i'))[1]) AS raion
            FROM (SELECT DISTINCT rca, code_cato1 FROM pop_v3 WHERE code_cato1 <> '') v
            JOIN corpus c ON c.rca = v.rca
           WHERE c.full_path_rus ~* 'район\\s+[^,]+'
        )
        SELECT code_cato1, mode() WITHIN GROUP (ORDER BY {CLEAN}) AS raion
          FROM pairs WHERE raion IS NOT NULL AND btrim(raion) <> ''
         GROUP BY code_cato1
    """)
    conn.commit()
    conn.execute("ALTER TABLE kato_raion ADD PRIMARY KEY (code_cato1)")
    print(f"  кодов района: {conn.execute('SELECT count(*) FROM kato_raion').fetchone()[0]:,}", flush=True)

    # 2) доминирующий code_cato1 на жилище
    print("доминирующий район на жилище...", flush=True)
    conn.execute("DROP TABLE IF EXISTS dwl_cato1")
    conn.execute(f"""
        CREATE UNLOGGED TABLE dwl_cato1 AS
        WITH pv AS (SELECT {BKEY} AS bkey, code_cato1 FROM pop_v3 WHERE code_cato1 <> '')
        SELECT d.dwelling_id, mode() WITHIN GROUP (ORDER BY pv.code_cato1) AS code_cato1
          FROM pv JOIN pop_dwelling d ON d.bkey = pv.bkey
         GROUP BY d.dwelling_id
    """)
    conn.commit()
    conn.execute("ALTER TABLE dwl_cato1 ADD PRIMARY KEY (dwelling_id)")

    # 3) rainame из справочника
    print("пишу rainame...", flush=True)
    cur = conn.execute("""
        UPDATE pop_dwelling d
           SET stats = jsonb_set(d.stats, '{rainame}', to_jsonb('РАЙОН ' || upper(k.raion)))
          FROM dwl_cato1 dc JOIN kato_raion k USING (code_cato1)
         WHERE dc.dwelling_id = d.dwelling_id AND k.raion IS NOT NULL AND d.stats IS NOT NULL
    """)
    conn.commit()
    print(f"  обновлено: {cur.rowcount:,}", flush=True)

    # ЗАГРАНИЦА долой
    for col in ("rainame", "regname"):
        conn.execute(f"UPDATE pop_dwelling SET stats=jsonb_set(stats,'{{{col}}}','null'::jsonb) "
                     f"WHERE stats->>'{col}' ILIKE '%ЗАГРАНИЦА%'")
    conn.commit()

    conn.execute("DROP TABLE kato_raion"); conn.execute("DROP TABLE dwl_cato1")
    conn.commit()

    print("\n--- Астана районы ---", flush=True)
    for (r,) in conn.execute("""
        SELECT DISTINCT stats->>'rainame' FROM pop_dwelling
         WHERE stats->>'regname'='Г.АСТАНА' AND stats->>'rainame' IS NOT NULL ORDER BY 1
    """).fetchall():
        print("  ", r, flush=True)
    conn.close()


if __name__ == "__main__":
    main()
