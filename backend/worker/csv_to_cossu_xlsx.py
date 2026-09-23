"""
Превращает CSV-выгрузку ЦОССУ из DBeaver в xlsx для пайплайна.

Зачем отдельный скрипт: если открыть такой CSV в Excel и пересохранить в xlsx,
записи рвутся по запятым и кавычкам внутри полей (названия организаций, адреса).
Получается мусор — обрывки названий в колонке branch_ids, сдвинутые поля. При
заливке такого файла cossu_remove_stale.py удалит из базы живые учреждения,
потому что их branch_id в файле не найдётся.

Штатный csv-парсер Python кавычки и переводы строк внутри полей понимает
правильно, поэтому конвертировать нужно им, а не Excel.

Запуск:
    python backend/worker/csv_to_cossu_xlsx.py выгрузка.csv
    python backend/worker/csv_to_cossu_xlsx.py выгрузка.csv --out data/output/cossu.xlsx

Дальше файл идёт обычным путём:
    seed_cossu.py → cossu_remove_stale.py → geocode_cossu_2gis.py
"""
import argparse
import csv
import io
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_cossu_from_proon import (  # noqa: E402
    EXPECTED_COLUMNS, INT_COLUMNS, OUT_DIR, write_xlsx,
)


def read_csv(path: Path):
    """Читает CSV, разбираясь с кодировкой. Возвращает (колонки, строки)."""
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            print("кодировка: %s" % enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        sys.exit("ERROR: не удалось определить кодировку файла")

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        sys.exit("ERROR: файл пуст")
    return rows[0], rows[1:]


def main() -> None:
    ap = argparse.ArgumentParser(description="CSV-выгрузка ЦОССУ → xlsx")
    ap.add_argument("csv_path")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = Path(args.csv_path)
    if not src.exists():
        sys.exit("ERROR: файл не найден: %s" % src)

    header, rows = read_csv(src)
    print("строк данных: %d" % len(rows))

    if [h.strip() for h in header] != EXPECTED_COLUMNS:
        sys.exit("ERROR: шапка не совпадает с ожидаемой.\n  ожидалось: %s\n  в файле:   %s"
                 % (", ".join(EXPECTED_COLUMNS), ", ".join(header)))

    bad_width = [i + 2 for i, r in enumerate(rows) if len(r) != len(EXPECTED_COLUMNS)]
    if bad_width:
        sys.exit("ERROR: в %d строках не 18 колонок (например, строки %s) — "
                 "файл повреждён" % (len(bad_width), bad_width[:5]))

    # Числовые колонки приводим к int, пустые значения — в None, чтобы файл
    # получился таким же, как у автоматической выгрузки из базы.
    clean = []
    for r in rows:
        out_row = []
        for name, value in zip(EXPECTED_COLUMNS, r):
            value = (value or "").strip()
            if value == "":
                out_row.append(None)
            elif name in INT_COLUMNS:
                try:
                    out_row.append(int(float(value)))
                except ValueError:
                    out_row.append(None)
            else:
                out_row.append(value)
        clean.append(out_row)

    out_path = Path(args.out) if args.out else (
        OUT_DIR / ("cossu_%s.xlsx" % datetime.now().strftime("%Y_%m_%d"))
    )
    write_xlsx(EXPECTED_COLUMNS, clean, out_path)

    bin_idx = EXPECTED_COLUMNS.index("org_bin")
    br_idx = EXPECTED_COLUMNS.index("branch_ids")
    no_bin = sum(1 for r in clean if not r[bin_idx])
    branches = [r[br_idx] for r in clean]
    print("\nФайл: %s" % out_path)
    print("  строк:       %d" % len(clean))
    print("  учреждений:  %d" % len({r[bin_idx] for r in clean if r[bin_idx]}))
    print("  койко-мест:  %d" % sum(int(r[EXPECTED_COLUMNS.index("fakt_koika_mesto")] or 0)
                                    for r in clean))
    print("  проживающих: %d" % sum(int(r[EXPECTED_COLUMNS.index("residents_count")] or 0)
                                    for r in clean))
    print("  в очереди:   %d" % sum(int(r[EXPECTED_COLUMNS.index("queue_count")] or 0)
                                    for r in clean))
    if no_bin:
        print("  ВНИМАНИЕ: строк без org_bin: %d — seed_cossu.py их пропустит" % no_bin)
    dup = len(branches) - len(set(branches))
    if dup:
        print("  ВНИМАНИЕ: повторяющихся branch_ids: %d" % dup)


if __name__ == "__main__":
    main()
