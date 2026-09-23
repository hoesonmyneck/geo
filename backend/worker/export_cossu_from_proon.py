"""
Выгружает ЦОССУ напрямую из корпоративной базы proon в xlsx.

Заменяет ручную работу: раньше базист выполнял запрос в DBeaver, экспортировал
результат в Excel и присылал файл. Колонки здесь идут ровно в том порядке,
который ждёт worker/seed_cossu.py, поэтому дальше ничего менять не нужно —
получившийся файл скармливается тому же пайплайну:

    seed_cossu.py <xlsx>  →  cossu_remove_stale.py <xlsx>  →  geocode_cossu_2gis.py

Запуск (с ноутбука, при поднятом VPN до корпоративной сети):

    python backend/worker/export_cossu_from_proon.py
    python backend/worker/export_cossu_from_proon.py --out data/output/cossu.xlsx

Параметры подключения берутся из окружения или из файла proon.env в корне
репозитория (он в .gitignore, в коммит не уедет):

    PROON_HOST=...
    PROON_PORT=5432
    PROON_DB=proon
    PROON_SCHEMA=social_proon
    PROON_USER=...
    PROON_PASSWORD=...

Важно: сеть с базой доступна только изнутри корпоративной сети. С ноутбука —
при поднятом VPN, с сервера — из контейнера cossu-exporter, который поднимает
туннель сам (файрвол сервера порт 5432 наружу не пропускает).

Скрипт НИЧЕГО не пишет в базу: соединение открывается read-only.
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
import psycopg

_HERE = Path(__file__).resolve()
# В репозитории файл лежит в backend/worker/, в контейнере-выгружателе — в
# корне /app, где двух родителей просто нет.
REPO = _HERE.parents[2] if len(_HERE.parents) > 2 else _HERE.parent
# Пути переопределяются окружением: тот же код работает и с ноутбука из
# репозитория, и внутри контейнера-выгружателя, где раскладка другая.
SQL_PATH = Path(os.environ.get(
    "COSSU_SQL_PATH", Path(__file__).resolve().parent / "sql" / "cossu_export.sql"))
STATE_PATH = Path(os.environ.get(
    "COSSU_STATE_PATH", REPO / "data" / "output" / "cossu_export_state.json"))
OUT_DIR = Path(os.environ.get("COSSU_OUT_DIR", REPO / "data" / "output"))


class SanityError(Exception):
    """Выгрузка не прошла проверки и не должна идти дальше по пайплайну."""

# Порядок и имена колонок, которые обязан вернуть запрос. Должны совпадать с
# COLUMN_MAP в seed_cossu.py — если разъедутся, данные лягут не в те поля.
EXPECTED_COLUMNS = [
    "branch_ids", "region", "kato_region", "rayon", "kato_rayon",
    "rayon2", "kato_rayon2", "additional_address_ru", "org_bin", "sobst",
    "org_name", "fulladdress", "otd_name", "otd_typ", "otd_podtyp",
    "fakt_koika_mesto", "residents_count", "queue_count",
]
INT_COLUMNS = {"fakt_koika_mesto", "residents_count", "queue_count"}

# Ниже этого числа строк выгрузка считается неполной и дальше не идёт.
# На 21.09.2026 запрос отдаёт 1233 строки.
DEFAULT_MIN_ROWS = 900
# Допустимое падение количества строк относительно прошлой удачной выгрузки.
DEFAULT_MAX_DROP = 0.10


def load_env_file(path: Path) -> None:
    """Подтягивает переменные из .env-файла, не перетирая уже заданные."""
    if not path.exists():
        return
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def connect_params() -> dict:
    load_env_file(REPO / "proon.env")
    missing = [k for k in ("PROON_HOST", "PROON_DB", "PROON_USER", "PROON_PASSWORD")
               if not os.environ.get(k)]
    if missing:
        sys.exit("ERROR: не заданы параметры подключения: %s\n"
                 "Создайте proon.env в корне репозитория (см. шапку скрипта)."
                 % ", ".join(missing))
    return {
        "host": os.environ["PROON_HOST"],
        "port": int(os.environ.get("PROON_PORT", "5432")),
        "dbname": os.environ["PROON_DB"],
        "user": os.environ["PROON_USER"],
        "password": os.environ["PROON_PASSWORD"],
        "connect_timeout": int(os.environ.get("PROON_CONNECT_TIMEOUT", "20")),
    }


def fetch_rows(schema: str, statement_timeout: str):
    """Выполняет запрос и возвращает (колонки, строки). Только чтение."""
    params = connect_params()
    sql = io.open(SQL_PATH, encoding="utf-8").read()
    started = datetime.now()
    conn = psycopg.connect(**params)
    conn.read_only = True
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET search_path TO %s, public" % schema)
            cur.execute("SET statement_timeout TO '%s'" % statement_timeout)
            cur.execute(sql)
            columns = [d.name for d in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()
    elapsed = (datetime.now() - started).total_seconds()
    return columns, rows, elapsed


def check_sanity(columns, rows, min_rows: float, max_drop: float) -> dict:
    """Проверки до записи файла. Любая осечка — выход с ошибкой.

    Нужны потому, что дальше по пайплайну идёт cossu_remove_stale.py, который
    удаляет из нашей базы всё, чего нет в файле. Неполная выгрузка (оборванный
    VPN, частичный ответ) без этих проверок вычистила бы ЦОССУ с карты.
    """
    if columns != EXPECTED_COLUMNS:
        raise SanityError("запрос вернул не те колонки.\n  ожидалось: %s\n  получено:  %s"
                 % (", ".join(EXPECTED_COLUMNS), ", ".join(columns)))

    if len(rows) < min_rows:
        raise SanityError("строк %d, это меньше порога %d — выгрузка выглядит неполной.\n"
                 "Если источник действительно так сократился, перезапустите с "
                 "--min-rows <новый порог>." % (len(rows), min_rows))

    prev = {}
    if STATE_PATH.exists():
        try:
            prev = json.loads(io.open(STATE_PATH, encoding="utf-8").read())
        except (ValueError, OSError):
            prev = {}
    prev_rows = prev.get("rows")
    if prev_rows:
        drop = (prev_rows - len(rows)) / float(prev_rows)
        if drop > max_drop:
            raise SanityError("строк стало %d против %d в прошлый раз (-%.1f%%), "
                     "порог -%.0f%%.\nЕсли сокращение настоящее, перезапустите с "
                     "--max-drop <доля>." % (len(rows), prev_rows, drop * 100, max_drop * 100))

    bin_idx = EXPECTED_COLUMNS.index("org_bin")
    branch_idx = EXPECTED_COLUMNS.index("branch_ids")
    no_bin = sum(1 for r in rows if not r[bin_idx])
    branch_ids = [r[branch_idx] for r in rows]
    dup = len(branch_ids) - len(set(branch_ids))

    return {
        "rows": len(rows),
        "orgs": len({r[bin_idx] for r in rows}),
        "no_bin": no_bin,
        "dup_branch_ids": dup,
        "beds": sum(int(r[EXPECTED_COLUMNS.index("fakt_koika_mesto")] or 0) for r in rows),
        "residents": sum(int(r[EXPECTED_COLUMNS.index("residents_count")] or 0) for r in rows),
        "queue": sum(int(r[EXPECTED_COLUMNS.index("queue_count")] or 0) for r in rows),
        "prev_rows": prev_rows,
    }


def write_xlsx(columns, rows, out_path: Path) -> None:
    """Пишет файл в том виде, в каком его ждёт seed_cossu.py: шапка в первой
    строке, данные со второй, 18 колонок в фиксированном порядке."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "cossu"
    ws.append(columns)
    for row in rows:
        cells = []
        for name, value in zip(columns, row):
            if value is None:
                cells.append(None)
            elif name in INT_COLUMNS or isinstance(value, Decimal):
                cells.append(int(value))
            else:
                cells.append(value)
        ws.append(cells)
    wb.save(out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Выгрузка ЦОССУ из базы proon в xlsx")
    ap.add_argument("--out", default=None,
                    help="путь к файлу (по умолчанию data/output/cossu_<дата>.xlsx)")
    ap.add_argument("--schema", default=os.environ.get("PROON_SCHEMA", "social_proon"))
    ap.add_argument("--min-rows", type=int, default=DEFAULT_MIN_ROWS)
    ap.add_argument("--max-drop", type=float, default=DEFAULT_MAX_DROP,
                    help="допустимая доля падения числа строк, 0.10 = 10%%")
    ap.add_argument("--statement-timeout", default="600s")
    args = ap.parse_args()

    out_path = Path(args.out) if args.out else (
        OUT_DIR / ("cossu_%s.xlsx" % datetime.now().strftime("%Y_%m_%d"))
    )

    print("Подключаюсь к базе ...")
    columns, rows, elapsed = fetch_rows(args.schema, args.statement_timeout)
    print("  запрос отработал за %.1f c, строк: %d" % (elapsed, len(rows)))

    try:
        stats = check_sanity(columns, rows, args.min_rows, args.max_drop)
    except SanityError as exc:
        sys.exit("ERROR: %s" % exc)
    write_xlsx(columns, rows, out_path)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    io.open(STATE_PATH, "w", encoding="utf-8").write(json.dumps({
        "rows": stats["rows"], "orgs": stats["orgs"],
        "file": str(out_path), "at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2))

    print("\nФайл: %s" % out_path)
    print("  строк:        %d%s" % (
        stats["rows"],
        "" if not stats["prev_rows"] else " (в прошлый раз %d)" % stats["prev_rows"]))
    print("  учреждений:   %d" % stats["orgs"])
    print("  койко-мест:   %d" % stats["beds"])
    print("  проживающих:  %d" % stats["residents"])
    print("  в очереди:    %d" % stats["queue"])
    if stats["no_bin"]:
        print("  ВНИМАНИЕ: строк без org_bin: %d — seed_cossu.py их пропустит"
              % stats["no_bin"])
    if stats["dup_branch_ids"]:
        print("  ВНИМАНИЕ: повторяющихся branch_ids: %d — записи затрут друг друга"
              % stats["dup_branch_ids"])
    print("\nДальше: seed_cossu.py → cossu_remove_stale.py → geocode_cossu_2gis.py")


if __name__ == "__main__":
    main()
