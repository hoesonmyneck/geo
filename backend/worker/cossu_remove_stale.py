"""
Удаляет из таблицы cossu отделения, которых НЕТ в актуальной выгрузке xlsx.

seed_cossu.py добавляет/обновляет по branch_id, но НЕ удаляет отсутствующие.
Этот скрипт добивает: удаляет строки, чей branch_id не встречается в новом файле
(и строки без branch_id — это мусор от прошлых заливок).

Запуск:
    docker compose exec backend bash -lc "cd /app && python worker/cossu_remove_stale.py /app/data/input/cossu_new.xlsx"
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import openpyxl
from sqlalchemy import delete, select, func

from app.db.session import AsyncSessionLocal
from app.db.models import Cossu


def read_branch_ids(path: Path) -> set[str]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    bids: set[str] = set()
    for r, row in enumerate(ws.iter_rows(values_only=True)):
        if r == 0:
            continue  # заголовок
        v = row[0]  # branch_ids — первая колонка
        if v is not None and str(v).strip():
            bids.add(str(v).strip())
    wb.close()
    return bids


async def main():
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app/data/input/cossu.xlsx")
    if not xlsx.exists():
        print(f"ERROR: файл не найден: {xlsx}")
        sys.exit(1)

    file_bids = read_branch_ids(xlsx)
    print(f"branch_id в файле: {len(file_bids)}")

    async with AsyncSessionLocal() as db:
        total = (await db.execute(select(func.count()).select_from(Cossu))).scalar()
        # Удаляем: branch_id пустой ИЛИ его нет в файле
        res = await db.execute(
            delete(Cossu).where(
                (Cossu.branch_id.is_(None)) | (Cossu.branch_id.notin_(file_bids))
            )
        )
        await db.commit()
        left = (await db.execute(select(func.count()).select_from(Cossu))).scalar()
        orgs = (await db.execute(select(func.count(func.distinct(Cossu.org_bin))))).scalar()
        print(f"было: {total} строк | удалено: {res.rowcount} | осталось: {left} строк, {orgs} учреждений")


if __name__ == "__main__":
    asyncio.run(main())
