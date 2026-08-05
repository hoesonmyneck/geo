"""
Заливка выгрузки KARTA (41 xlsx, ~20M человек) в staging_person через COPY.

Порядок колонок в xlsx точно совпадает со staging_person (30 колонок),
поэтому заливка прямая. Резюмируемо: трек залитых файлов в load_progress.
Каждый файл — одна COPY-транзакция (атомарно; прерывание → откат, рестарт зальёт заново).

Запуск (локально, порт 5432 проброшен):
    python backend/worker/load_karta.py
"""
from __future__ import annotations
import sys, time
from pathlib import Path

import openpyxl
import psycopg

ROOT = Path(__file__).resolve().parents[2]
KARTA_DIR = ROOT / "KARTA"

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"

# 30 колонок staging_person в порядке xlsx
COLS = [
    "code_reg", "sicid", "kato_reg", "kato_regname", "kato_rai", "kato_rainame",
    "reg_address_street", "reg_address_building", "reg_address_corpus",
    "gender_id", "vozrast", "trud_vozrast", "deti_do18", "working", "lsi", "asp",
    "student", "pensioners", "ip", "kandas",
    "name_cato0", "name_cato1", "name_cato2", "name_cato3", "type_",
    "rca", "clean_address_ru", "clean_address_kz", "building_id", "ids",
]
NCOL = len(COLS)


def ensure_progress(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS load_progress (
            filename   text PRIMARY KEY,
            rows       bigint,
            loaded_at  timestamptz DEFAULT now()
        )
    """)
    conn.commit()


def done_files(conn) -> set[str]:
    cur = conn.execute("SELECT filename FROM load_progress")
    return {r[0] for r in cur.fetchall()}


def load_file(conn, path: Path) -> int:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    n = 0
    copy_sql = f"COPY staging_person ({', '.join(COLS)}) FROM STDIN"
    with conn.cursor() as cur:
        with cur.copy(copy_sql) as cp:
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if r == 0:
                    continue  # заголовок
                # ровно NCOL значений (добить None если строка короче)
                vals = list(row[:NCOL])
                if len(vals) < NCOL:
                    vals += [None] * (NCOL - len(vals))
                # пустые строки-заглушки → None
                if vals[1] is None and vals[0] is None:
                    continue
                cp.write_row(vals)
                n += 1
    wb.close()
    conn.execute(
        "INSERT INTO load_progress (filename, rows) VALUES (%s, %s)",
        (path.name, n),
    )
    conn.commit()
    return n


def main():
    files = sorted(KARTA_DIR.glob("*.xlsx"))
    if not files:
        print(f"нет xlsx в {KARTA_DIR}", file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(DSN) as conn:
        ensure_progress(conn)
        done = done_files(conn)
        todo = [f for f in files if f.name not in done]
        print(f"Всего файлов: {len(files)} | уже залито: {len(done)} | к заливке: {len(todo)}")

        t0 = time.time()
        total = 0
        for i, f in enumerate(todo, 1):
            ft = time.time()
            n = load_file(conn, f)
            total += n
            dt = time.time() - ft
            elapsed = time.time() - t0
            print(f"[{i}/{len(todo)}] {f.name}: {n:,} строк за {dt:.0f}с "
                  f"| всего {total:,} | прошло {elapsed/60:.0f} мин")

        # итоговое число строк в staging
        cur = conn.execute("SELECT count(*) FROM staging_person")
        print(f"\nГотово. staging_person всего: {cur.fetchone()[0]:,}")


if __name__ == "__main__":
    main()
