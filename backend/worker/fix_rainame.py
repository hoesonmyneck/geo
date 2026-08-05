"""Чинит rainame в pop_dwelling.stats: берёт чистое название района из corpus
(там казахские буквы целые) вместо битого name_cato1 ('РАЙОН БАЙ?О?ЫР').
Также убирает псевдо-район ЗАГРАНИЦА.

Запуск (ХОСТ): python backend/worker/fix_rainame.py
"""
from __future__ import annotations
import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"


def main() -> None:
    conn = psycopg.connect(DSN, autocommit=False)
    conn.execute("SET work_mem='2GB'")
    conn.execute("SET max_parallel_workers_per_gather=0")

    conn.execute("DROP TABLE IF EXISTS dwl_rai")
    print("тяну чистый адрес из corpus по представителю-rca...", flush=True)
    conn.execute("""
        CREATE UNLOGGED TABLE dwl_rai AS
        SELECT DISTINCT ON (m.dwelling_id) m.dwelling_id,
               btrim((regexp_match(c.full_path_rus, 'район\\s+([^,]+)', 'i'))[1]) AS raion
          FROM pop_rca_dwelling m
          JOIN corpus c ON c.rca = m.rca
         WHERE c.full_path_rus ~* 'район\\s+[^,]+'
         ORDER BY m.dwelling_id, m.rca
    """)
    conn.commit()
    conn.execute("ALTER TABLE dwl_rai ADD PRIMARY KEY (dwelling_id)")
    n = conn.execute("SELECT count(*) FROM dwl_rai WHERE raion IS NOT NULL").fetchone()[0]
    print(f"  районов из corpus: {n:,}", flush=True)

    print("пишу чистый rainame в stats...", flush=True)
    cur = conn.execute("""
        UPDATE pop_dwelling d
           SET stats = jsonb_set(d.stats, '{rainame}',
                                 to_jsonb('РАЙОН ' || upper(k.raion)))
          FROM dwl_rai k
         WHERE k.dwelling_id = d.dwelling_id AND k.raion IS NOT NULL
           AND d.stats IS NOT NULL
    """)
    conn.commit()
    print(f"  обновлено: {cur.rowcount:,}", flush=True)

    # ЗАГРАНИЦА — не район: убираем из rainame и regname
    for col in ("rainame", "regname"):
        c2 = conn.execute(f"""
            UPDATE pop_dwelling SET stats = jsonb_set(stats, '{{{col}}}', 'null'::jsonb)
             WHERE stats->>'{col}' ILIKE '%ЗАГРАНИЦА%'
        """)
        print(f"  ЗАГРАНИЦА убрана из {col}: {c2.rowcount:,}", flush=True)
    conn.commit()

    conn.execute("DROP TABLE dwl_rai")
    conn.commit()

    print("\n--- примеры rainame (Астана) ---", flush=True)
    for (r,) in conn.execute("""
        SELECT DISTINCT stats->>'rainame' FROM pop_dwelling
         WHERE stats->>'regname'='Г.АСТАНА' AND stats->>'rainame' IS NOT NULL LIMIT 12
    """).fetchall():
        print("  ", r, flush=True)
    conn.close()


if __name__ == "__main__":
    main()
