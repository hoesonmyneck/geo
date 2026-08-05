"""Пересборка building из staging_person (дедуп адресов → точки для 2GIS).

Раньше этот дедуп жил разовыми запросами в psql и нигде не сохранился —
при следующем обновлении населения логику пришлось бы восстанавливать по
осколкам. Теперь он здесь.

Три класса точек:

  B  многоквартирный дом — группируем по s_building_id из РКА.
     Надёжнее текста: один building_id = один дом, как бы ни был записан
     адрес ("дом 33" / "д.33" / "33а").

  A  прочие адреса (частный сектор) — building_id нет, группируем по
     нормализованному тексту адреса.

  V  село — НЕ геокодим каждый дом: все жители села садятся в одну точку.
     Группируем по цепочке КАТО (область|район|с.о.|НП).

Про географию сёл (важно, тут была ошибка):
  В KARTA две пары гео-колонок, и они означают РАЗНОЕ.
    * name_cato0..3   — цепочка адреса. Иерархия верная. Дефект: в исходном
                        xlsx потеряны казахские буквы ("РАЙОН БАЙ?О?ЫР").
    * kato_regname/rainame — административная привязка (прописка). Текст
                        чистый, но у трети сёл область НЕ совпадает с адресом
                        (карасайское село Алматинской подписано "г.Шымкент").
  Прошлая сборка брала для geocode_addr именно kato_regname/rainame — и треть
  сёл поехала бы в 2GIS с чужой областью. Берём name_cato*: неверная область
  ломает геокод гарантированно, а "?" в названии 2GIS чаще всего переживает.

Запуск:
    python backend/worker/rebuild_building.py [--apply]
Без --apply собирает building_new и печатает сверку со старой building,
ничего не подменяя.
"""
from __future__ import annotations

import argparse
import sys

import psycopg

DSN = "host=localhost port=5432 dbname=geo user=geo password=geopassword123"


APPLY_FIXES = """
-- Вливаем починенные адреса РКА в сырьё, чтобы dedup_key считался от чистого
-- текста. Ключ по rca — точное совпадение, без сопоставления строк.
UPDATE staging_person s
   SET clean_address_ru = f.rus,
       clean_address_kz = NULLIF(f.kaz, '')
  FROM rka_fix f
 WHERE s.rca = f.rca
   AND s.clean_address_ru IS DISTINCT FROM f.rus;
"""

