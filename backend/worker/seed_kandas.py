"""
Засевает таблицу kandas данными.
Если запись с таким ИИН уже есть — обновляет, не дублирует.

Запуск:
    docker compose exec backend python /app/worker/seed_kandas.py
"""
import asyncio, sys
sys.path.insert(0, "/app")

from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models import Kandas

KANDAS_DATA = [
    {
        "fio":         "ОРДАБАЕВ АМАН РАШИДОВИЧ",
        "iin":         "990421051069",
        "dob":         "21.04.2000",
        "age":         26,
        "citizenship": "Россия",
        "gender":      "Мужской",
        "oblast":      "Северо-Казахстанская",
        "raion":       "Петропавловск",
        "city":        "Петропавловск",
        "street":      "Жабаева",
        "house":       "170",
        "apt":         None,
        "phone":       "87471907407",
        "extra": {
            "nationality": "Казах",
            "cks_cat":     "D",
        },
    },
    {
        "fio":         "ЕЛИКБАЙ ТАУ",
        "iin":         "980525000345",
        "dob":         "25.05.1998",
        "age":         28,
        "citizenship": "Монголия",
        "gender":      "Мужской",
        "oblast":      "Восточно-Казахстанская",
        "raion":       "Уланский район",
        "city":        "с.Айыртау",
        "street":      "Токан Сембаев",
        "house":       "17",
        "apt":         "1",
        "phone":       "87073661474",
        "extra": {
            "nationality":  "Казах",
            "bin_work":     "980940002576",
            "work_org":     "Школа-лицей №3 им. Шокана Уалиханова, г.Усть-Каменогорск",
            "work_address": "г.Усть-Каменогорск, ул.Крылова, 35",
            "work_type":    "Основная работа",
            "position":     "Техник по обслуживанию компьютерных устройств",
            "staff_pos":    "Лаборант компьютерных классов",
            "education":    "ВКТУ им. Д.Серикбаева",
            "specialty":    "5В070500 Математическое и компьютерное моделирование, выпускник",
            "status":       "С",
            "cks_cat":      "С",
            "benefits":     "Подушевое финансирование гос. органов среднего образования, 01.01.2024–31.12.2025, сумма: 658 821 ₸",
        },
    },
    {
        "fio":         "БОЛАТХАН МӘРМӘР",
        "iin":         "020218051282",
        "dob":         "18.02.2002",
        "age":         24,
        "citizenship": "КНР",
        "gender":      "Женский",
        "oblast":      "Восточно-Казахстанская",
        "raion":       "Усть-Каменогорск",
        "city":        "Усть-Каменогорск",
        "street":      "мкр. Подхоз СЦК",
        "house":       "77",
        "apt":         None,
        "phone":       None,
        "extra": {
            "nationality": "Казах",
            "note":        "Адрес регистрации не указан",
        },
    },
    {
        "fio":         "РАНБАЕВ ЕРБОЛАТ АРАЛБАЕВИЧ",
        "iin":         "040118050732",
        "dob":         "18.01.2004",
        "age":         22,
        "citizenship": "Россия",
        "gender":      "Мужской",
        "oblast":      "Северо-Казахстанская",
        "raion":       "Петропавловск",
        "city":        "Петропавловск",
        "street":      None,
        "house":       None,
        "apt":         None,
        "phone":       "87770384710",
        "extra": {
            "nationality": "Казах",
            "cks_cat":     "D",
            "note":        "Улица и дом не указаны",
        },
    },
    {
        "fio":         "Тилеужан Еркингул",
        "iin":         "010608000276",
        "dob":         "08.06.2001",
        "age":         24,
        "citizenship": "Монголия",
        "gender":      "Женский",
        "oblast":      "Восточно-Казахстанская",
        "raion":       "Усть-Каменогорск",
        "city":        "Усть-Каменогорск",
        "street":      "Серикбаева",
        "house":       "19",
        "apt":         "303Б",
        "phone":       None,
        "extra": {
            "nationality": "Казах",
            "education":   "Восточно-Казахстанский университет им. Сарсена Аманжолова",
            "specialty":   "В006 Подготовка учителей музыки",
            "status":      "Учится",
            "family": [
                {"role": "Муж",     "fio": "Аманкелди Алпамыс", "iin": "980118051023"},
                {"role": "Ребёнок", "fio": "Алпамыс Айбиби",    "iin": "220524050190"},
                {"role": "Ребёнок", "fio": "Алпамыс Арнур",     "iin": "250408050086"},
            ],
        },
    },
]


async def main():
    async with AsyncSessionLocal() as db:
        inserted = updated = 0
        seen_iins = set()
        for data in KANDAS_DATA:
            iin = data.get("iin")
            # Пропускаем дубликаты внутри списка (одинаковый ИИН)
            if iin and iin in seen_iins:
                print(f"  Skipped duplicate IIN: {iin}")
                continue
            if iin:
                seen_iins.add(iin)

            existing = None
            if iin:
                result = await db.execute(select(Kandas).where(Kandas.iin == iin))
                existing = result.scalar_one_or_none()

            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
                updated += 1
                print(f"  Updated: {data['fio']}")
            else:
                db.add(Kandas(**data))
                inserted += 1
                print(f"  Inserted: {data['fio']}")

        await db.commit()
        print(f"\nDone: {inserted} inserted, {updated} updated.")


if __name__ == "__main__":
    asyncio.run(main())
