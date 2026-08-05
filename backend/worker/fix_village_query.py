"""Переводит сельские geocode_addr в формат 'село W, область O' — 2ГИС так
находит само село в 77% против 34% у полного адреса (лишний район/с.о. сбивал
на райцентр). Полный адрес остаётся в sample_addr_ru.

Запуск (ХОСТ): python backend/worker/fix_village_query.py
"""
from __future__ import annotations
import re, psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"
NP  = re.compile(r'((?:сел[оа]|аул|пос[её]лок|станция|разъезд|кент)\s+[^,]+)', re.I)
OBL = re.compile(r'област[ьи]\s+([^,]+)', re.I)


def main() -> None:
    conn = psycopg.connect(DSN, autocommit=False)
    rows = conn.execute(
        "SELECT dwelling_id, geocode_addr FROM pop_dwelling WHERE kind='village' "
        "AND geocode_addr IS NOT NULL AND geocode_addr<>''"
    ).fetchall()
    upd, skip = [], 0
    for did, addr in rows:
        np = NP.findall(addr)
        obl = OBL.search(addr)
        if np and obl:
            upd.append((f"{np[-1].strip()}, область {obl.group(1).strip()}", did))
        else:
            skip += 1
    with conn.cursor() as cur:
        cur.executemany("UPDATE pop_dwelling SET geocode_addr=%s WHERE dwelling_id=%s", upd)
    conn.commit()
    print(f"сёл всего: {len(rows)} | переформатировано: {len(upd)} | пропущено (нет НП/области): {skip}")
    print("примеры:")
    for a, _ in upd[:5]:
        print("  ", a)
    conn.close()


if __name__ == "__main__":
    main()
