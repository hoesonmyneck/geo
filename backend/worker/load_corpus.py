"""Грузит распарсенный корпус РКА (все jsonl) в таблицу corpus для сборки
выгрузки базисту.

Источники и приоритет (меньше src = выше приоритет при дедупе по rca):
    1 rka_fixed.jsonl         — вычищенные битые адреса (есиль-есиль)
    2 rka_egov_missing.jsonl  — свежий догон недостающих
    3 rka_egov_reparse.jsonl
    4 rka_egov_extra.jsonl
    5 rka_egov_output.jsonl    — основной прогон

Грузим только status=ok. Пустые/ошибочные в выгрузке останутся без адреса
(LEFT JOIN на этапе сборки CSV).

Запуск:
    python backend/worker/load_corpus.py
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"
ROOT = Path(__file__).resolve().parents[2]

SOURCES = [
    (1, ROOT / "rka_fixed.jsonl"),
    (2, ROOT / "rka_egov_missing.jsonl"),
    (3, ROOT / "rka_egov_reparse.jsonl"),
    (4, ROOT / "rka_egov_extra.jsonl"),
    (5, ROOT / "rka_egov_output.jsonl"),
]

DDL = """
DROP TABLE IF EXISTS corpus_raw;
CREATE UNLOGGED TABLE corpus_raw (
    rca           text,
    full_path_rus text,
    full_path_kaz text,
    s_building_id text,
    index_        text,
    src           int
);
"""


def rows(path: Path, src: int):
    if not path.exists():
        print(f"  ! нет файла {path.name} — пропускаю")
        return
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") != "ok":
                continue
            yield (r.get("rca"), r.get("full_path_rus"), r.get("full_path_kaz"),
                   r.get("s_building_id"), r.get("index"), src)
            n += 1
    print(f"  {path.name}: {n:,} ok")


def main() -> None:
    with psycopg.connect(DSN, autocommit=False) as conn:
        conn.execute("SET max_parallel_workers_per_gather = 0")
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()

        total = 0
        with conn.cursor().copy(
            "COPY corpus_raw (rca, full_path_rus, full_path_kaz, s_building_id, index_, src) FROM STDIN"
        ) as cp:
            for src, path in SOURCES:
                print(f"грузим src={src} {path.name} ...")
                for row in rows(path, src):
                    cp.write_row(row)
                    total += 1
        conn.commit()
        print(f"\nвсего загружено строк ok: {total:,}")

        # Схлопываем до одной строки на rca — по наименьшему src (высший приоритет).
        print("дедуп по rca (приоритет по src) ...")
        conn.execute("DROP TABLE IF EXISTS corpus")
        conn.execute("""
            CREATE TABLE corpus AS
            SELECT DISTINCT ON (rca) rca, full_path_rus, full_path_kaz,
                   s_building_id, index_, src
              FROM corpus_raw
             ORDER BY rca, src
        """)
        conn.execute("ALTER TABLE corpus ADD PRIMARY KEY (rca)")
        conn.commit()

        n = conn.execute("SELECT count(*) FROM corpus").fetchone()[0]
        print(f"уникальных rca в corpus: {n:,}")

        print("\n--- состав по index / building_id ---")
        for row in conn.execute("""
            SELECT index_,
                   count(*) AS всего,
                   count(*) FILTER (WHERE s_building_id IS NOT NULL AND s_building_id<>'') AS есть_bid
              FROM corpus GROUP BY index_ ORDER BY 2 DESC
        """).fetchall():
            print(f"  {str(row[0]):16} всего={row[1]:>10,}  есть_bid={row[2]:>10,}")

        conn.execute("DROP TABLE corpus_raw")
        conn.commit()


if __name__ == "__main__":
    main()