BUILD_STEPS = ["""
DROP TABLE IF EXISTS building_new
""", """
-- INCLUDING ALL копирует и default для id, отдельный ALTER не нужен
CREATE TABLE building_new (LIKE building INCLUDING ALL)
""", """
DROP TABLE IF EXISTS addr_building_map
""", """
-- Карта "адрес -> building_id" по строкам, где building_id ЕСТЬ.
--
-- Зачем: у одного дома часть жильцов имеет building_id, часть нет. Первые
-- шли в ключ B, вторые — в A, и дом разъезжался на ДВЕ точки с одним адресом
-- (19 390 домов). На карте два маркера друг на друге, в карточках — половина
-- жильцов. Теперь безномерные строки подтягивают building_id по адресу.
--
-- Адрес может указывать на несколько building_id (у РКА свои дубли) — берём
-- минимальный, лишь бы одинаково здесь и в link_persons.py.
CREATE TABLE addr_building_map AS
SELECT lower(regexp_replace(clean_address_ru, ',\\s*(Квартира|Пәтер)\\s+[^,]*$', '')) AS addr_norm,
       min(building_id) AS building_id
  FROM staging_person
 WHERE type_ <> 'СЕЛО'
   AND building_id IS NOT NULL AND building_id <> ''
   AND clean_address_ru IS NOT NULL AND clean_address_ru <> ''
 GROUP BY 1
""", """
ALTER TABLE addr_building_map ADD PRIMARY KEY (addr_norm)
""", """
-- V: сёла. Одна точка на населённый пункт.
--
-- У 336 групп (276 703 человека) name_cato3 (сам НП) пустой. Прошлая сборка
-- делала из них безымянные точки на центр района. Достаём НП из чистого
-- РКА-адреса ("..., сельский округ Кошкарата, село Ошакты, улица ...") и
-- приводим к формату name_cato3 ("Ошакты" -> "С.ОШАКТЫ"), чтобы ключ совпал
-- с уже существующими сёлами и точка не раздвоилась.
--
-- name_cato2 (сельский округ) в ключ НЕ берём. Он ненадёжен: у жителей ОДНОГО
-- дома ("город Риддер, поселок Ульба, улица Гэсовская, дом 1") name_cato2
-- бывает разным — С.ЛИВИНО, С.БУТАКОВО и т.д., то есть противоречит адресу.
-- С ним в ключе один посёлок Ульба разъезжался на 8 точек (1 245 сёл,
-- 1 410 лишних точек; в старой сборке та же болезнь тише — 77 групп).
-- Цена: два одноимённых села в разных с.о. одного района сольются в одну
-- точку. Они и так рядом, а битый ключ ломает больше.
-- geocode_addr берём из ЧИСТОГО РКА-адреса, обрезанного по селу, а НЕ из
-- name_cato*. В name_cato* потеряны казахские буквы ("РАЙОН Б?ЙТЕРЕК"), а в
-- РКА то же самое лежит нормальной кодировкой ("район Бәйтерек") — и полнее,
-- с сельским округом, в том же формате, что у 900 тыс. домов. Покрытие 93%;
-- остальным собираем строку из name_cato* как запасной вариант.
-- name_cato* остаётся ТОЛЬКО ключом группировки: там мохобайт безвреден,
-- лишь бы был одинаковым в пределах села.
INSERT INTO building_new (level, dedup_key, kato_np, regname, rainame, geocode_addr)
WITH m AS (
  -- Жадный '^.*' сам доводит до ПОСЛЕДНЕГО НП в строке: адрес идёт от общего
  -- к частному, и самый глубокий НП — верный ("город Риддер, поселок Ульба").
  SELECT s.name_cato0, s.name_cato1, s.name_cato3, s.sicid,
         regexp_match(s.clean_address_ru,
           '^(.*,\\s*(село|аул|пос[её]лок|станция|разъезд)\\s+([^,]+))', 'i') AS r,
         -- Для строк, где name_cato3 — на самом деле ГОРОДСКОЙ РАЙОН
         -- ("РАЙОН ИМ.КАЗЫБЕК БИ"): адрес, обрезанный по району. Иначе точке
         -- на 238 тыс. человек достался бы адрес случайного посёлка внутри
         -- района, да ещё с мохобайтом из name_cato3.
         regexp_match(s.clean_address_ru, '^(.*,\\s*район\\s+[^,]+)', 'i') AS rr,
         s.clean_address_ru
    FROM staging_person s
   WHERE s.type_ = 'СЕЛО'
     AND s.name_cato0 IS NOT NULL AND s.name_cato1 IS NOT NULL
     -- ЗАГРАНИЦА: 99 167 человек живут вне Казахстана. Точки у них быть не
     -- может, 2GIS по запросу "ЗАГРАНИЦА, ЗАГРАНИЦА, Казахстан" посадил бы
     -- их куда попало. В person они остаются с building_id = NULL.
     AND s.name_cato0 <> 'ЗАГРАНИЦА'
), n AS (
  SELECT *,
         -- название НП, как оно записано в РКА-адресе, в формате name_cato3
         CASE WHEN r IS NOT NULL THEN
           CASE lower(r[2])
             WHEN 'аул'     THEN 'А.'
             WHEN 'поселок' THEN 'П.'
             WHEN 'посёлок' THEN 'П.'
             WHEN 'станция' THEN 'СТ.'
             WHEN 'разъезд' THEN 'РЗД.'
             ELSE 'С.'
           END || upper(btrim(r[3]))
         END AS np_from_addr
    FROM m
), src AS (
  SELECT name_cato0, name_cato1, sicid,
         coalesce(nullif(name_cato3, ''), np_from_addr) AS np,
         CASE
           -- name_cato3 = городской район -> берём адрес по район, а не по НП
           WHEN name_cato3 LIKE 'РАЙОН%' AND rr IS NOT NULL
                AND clean_address_ru LIKE 'Республика Казахстан%' THEN btrim(rr[1])
           -- r[1] — адрес, обрезанный по последнему НП: "..., село Асан".
           -- Берём ТОЛЬКО если село в адресе совпадает с селом в ключе.
           -- Данные противоречивы: у строки с name_cato3='С.БУРГЕМ' адрес
           -- бывает "...город Кентау, село Карнак". Без этой сверки село
           -- получало адрес соседа и уезжало на чужие координаты (532 села).
           WHEN (name_cato3 IS NULL OR name_cato3 NOT LIKE 'РАЙОН%')
                AND r IS NOT NULL AND clean_address_ru LIKE 'Республика Казахстан%'
                AND np_from_addr = coalesce(nullif(name_cato3,''), np_from_addr)
             THEN btrim(r[1])
         END AS rka_np_addr
    FROM n
)
SELECT 'village',
       'V' || name_cato0 || '|' || name_cato1 || '|' || coalesce(np,''),
       np,
       name_cato0,
       name_cato1,
       coalesce(
         -- 1) чистый РКА: "Республика Казахстан, ..., район Бәйтерек, ..., село Асан"
         (array_agg(rka_np_addr ORDER BY sicid) FILTER (WHERE rka_np_addr IS NOT NULL))[1],
         -- 2) запасной: из name_cato* ("село АСАН, РАЙОН X, ОБЛАСТЬ Y, Казахстан").
         --    np пуст (нет ни КАТО, ни адреса) -> точка на центр района
         btrim(concat_ws(', ',
           CASE WHEN np IS NULL OR np = '' THEN NULL
                ELSE nullif(btrim(
                    CASE split_part(np,'.',1)
                        WHEN 'С'   THEN 'село'
                        WHEN 'А'   THEN 'аул'
                        WHEN 'П'   THEN 'поселок'
                        WHEN 'СТ'  THEN 'станция'
                        WHEN 'РЗД' THEN 'разъезд'
                        WHEN 'УЧ'  THEN 'участок'
                        ELSE ''
                    END || ' ' ||
                    CASE WHEN position('.' in np) > 0
                         THEN substr(np, position('.' in np)+1)
                         ELSE np END), '')
           END,
           name_cato1, name_cato0, 'Казахстан'), ', ')
       )
  FROM src
 GROUP BY name_cato0, name_cato1, np
""", """

-- B: многоквартирные дома по s_building_id (своему или подтянутому по адресу).
INSERT INTO building_new (level, dedup_key, s_building_id, clean_addr_ru, clean_addr_kz,
                          regname, rainame, geocode_addr)
SELECT 'house',
       'B' || bld,
       bld::bigint,   -- в staging это text, в building — bigint
       (array_agg(clean_address_ru ORDER BY sicid))[1],
       (array_agg(clean_address_kz ORDER BY sicid))[1],
       (array_agg(kato_regname   ORDER BY sicid))[1],
       (array_agg(kato_rainame   ORDER BY sicid))[1],
       -- в 2GIS без квартиры: дом ищем, квартиру нет
       regexp_replace((array_agg(clean_address_ru ORDER BY sicid))[1],
                      ',\\s*(Квартира|Пәтер)\\s+[^,]*$', '')
  FROM (
    SELECT s.*, coalesce(nullif(s.building_id,''), a.building_id) AS bld
      FROM staging_person s
      LEFT JOIN addr_building_map a
        ON a.addr_norm = lower(regexp_replace(s.clean_address_ru,
                                ',\\s*(Квартира|Пәтер)\\s+[^,]*$', ''))
     WHERE s.type_ <> 'СЕЛО'
       AND s.clean_address_ru IS NOT NULL AND s.clean_address_ru <> ''
  ) t
 WHERE bld IS NOT NULL
 GROUP BY bld
""", """
-- A: адреса, за которыми building_id не стоит вообще ни у кого.
INSERT INTO building_new (level, dedup_key, clean_addr_ru, clean_addr_kz,
                          regname, rainame, geocode_addr)
SELECT 'house',
       'A' || lower(regexp_replace(clean_address_ru, ',\\s*(Квартира|Пәтер)\\s+[^,]*$', '')),
       (array_agg(clean_address_ru ORDER BY sicid))[1],
       (array_agg(clean_address_kz ORDER BY sicid))[1],
       (array_agg(kato_regname ORDER BY sicid))[1],
       (array_agg(kato_rainame ORDER BY sicid))[1],
       regexp_replace((array_agg(clean_address_ru ORDER BY sicid))[1],
                      ',\\s*(Квартира|Пәтер)\\s+[^,]*$', '')
  FROM staging_person s
 WHERE s.type_ <> 'СЕЛО'
   AND (s.building_id IS NULL OR s.building_id = '')
   AND s.clean_address_ru IS NOT NULL AND s.clean_address_ru <> ''
   AND NOT EXISTS (
     SELECT 1 FROM addr_building_map a
      WHERE a.addr_norm = lower(regexp_replace(s.clean_address_ru,
                                ',\\s*(Квартира|Пәтер)\\s+[^,]*$', ''))
   )
 GROUP BY lower(regexp_replace(clean_address_ru, ',\\s*(Квартира|Пәтер)\\s+[^,]*$', ''))
"""]

