"""Выгрузка СЁЛ региона в Excel с иерархией КАТО: регион → район → сельский округ → НП.

Источник — pop_dwelling (kind='village'): одно село = одна строка, вся демография
уже схлопнута в stats. Название сельского округа в pop_dwelling НЕТ, берём его из
corpus.full_path_rus (адреса egov содержат «СЕЛЬСКИЙ ОКРУГ X») по общим РКА.

ВАЖНО: это выгрузка ПО НАСЕЛЁННЫМ ПУНКТАМ, а не по людям. Персональных
идентификаторов (SICID/ИИН) в наборе населения нет — там ключ РКА (адрес).

Запуск (ХОСТ):
    python backend/worker/export_villages_xlsx.py "КАРАГАНДИНСКАЯ" [out.xlsx]
"""
from __future__ import annotations
import sys
import psycopg
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"

HEADER = [
    ("КАТО",              12),
    ("Регион",            26),
    ("Район",             28),
    ("Сельский округ",    30),
    ("Населённый пункт",  34),
    ("Всего человек",     14),
    ("Мужчин",            10),
    ("Женщин",            10),
    ("Дети до 18",        12),
    ("Труд. возраст",     14),
    ("Работающих",        13),
    ("Пенсионеров",       13),
    ("Студентов",         11),
    ("ЛСИ",                8),
    ("АСП",                8),
    ("ИП",                 8),
    ("Многодетных",       13),
    ("Кандасов",          11),
    ("Адресов (РКА)",     14),
    ("Широта",            12),
    ("Долгота",           12),
    ("Точность геокода",  18),
    ("Слито строк",       12),
]

SQL = r"""
WITH v AS (
    SELECT dwelling_id, bkey, geocode_addr, rca_count, lat, lon, precision, stats
      FROM pop_dwelling
     WHERE kind = 'village' AND stats->>'regname' ILIKE %(reg)s
), okrug AS (
    -- сельский округ берём из адресов egov; на село их много, берём самый частый.
    -- У ПОСЁЛКОВ этого уровня в иерархии нет (район -> посёлок), там будет пусто.
    SELECT m.dwelling_id,
           mode() WITHIN GROUP (
               ORDER BY initcap(btrim((regexp_match(c.full_path_rus,
                        'СЕЛЬСКИЙ ОКРУГ\s+([^,]+)', 'i'))[1]))
           ) AS okrug_name
      FROM v
      JOIN pop_rca_dwelling m ON m.dwelling_id = v.dwelling_id
      JOIN corpus c           ON c.rca = m.rca
     WHERE c.full_path_rus ~* 'СЕЛЬСКИЙ ОКРУГ'
     GROUP BY m.dwelling_id
), j AS (
    SELECT
        -- bkey = 'V'+КАТО, но у части сёл дедуп свалился на текст адреса —
        -- такие пропускаем, иначе в колонку КАТО лезет кусок адреса
        CASE WHEN ltrim(v.bkey, 'V') ~ '^[0-9]+$' THEN ltrim(v.bkey, 'V') END AS kato,
        initcap(v.stats->>'regname')  AS reg,
        initcap(v.stats->>'rainame')  AS rai,
        coalesce(o.okrug_name, '')    AS ok,
        coalesce(nullif(initcap(btrim(split_part(v.geocode_addr, ',', 1))), ''),
                 '(адрес не указан)') AS np,
        (v.stats->>'total')::int          AS total,
        (v.stats->>'male')::int           AS male,
        (v.stats->>'female')::int         AS female,
        (v.stats->>'deti_do18')::int      AS deti,
        (v.stats->>'trud_vozrast')::int   AS trud,
        (v.stats->>'working')::int        AS working,
        (v.stats->>'pensioners')::int     AS pens,
        (v.stats->>'student')::int        AS stud,
        (v.stats->>'lsi')::int            AS lsi,
        (v.stats->>'asp')::int            AS asp,
        (v.stats->>'ip')::int             AS ip,
        (v.stats->>'mnogodetnyi')::int    AS mnogo,
        (v.stats->>'kandas')::int         AS kandas,
        v.rca_count, v.lat, v.lon, v.precision
      FROM v LEFT JOIN okrug o ON o.dwelling_id = v.dwelling_id
)
-- Схлопываем НП: один посёлок/село может лежать в 2 строках pop_dwelling
-- (одна по КАТО, вторая по адресу) — для отчёта это один населённый пункт.
SELECT coalesce(max(kato), '—')            AS kato,
       reg, rai,
       coalesce(nullif(max(ok), ''), '—')  AS okrug,
       np,
       sum(total), sum(male), sum(female), sum(deti), sum(trud), sum(working),
       sum(pens), sum(stud), sum(lsi), sum(asp), sum(ip), sum(mnogo), sum(kandas),
       sum(rca_count),
       (array_agg(lat       ORDER BY total DESC NULLS LAST))[1] AS lat,
       (array_agg(lon       ORDER BY total DESC NULLS LAST))[1] AS lon,
       (array_agg(precision ORDER BY total DESC NULLS LAST))[1] AS precision,
       count(*)                            AS слито_строк
  FROM j
 GROUP BY reg, rai, np
 ORDER BY rai, okrug, np
"""


def main() -> None:
    if len(sys.argv) < 2:
        print('Укажи регион, напр.: python ... "КАРАГАНДИНСКАЯ"', file=sys.stderr)
        sys.exit(1)
    region = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else f"села_{region.lower()}.xlsx"

    conn = psycopg.connect(DSN)
    print(f"регион ILIKE %{region}% ...", flush=True)
    rows = conn.execute(SQL, {"reg": f"%{region}%"}).fetchall()
    conn.close()
    print(f"  найдено сёл: {len(rows):,}", flush=True)
    if not rows:
        print("  ничего не найдено — проверь написание региона", file=sys.stderr)
        sys.exit(2)

    wb = Workbook()
    ws = wb.active
    ws.title = "Сёла"
    ws.append([h for h, _ in HEADER])
    for i, (_, w) in enumerate(HEADER, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    hdr_font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.font = hdr_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.fill = __import__("openpyxl").styles.PatternFill("solid", fgColor="2563EB")
    ws.freeze_panes = "F2"          # иерархия слева всегда видна
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADER))}{len(rows) + 1}"

    for r in rows:
        ws.append(list(r))

    # Итоговая строка
    last = len(rows) + 2
    ws.cell(last, 5, "ИТОГО").font = Font(bold=True)
    for col in range(6, 20):
        c = ws.cell(last, col, f"=SUM({get_column_letter(col)}2:{get_column_letter(col)}{last-1})")
        c.font = Font(bold=True)

    wb.save(out)
    people = sum(r[5] or 0 for r in rows)
    print(f"\nГотово: {out}")
    print(f"  сёл: {len(rows):,}   человек: {people:,}")


if __name__ == "__main__":
    main()
