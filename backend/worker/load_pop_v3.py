"""Потоковая заливка полного населения v3 (rka_building_ids_v3.csv, ~28ГБ, ~20М)
в таблицу pop_v3. Новый ПОЛНЫЙ источник, заменяет staging_person/corpus как
основу для геокода населения.

- Читаем построчно (csv, ';'), берём только нужные колонки ПО ИМЕНАМ из шапки.
- Всё грузим как text (робастно к грязному CSV); типы приводим на дедупе.
- COPY через psycopg — быстрый однопроходный залив.

Гео-колонки для дедупа: rca(A1), building_id(A4), type_(ГОРОД/СЕЛО),
clean_address_ru, code_cato0..4, name_cato0/1/3.
Демография/флаги — чтобы не перечитывать 28ГБ ради статистики позже.

Запуск (на ХОСТЕ, порт 5432 проброшен):
    python backend/worker/load_pop_v3.py "C:/.../rka_building_ids_v3.csv"
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"

# имя колонки в шапке файла -> имя колонки в таблице pop_v3
WANT = {
    "A1": "rca",
    "A4": "building_id",
    "TYPE_": "type_",
    "CLEAN_ADDRESS_RU": "clean_address_ru",
    "CODE_CATO0": "code_cato0",
    "CODE_CATO1": "code_cato1",
    "CODE_CATO2": "code_cato2",
    "CODE_CATO3": "code_cato3",
    "CODE_CATO4": "code_cato4",
    "NAME_CATO0": "name_cato0",
    "NAME_CATO1": "name_cato1",
    "NAME_CATO3": "name_cato3",
    "GENDER_ID": "gender_id",
    "VOZRAST": "vozrast",
    "NATIONALTY_ID": "nationalty_id",
    "CITIZENSHIP_ID": "citizenship_id",
    "PERSON_STATUS_ID": "person_status_id",
    "STATUS": "status",
    "TRUD_VOZRAST": "trud_vozrast",
    "DETI_DO18": "deti_do18",
    "WORKING": "working",
    "LSI": "lsi",
    "ASP": "asp",
    "RT_UNEMPLOYED": "rt_unemployed",
    "STUDENT": "student",
    "PENSIONERS": "pensioners",
    "IP": "ip",
    "KANDAS": "kandas",
    "BEREM": "berem",
    "UHOD_INV": "uhod_inv",
    "FOREIGNERS": "foreigners",
    "MNOGODETNYI": "mnogodetnyi",
    "WOMAN_UHOD_DO3": "woman_uhod_do3",
    "CBD": "cbd",
}


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("rka_building_ids_v3.csv")
    if not src.exists():
        print(f"нет файла: {src}", file=sys.stderr)
        sys.exit(1)

    csv.field_size_limit(1 << 24)
    cols = list(WANT.values())

    conn = psycopg.connect(DSN, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS pop_v3")
    conn.execute("CREATE UNLOGGED TABLE pop_v3 (" + ", ".join(f"{c} text" for c in cols) + ")")

    t0 = time.time()
    n = 0
    with src.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";", quotechar='"')
        header = next(reader)
        idx = {h: i for i, h in enumerate(header)}
        missing = [k for k in WANT if k not in idx]
        if missing:
            print(f"нет колонок в шапке: {missing}", file=sys.stderr)
            sys.exit(1)
        pick = [idx[k] for k in WANT]           # порядок как в cols
        m = len(header)

        copy_sql = "COPY pop_v3 (" + ", ".join(cols) + ") FROM STDIN"
        with conn.cursor().copy(copy_sql) as cp:
            for row in reader:
                if len(row) < m:                 # битая/короткая строка — пропускаем
                    continue
                cp.write_row(tuple(row[i] or None for i in pick))
                n += 1
                if n % 1_000_000 == 0:
                    rate = n / max(time.time() - t0, 1e-6)
                    print(f"  {n:,} строк  {rate:,.0f}/с", flush=True)

    conn.execute("ANALYZE pop_v3")
    print(f"\nготово: {n:,} строк за {(time.time()-t0)/60:.1f} мин")
    conn.close()


if __name__ == "__main__":
    main()
