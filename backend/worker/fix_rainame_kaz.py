"""Чинит казахские буквы в названиях районов, которые при импорте населения
превратились в «?». Правит pop_dwelling.stats->>'rainame' и pop_v3.name_cato1.

Идемпотентно: ищет по испорченной строке; после правки совпадений уже нет.
Запуск: docker compose exec backend python /app/worker/fix_rainame_kaz.py"""
from __future__ import annotations
import os
import psycopg

DSN = (
    f"host={os.getenv('POSTGRES_HOST', 'postgres')} "
    f"dbname={os.getenv('POSTGRES_DB', 'geo')} "
    f"user={os.getenv('POSTGRES_USER', 'geo')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'geopassword123')}"
)

# испорченное → правильное (13 районов по всей стране)
FIX = {
    "РАЙОН А?СУАТ":        "РАЙОН АҚСУАТ",
    "РАЙОН ЖА?АСЕМЕЙ":     "РАЙОН ЖАҢАСЕМЕЙ",
    "РАЙОН МА?АНШЫ":       "РАЙОН МАҚАНШЫ",
    "?ОНАЕВ Г.А.":         "ҚОНАЕВ Г.А.",
    "РАЙОН Б?ЙТЕРЕК":      "РАЙОН БӘЙТЕРЕК",
    "РАЙОН ТЕРЕ?К?Л":      "РАЙОН ТЕРЕҢКӨЛ",
    "РАЙОН А??УЛЫ":        "РАЙОН АҚҚУЛЫ",
    "РАЙОН МАР?АК?Л":      "РАЙОН МАРҚАКӨЛ",
    "РАЙОН ?ЛКЕН НАРЫН":   "РАЙОН ҮЛКЕН НАРЫН",
    "РАЙОН БАЙ?О?ЫР":      "РАЙОН БАЙҚОҢЫР",
    "РАЙОН Н?РА":          "РАЙОН НҰРА",
    "РАЙОН САРАЙШЫ?":      "РАЙОН САРАЙШЫҚ",
    "РАЙОН Т?РАН":         "РАЙОН ТҰРАН",
}

c = psycopg.connect(DSN, autocommit=True)
for bad, good in FIX.items():
    n1 = c.execute(
        "UPDATE pop_dwelling SET stats = jsonb_set(stats, '{rainame}', to_jsonb(%s::text)) "
        "WHERE stats->>'rainame' = %s",
        (good, bad),
    ).rowcount
    n2 = c.execute(
        "UPDATE pop_v3 SET name_cato1 = %s WHERE name_cato1 = %s",
        (good, bad),
    ).rowcount
    print(f"  {bad:<20} → {good:<20} pop_dwelling:{n1}  pop_v3:{n2}", flush=True)

left = c.execute("SELECT count(*) FROM pop_dwelling WHERE stats->>'rainame' LIKE '%?%'").fetchone()[0]
print(f"осталось испорченных rainame в pop_dwelling: {left}", flush=True)
c.close()
