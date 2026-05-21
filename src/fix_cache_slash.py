"""Очищаем кэш полностью — все medium/low/miss + все high записи.
После исправления логики geocode.py (пасс с домом не возвращает highway
раньше времени) нужно пересчитать все адреса с нуля.
Высокоточные (high) результаты будут переполучены заново и снова закэшированы.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "cache.sqlite"
conn = sqlite3.connect(str(DB_PATH))

stats = dict(conn.execute(
    "SELECT confidence, COUNT(*) FROM geocode_cache GROUP BY confidence"
).fetchall())
print("Кэш до очистки:", stats)

deleted = conn.execute("DELETE FROM geocode_cache").rowcount
conn.commit()

remaining = conn.execute("SELECT COUNT(*) FROM geocode_cache").fetchone()[0]
conn.close()

print(f"Удалено записей: {deleted:,}")
print(f"Осталось в кэше: {remaining}")
print("Готово. Запустите pipeline заново.")
