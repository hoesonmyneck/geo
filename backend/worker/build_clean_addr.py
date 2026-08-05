"""Точечно чинит geocode_addr в pop_dwelling: заменяет ТОЛЬКО те русские адреса,
где казахские буквы побиты при выгрузке из базы (ә→ј, ө→ґ, ү→ї, ң→ѕ, қ→ќ, ұ→ў
и т.п.). Чистый русский адрес берём из corpus.full_path_rus по rca.

Обычные русские адреса v3 (без битых букв) НЕ трогаем.

Запуск (ХОСТ): python backend/worker/build_clean_addr.py
"""
from __future__ import annotations
import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"
# Класс «мохобайт»-символов, которыми замещены казахские буквы (без легитимного і).
BROKEN = r'[ѕґїјќўҳЅҐЇЈЌЎҲ]'
APT_RU = r',\s*(Квартира|Пәтер)\s+[^,]*$'
VCUT   = r'^(.*,\s*(село|аул|посёлок|поселок|станция|разъезд)\s+[^,]+)'


def main() -> None:
    with psycopg.connect(DSN, autocommit=False) as conn:
        conn.execute("SET work_mem='2GB'")
        # 0 — без параллелизма: /dev/shm контейнера всего 64МБ, параллельный
        # хеш-джойн падает с DiskFull на shared memory.
        conn.execute("SET max_parallel_workers_per_gather=0")

        broken = conn.execute(
            f"SELECT count(*) FROM pop_dwelling WHERE geocode_addr ~ '{BROKEN}'"
        ).fetchone()[0]
        print(f"жилищ с битыми буквами в адресе: {broken:,}")

        # чистый corpus-адрес (представитель-rca) ТОЛЬКО для битых жилищ
        conn.execute("DROP TABLE IF EXISTS dwl_ru")
        cur = conn.execute(f"""
            CREATE UNLOGGED TABLE dwl_ru AS
            SELECT DISTINCT ON (m.dwelling_id) m.dwelling_id, d.kind, c.full_path_rus
              FROM pop_dwelling d
              JOIN pop_rca_dwelling m ON m.dwelling_id = d.dwelling_id
              JOIN corpus c ON c.rca = m.rca
             WHERE d.geocode_addr ~ '{BROKEN}'
               AND c.full_path_rus IS NOT NULL AND c.full_path_rus <> ''
               AND c.full_path_rus !~ '{BROKEN}'          -- в corpus адрес должен быть чистый
             ORDER BY m.dwelling_id, m.rca
        """)
        print(f"нашли чистый адрес в corpus для: {cur.rowcount:,}")
        conn.commit()
        conn.execute("ALTER TABLE dwl_ru ADD PRIMARY KEY (dwelling_id)")

        upd = conn.execute(f"""
            UPDATE pop_dwelling d
               SET geocode_addr = CASE
                     WHEN k.kind='village'
                       THEN coalesce(btrim((regexp_match(k.full_path_rus, '{VCUT}', 'i'))[1]),
                                     regexp_replace(k.full_path_rus, '{APT_RU}', ''))
                     ELSE regexp_replace(k.full_path_rus, '{APT_RU}', '')
                   END
              FROM dwl_ru k
             WHERE k.dwelling_id = d.dwelling_id
        """)
        conn.commit()
        print(f"заменено адресов: {upd.rowcount:,}")

        conn.execute("DROP TABLE IF EXISTS dwl_ru")
        conn.commit()

        left = conn.execute(
            f"SELECT count(*) FROM pop_dwelling WHERE geocode_addr ~ '{BROKEN}'"
        ).fetchone()[0]
        print(f"осталось битых (нет чистого в corpus): {left:,}")
        print("\nпримеры исправленных:")
        for (a,) in conn.execute(
            "SELECT left(geocode_addr,80) FROM pop_dwelling WHERE geocode_addr ~ '[әөүңқұ]' LIMIT 4"
        ).fetchall():
            print("   ", a)


if __name__ == "__main__":
    main()