CHECK = """
-- Без параллелизма: у контейнера постгреса /dev/shm всего 64 МБ (дефолт
-- Docker), и параллельные воркеры падают с DiskFull на shared memory.
SELECT
  (SELECT count(*) FROM building)                                        AS old_total,
  (SELECT count(*) FROM building_new)                                    AS new_total,
  (SELECT count(*) FROM building_new WHERE level='village')              AS new_villages,
  (SELECT count(*) FROM building_new WHERE left(dedup_key,1)='B')        AS new_b,
  (SELECT count(*) FROM building_new WHERE left(dedup_key,1)='A')        AS new_a,
  (SELECT count(*) FROM building_new n
     WHERE NOT EXISTS (SELECT 1 FROM building o WHERE o.dedup_key=n.dedup_key)) AS only_new,
  (SELECT count(*) FROM building o
     WHERE NOT EXISTS (SELECT 1 FROM building_new n WHERE n.dedup_key=o.dedup_key)) AS only_old,
  (SELECT count(*) FROM building_new
     WHERE geocode_addr IS NULL OR btrim(geocode_addr)='')               AS empty_geocode,
  (SELECT count(*) FROM building_new
     WHERE geocode_addr ~* 'квартира|пәтер')                             AS geocode_with_apt;
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="подменить building на building_new (иначе только сверка)")
    ap.add_argument("--skip-fixes", action="store_true")
    args = ap.parse_args()


    with psycopg.connect(DSN, autocommit=False) as conn:
        n = conn.execute("SELECT count(*) FROM staging_person").fetchone()[0]
        print(f"staging_person: {n:,} строк")
        if n == 0:
            print("staging пуст — заливка не отработала", file=sys.stderr)
            sys.exit(1)

        if not args.skip_fixes:
            cur = conn.execute(APPLY_FIXES)
            # Коммитим СРАЗУ. Иначе падение на следующем шаге откатывает правки
            # вместе со сборкой — и пересборка молча идёт по битому сырью
            # (ровно так и вышло: "влито 9 135", потом падение, потом откат).
            conn.commit()
            print(f"влито починенных адресов в staging: {cur.rowcount:,} (закоммичено)")
        left = conn.execute(
            "SELECT count(*) FROM staging_person WHERE clean_address_ru LIKE ', %'"
        ).fetchone()[0]
        print(f"битых адресов осталось в staging: {left:,}")

        print("собираю building_new...")
        conn.execute("SET work_mem = '1GB'")
        conn.execute("SET max_parallel_workers_per_gather = 0")
        for i, step in enumerate(BUILD_STEPS, 1):
            cur = conn.execute(step)
            if "INSERT" in step:
                print(f"  шаг {i}/{len(BUILD_STEPS)}: {cur.rowcount:,} строк")
        conn.commit()

        row = conn.execute(CHECK).fetchone()
        cols = ["old_total", "new_total", "new_villages", "new_b", "new_a",
                "only_new", "only_old", "empty_geocode", "geocode_with_apt"]
        print("\n--- сверка со старой building ---")
        for c, v in zip(cols, row):
            print(f"  {c:18s} {v:,}")

        if args.apply:
            conn.execute("DROP TABLE IF EXISTS building_old_backup")
            conn.execute("ALTER TABLE building RENAME TO building_old_backup")
            conn.execute("ALTER TABLE building_new RENAME TO building")

            # Переименование НЕ переносит эти две связи: и FK, и владение
            # последовательностью держатся за OID таблицы, а не за имя, поэтому
            # молча уезжают на building_old_backup. Последствия: привязка людей
            # валидируется против СТАРЫХ id, а DROP бэкап-таблицы утаскивает
            # последовательность и ломает вставку в боевую. Чиним сразу.
            conn.execute("ALTER TABLE person DROP CONSTRAINT IF EXISTS person_building_id_fkey")
            # Подмена building обесценивает ВСЕ ссылки person.building_id: id в
            # новой таблице другие. Держать их нельзя (FK не встанет, а без FK
            # человек молча укажет на чужой дом), восстанавливать нечем — только
            # заново через link_persons.py. Поэтому чистим.
            n_person = conn.execute("SELECT count(*) FROM person").fetchone()[0]
            if n_person:
                conn.execute("TRUNCATE person")
                print(f"  person очищен ({n_person:,} строк): ссылки на старые id "
                      f"недействительны, нужен прогон link_persons.py")
            conn.execute("ALTER TABLE person ADD CONSTRAINT person_building_id_fkey "
                         "FOREIGN KEY (building_id) REFERENCES building(id)")
            conn.execute("ALTER SEQUENCE building_id_seq OWNED BY building.id")
            conn.execute("ALTER TABLE building_old_backup ALTER COLUMN id DROP DEFAULT")
            conn.execute("SELECT setval('building_id_seq', (SELECT max(id) FROM building))")
            conn.commit()

            fk = conn.execute(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conname='person_building_id_fkey'").fetchone()
            seq = conn.execute("SELECT pg_get_serial_sequence('building','id')").fetchone()
            print("\nbuilding подменён (старая осталась как building_old_backup)")
            print(f"  person FK -> {fk[0]} | sequence -> {seq[0]}")
        else:
            print("\n(сверка; для подмены запусти с --apply)")


if __name__ == "__main__":
    main()
