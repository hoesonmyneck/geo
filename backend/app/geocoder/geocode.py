"""
Hybrid geocoder: Nominatim → Photon → old_name_index → fuzzy

Resolution order per AddressRecord:
  1. Cache hit
  2. Nominatim structured query
  3. Photon free-text fallback
  4. old_name_index lookup in Nominatim Postgres (if psycopg available)
  5. rapidfuzz match against city street list
  6. miss
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .cache import CacheEntry, get as cache_get, make_hash, put as cache_put
from .normalize import AddressRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (overridable via environment variables)
# ---------------------------------------------------------------------------
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "http://localhost:8080")
PHOTON_URL = os.getenv("PHOTON_URL", "http://localhost:2322")
POSTGRES_DSN = os.getenv(
    "NOMINATIM_PG_DSN",
    "host=localhost port=5432 dbname=nominatim user=nominatim password=nominatim",
)
FUZZY_THRESHOLD = int(os.getenv("FUZZY_THRESHOLD", "85"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# City coordinates for Photon location bias
# Добавляй новые города по мере расширения на 20M записей
# ---------------------------------------------------------------------------
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "АСТАНА":    (51.1801, 71.4460),
    "АЛМАТЫ":   (43.2220, 76.8512),
    "ШЫМКЕНТ":  (42.3000, 69.5900),
    "ҚАРАҒАНДЫ": (49.8028, 73.1028),
    "АКТОБЕ":   (50.2839, 57.1669),
    "ТАРАЗ":    (42.9000, 71.3667),
    "ПАВЛОДАР":  (52.2873, 76.9674),
    "УСТЬ-КАМЕНОГОРСК": (49.9483, 82.6286),
    "СЕМЕЙ":    (50.4114, 80.2272),
    "АТЫРАУ":   (47.1167, 51.8833),
    "КОСТАНАЙ":  (53.2144, 63.6240),
    "КЫЗЫЛОРДА": (44.8527, 65.5090),
    "УРАЛЬСК":  (51.2333, 51.3667),
    "ПЕТРОПАВЛОВСК": (54.8667, 69.1500),
    "АКТАУ":    (43.6522, 51.1575),
    "ТУРКЕСТАН": (43.2975, 68.2675),
    "КОКШЕТАУ": (53.2844, 69.3958),
    "ТАЛДЫКОРГАН": (45.0175, 78.3730),
    "ЭКИБАСТУЗ": (51.7200, 75.3241),
    "ТЕМИРТАУ":  (50.0578, 72.9550),
    "КАРАГАНДA":      (49.8028, 73.1028),   # русское написание
    "БАЛХАШ":         (46.8483, 74.9949),
    "ЖЕЗҚАЗҒАН":      (47.7974, 67.7115),
    "ЖЕЗКАЗГАН":      (47.7974, 67.7115),
    "САТПАЕВ":        (47.9021, 67.5289),
    "КАРКАРАЛИНСК":   (49.4231, 75.4688),
    "ПРИОЗЕРСК":      (46.0612, 73.8523),
    "АБАЙ":           (49.6333, 72.8500),
    "АБАЙСК":         (49.6333, 72.8500),
    "ШАХТИНСК":       (49.7167, 72.5833),
    "САРАНЬ":         (49.8024, 72.8699),
    "БУХАР-ЖЫРАУСК":  (49.8500, 72.6000),
    "ОСАКАРОВСК":     (50.5950, 72.5470),
    "ШЕТСК":          (50.1800, 73.8500),
    "НУРИНСК":        (50.7000, 71.3000),
    "АКТОГАЙСК":      (48.5800, 76.7200),
}

# ---------------------------------------------------------------------------
# Bounding boxes городов (lat_min, lat_max, lon_min, lon_max)
# Результат, попавший за пределы bbox, отбрасывается как «чужой город».
# Поля взяты с запасом ~15-20 км от административной границы.
# ---------------------------------------------------------------------------
_CITY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "АСТАНА":          (50.75, 51.50, 71.00, 72.10),
    "АЛМАТЫ":          (42.80, 43.55, 76.50, 77.20),
    "ШЫМКЕНТ":         (41.90, 42.65, 69.25, 70.10),
    "ҚАРАҒАНДЫ":       (49.45, 50.15, 72.65, 73.65),
    "АКТОБЕ":          (49.95, 50.65, 56.75, 57.75),
    "ТАРАЗ":           (42.50, 43.25, 70.90, 71.85),
    "ПАВЛОДАР":        (51.90, 52.70, 76.55, 77.45),
    "УСТЬ-КАМЕНОГОРСК":(49.55, 50.35, 82.20, 83.15),
    "СЕМЕЙ":           (50.05, 50.80, 79.85, 80.65),
    "АТЫРАУ":          (46.75, 47.50, 51.50, 52.20),
    "КОСТАНАЙ":        (52.80, 53.65, 63.25, 64.15),
    "КЫЗЫЛОРДА":       (44.45, 45.25, 65.10, 66.05),
    "УРАЛЬСК":         (50.85, 51.65, 50.95, 51.85),
    "ПЕТРОПАВЛОВСК":   (54.45, 55.25, 68.70, 69.65),
    "АКТАУ":           (43.25, 44.05, 50.80, 51.60),
    "ТУРКЕСТАН":       (42.90, 43.65, 67.85, 68.70),
    "КОКШЕТАУ":        (52.90, 53.70, 68.95, 69.90),
    "ТАЛДЫКОРГАН":     (44.60, 45.40, 77.95, 78.80),
    "ЭКИБАСТУЗ":       (51.35, 52.10, 74.95, 75.85),
    "ТЕМИРТАУ":        (49.65, 50.45, 72.55, 73.40),
    "КАРАГАНДA":       (49.45, 50.15, 72.65, 73.65),   # русское написание (А кириллическая)
    "БАЛХАШ":          (46.55, 47.15, 74.60, 75.35),
    "ЖЕЗҚАЗҒАН":       (47.50, 48.10, 67.35, 68.15),
    "ЖЕЗКАЗГАН":       (47.50, 48.10, 67.35, 68.15),
    "САТПАЕВ":         (47.65, 48.20, 67.25, 67.90),
    "КАРКАРАЛИНСК":    (49.10, 49.75, 75.00, 75.90),
    "ПРИОЗЕРСК":       (45.70, 46.40, 73.50, 74.25),
    "АБАЙ":            (49.45, 49.85, 72.65, 73.15),
    "АБАЙСК":          (49.45, 49.85, 72.65, 73.15),
    "ШАХТИНСК":        (49.55, 49.90, 72.35, 72.80),
    "САРАНЬ":          (49.60, 49.95, 72.65, 73.00),
    # Сельские районы Карагандинской области
    "БУХАР-ЖЫРАУСК":   (49.35, 50.35, 71.80, 73.50),
    "ОСАКАРОВСК":      (50.35, 51.00, 72.10, 73.30),
    "ШЕТСК":           (49.80, 50.70, 73.30, 75.00),
    "НУРИНСК":         (50.10, 51.50, 69.80, 72.20),
    "АКТОГАЙСК":       (47.80, 49.50, 75.60, 78.80),
}


_LATIN_TO_CYR = str.maketrans("ABCEHKMOPTXabekmopx", "АВСЕНКМОРТХавекморх")

def _normalize_city_key(city: str) -> str:
    """Нормализуем имя города: uppercase + замена латинских омоглифов на кириллицу."""
    return city.upper().translate(_LATIN_TO_CYR)


# Пересобираем bbox словарь с нормализованными ключами (на случай смешанной кодировки)
_CITY_BBOX_NORM: dict[str, tuple[float, float, float, float]] = {
    _normalize_city_key(k): v for k, v in _CITY_BBOX.items()
}
# Дополнительные алиасы: Nominatim может вернуть разные написания
for _alias, _target in [
    ("КАРАГАНДA",  "ҚАРАҒАНДЫ"),
    ("КARАГАНДА",  "ҚАРАҒАНДЫ"),
    ("KARAGANDA",  "ҚАРАҒАНДЫ"),
    ("АСТАНА",     "АСТАНА"),
]:
    _norm_alias = _normalize_city_key(_alias)
    _norm_target = _normalize_city_key(_target)
    if _norm_target in _CITY_BBOX_NORM and _norm_alias not in _CITY_BBOX_NORM:
        _CITY_BBOX_NORM[_norm_alias] = _CITY_BBOX_NORM[_norm_target]


def _in_city_bbox(lat: float, lon: float, city: str) -> bool:
    """Возвращает True если координаты находятся в пределах bbox города."""
    key = _normalize_city_key(city)
    bbox = _CITY_BBOX_NORM.get(key)
    if bbox is None:
        return True  # неизвестный город — не фильтруем
    lat_min, lat_max, lon_min, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


# Photon типы которые принимаем как «найдена улица»
_PHOTON_STREET_TYPES = {"street", "road", "locality", "district", "city"}

# ---------------------------------------------------------------------------
# Nominatim genitive retry helpers
# ---------------------------------------------------------------------------
_CONSONANTS_RU = set("БВГДЖЗЙКЛМНПРСТФХЦЧШЩ")

def _word_to_genitive(word: str) -> str:
    """
    Склоняет одно слово в родительный падеж (русский).

    Мужской род:
      Мухамедханов → Мухамедханова   (-ОВ → -ОВА)
      Молдағалиев  → Молдағалиева    (-ЕВ → -ЕВА)
      Кайым        → Кайыма          (согласная → +А)
      Казахский    → Казахского      (-ИЙ/-ЫЙ → -ОГО)

    Женский род:
      Мәметова     → Мәметовой       (-ОВА/-ЕВА → -ОВОЙ/-ЕВОЙ)
      Жаманова     → Жамановой
      Роза         → Розы            (-А после согласной → -Ы)
    """
    u = word.upper()
    if len(u) <= 2:
        return word
    # Женские фамилии: -ОВА / -ЕВА → -ОВОЙ / -ЕВОЙ
    if u.endswith("ОВА") or u.endswith("ЕВА"):
        return word[:-1] + "ой"
    # Мужские фамилии: -ОВ / -ЕВ / -ЁВ → +А
    if u.endswith("ОВ") or u.endswith("ЕВ") or u.endswith("ЁВ"):
        return word + "а"
    # -ИН / -ЫН → +А (Сталин → Сталина)
    if u.endswith("ИН") or u.endswith("ЫН"):
        return word + "а"
    # -ИЙ / -ЫЙ → -ОГО (прилагательные)
    if u.endswith("ИЙ") or u.endswith("ЫЙ"):
        return word[:-2] + "ого"
    # -АЯ / -ЯЯ → -ОЙ (женские прилагательные)
    if u.endswith("АЯ") or u.endswith("ЯЯ"):
        return word[:-2] + "ой"
    # Женские имена на -А → -Ы (Роза → Розы, Мария → Марии handled separately)
    if u.endswith("А") and len(u) > 3 and u[-2] not in "АЕЁИОУЫЭЮЯаеёиоуыэюя":
        return word[:-1] + "ы"
    # Согласная → +А
    if u[-1] in _CONSONANTS_RU:
        return word + "а"
    return word  # не меняем


# Слова-признаки: название уже в родительном падеже — не склоняем повторно
_ALREADY_GENITIVE_MARKERS = re.compile(
    r"^(АКАДЕМИКА|ГЕНЕРАЛА|МАРШАЛА|ПРОФЕССОРА|ДОКТОРА|ГЕРОЯ|ИМЕНИ)\b",
    re.IGNORECASE,
)


def _to_genitive(name: str) -> str:
    """
    Эвристика: все слова → родительный падеж (для улиц-имён людей).

    Кайым Мухамедханов  → Кайыма Мухамедханова
    Жұбан Молдағалиев   → Жұбана Молдағалиева
    Мәншүк Мәметова     → Мәншүка Мәметовой
    Роза Жаманова       → Розы Жамановой

    Не трогает названия уже в родительном:
      Академика Рамазана Сулейменова → без изменений
    """
    if not name:
        return name
    if _ALREADY_GENITIVE_MARKERS.match(name.strip()):
        return name
    words = name.split()
    converted = [_word_to_genitive(w) for w in words]
    result = " ".join(converted)
    return result if result != name else name


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class GeoResult:
    sicid: int
    lat: Optional[float]
    lon: Optional[float]
    confidence: str   # high | medium | low | miss
    source: str       # nominatim | photon | old_name | fuzzy | cache | miss
    name_remapped: bool
    original_street: str
    street_used: str
    house_used: str
    city: str
    raw_osm_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Nominatim client
# ---------------------------------------------------------------------------

def _nominatim_confidence(data: dict, expected_house: str) -> str:
    """
    Determine confidence from Nominatim jsonv2 response.
    jsonv2 uses 'category'+'type' instead of 'addresstype'.
    Key signal: address.house_number match.
    """
    address = data.get("address", {})
    house_number = address.get("house_number", "")
    category = data.get("category", "")
    place_type = data.get("type", "")
    osm_type = data.get("osm_type", "")

    # House number match is the strongest signal — regardless of place type
    if house_number and _house_matches(house_number, expected_house):
        return "high"

    # Road/street found (centre of street) — medium
    if category == "highway" or place_type in {
        "residential", "primary", "secondary", "tertiary",
        "unclassified", "trunk", "pedestrian", "living_street",
        "road", "street",
    }:
        return "medium"

    # Something was found but no house match
    if osm_type in {"node", "way", "relation"} and address:
        return "low"

    return "low"


def _house_matches(found: str, expected: str) -> bool:
    """Compare house numbers tolerantly.

    Handles:
      - case/Cyrillic-Latin (43Б == 43б == 43B)
      - slash notation (4/4 == 4/4, 4/4 found when expected 4/4)
      - base-only match (OSM stores "4", we searched for "4/4")
    """
    if not found or not expected:
        return False
    f = found.strip().upper()
    e = expected.strip().upper()
    if f == e:
        return True
    # Если в ожидаемом есть "/", сравниваем и базовую часть
    if "/" in e:
        e_base = e.split("/")[0].strip()
        if f == e_base:
            return True
        # "4-4" == "4/4"
        if f == e.replace("/", "-"):
            return True
    if "/" in f:
        f_base = f.split("/")[0].strip()
        if f_base == e:
            return True
    return False


def _house_query_variants(house: str) -> list[str]:
    """Варианты номера дома для запросов к Nominatim.

    Для "4/4" генерирует: ["4/4", "4", "4-4"]
    Для "4В"  генерирует: ["4В"]
    """
    variants: list[str] = [house]
    if "/" in house:
        parts = house.split("/", 1)
        base   = parts[0].strip()
        suffix = parts[1].strip() if len(parts) > 1 else ""
        if base and base not in variants:
            variants.append(base)
        if base and suffix:
            hyph = f"{base}-{suffix}"
            if hyph not in variants:
                variants.append(hyph)
    return variants


async def _nominatim_get(
    client: httpx.AsyncClient,
    params: dict,
    sicid: int,
) -> Optional[dict]:
    """Single Nominatim HTTP call with retries."""
    try:
        resp = await client.get(
            f"{NOMINATIM_URL}/search",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        return results[0] if results else None
    except Exception as e:
        logger.debug("Nominatim error sicid=%s: %s", sicid, e)
        return None


async def _nominatim_query(client: httpx.AsyncClient, record: AddressRecord) -> Optional[dict]:
    """
    Structured search via Nominatim API.

    Pass 1 — оригинал (как в реестре):          «ТҰРАН», «ӘНЕТ БАБА»
    Pass 2 — транслитерация (Ұ→У, Ә→А, Қ→К):   «ТУРАН», «АНЕТ БАБА»
    Pass 3 — родительный падеж:                  «КАЙЫМа МУХАМЕДХАНОВа»
    Pass 4 — родительный + транслит (І→И):       «КАЙЫМа МУХАМЕДХАНОВа»
    Pass 5 — транслит альт (І→Ы):                «ЕДЫГЕ», «ТУЯКБЕРДЫ»
    Pass 6 — родительный + транслит альт (І→Ы)
    Pass 7 — street-only (без номера, только highway).
              Нужен когда passes 1-6 находят здание с чужим номером или POI.
    """
    city_cap = record.city.capitalize()
    base: dict = {"city": city_cap, "country": "Kazakhstan",
                  "format": "jsonv2", "addressdetails": 1, "limit": 1}

    # Ограничиваем поиск bbox города чтобы Nominatim не возвращал однофамильцев
    # из других городов. viewbox = lon_min,lat_max,lon_max,lat_min (W,N,E,S)
    bbox = _CITY_BBOX.get(record.city.upper())
    if bbox:
        lat_min, lat_max, lon_min, lon_max = bbox
        base["viewbox"] = f"{lon_min},{lat_max},{lon_max},{lat_min}"
        base["bounded"] = 1

    genitive_name         = _to_genitive(record.street_name)
    genitive_translit     = _to_genitive(record.street_name_translit)
    # Исторический вариант: І→Ы (напр. «Едіге»→«Едыге», «Тұяқберді»→«Туякберды»)
    translit_alt          = record.street_name_translit_alt
    genitive_translit_alt = _to_genitive(translit_alt)

    # Passes 1-N: с номером дома (ищем exact house или саму улицу).
    # Для номеров с "/" (напр. "4/4") пробуем также "4" и "4-4".
    street_names = list(dict.fromkeys([
        record.street_name,
        record.street_name_translit,
        genitive_name,
        genitive_translit,
        translit_alt,
        genitive_translit_alt,
    ]))
    house_variants = _house_query_variants(record.house)

    street_fallback: Optional[dict] = None  # highway/road result as last resort
    for house_q in house_variants:
        for name in street_names:
            if not name:
                continue
            house_street = f"{house_q} {name}".strip()
            data = await _nominatim_get(client, {**base, "street": house_street}, record.sicid)
            if data:
                addr_house = data.get("address", {}).get("house_number", "")
                if _house_matches(addr_house, record.house):
                    # Точное совпадение по номеру дома — сразу возвращаем
                    return data
                # Улица (highway) найдена — сохраняем как запасной вариант,
                # но продолжаем искать дом (не возвращаем из пассов с номером дома)
                if (data.get("category") == "highway"
                        or data.get("type") in {
                            "residential", "primary", "secondary", "tertiary",
                            "unclassified", "trunk", "pedestrian", "living_street",
                            "road", "service",
                        }):
                    if street_fallback is None:
                        street_fallback = data
                    continue  # продолжаем искать дом
                # POI (ресторан, колледж и т.п.) — запомним как fallback
                poi_fallback = data

    # Pass 7: только улица (без номера), принимаем только highway
    # Если уже нашли highway в пассах с домом — используем его
    if street_fallback:
        logger.debug("Street-only fallback from house passes sicid=%s", record.sicid)
        return street_fallback

    for name in dict.fromkeys([
        record.street_name,
        record.street_name_translit,
        genitive_name,
        genitive_translit,
        translit_alt,
        genitive_translit_alt,
    ]):
        if not name:
            continue
        data = await _nominatim_get(client, {**base, "street": name}, record.sicid)
        if data and (
            data.get("category") == "highway"
            or data.get("type") in {
                "residential", "primary", "secondary", "tertiary",
                "unclassified", "trunk", "pedestrian", "living_street",
                "road", "service",
            }
        ):
            logger.debug("Street-only pass found sicid=%s via %r", record.sicid, name)
            return data

    # POI-результаты (рестораны, колледжи и т.п.) не возвращаем —
    # их координаты не имеют отношения к искомой улице.
    return None


# ---------------------------------------------------------------------------
# Photon client
# ---------------------------------------------------------------------------

def _photon_confidence(props: dict, expected_house: str) -> str:
    osm_type = props.get("osm_type", "")
    house_number = props.get("housenumber", "")
    osm_value = props.get("type", "")

    if house_number and _house_matches(house_number, expected_house):
        return "high"
    if osm_value in {"road", "street"} or osm_type == "W":
        return "medium"
    return "low"


async def _photon_get(
    client: httpx.AsyncClient,
    params: dict,
    sicid: int,
) -> list[dict]:
    """Single Photon HTTP call, returns feature list."""
    try:
        resp = await client.get(
            f"{PHOTON_URL}/api",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("features", [])
    except Exception as e:
        logger.debug("Photon error sicid=%s: %s", sicid, e)
        return []


def _photon_exact_match(feats: list[dict], expected_house: str) -> Optional[dict]:
    """Возвращает первый результат с точным совпадением номера дома (без base-match)."""
    for feat in feats:
        h = feat.get("properties", {}).get("housenumber", "")
        if h and h.strip().upper() == expected_house.strip().upper():
            return feat
    # Разрешаем "4-4" == "4/4" и обратный slash
    for feat in feats:
        h = feat.get("properties", {}).get("housenumber", "").strip().upper()
        e = expected_house.strip().upper()
        if h and (h == e.replace("/", "-") or h.replace("/", "-") == e):
            return feat
    return None


def _photon_any_match(feats: list[dict], expected_house: str) -> Optional[dict]:
    """Возвращает первый результат с любым совпадением (включая base-match)."""
    for feat in feats:
        props = feat.get("properties", {})
        if _house_matches(props.get("housenumber", ""), expected_house):
            return feat
    return None


async def _photon_query(client: httpx.AsyncClient, record: AddressRecord) -> Optional[dict]:
    """
    Free-text search via Photon API с location bias.

    Pass 1a: запрос с оригинальным именем (казахский) — ищем точный номер дома.
    Pass 1b: запрос с транслитом — ищем точный номер дома.
    Pass 1c: любое совпадение из Pass 1a/1b (base-match как fallback).
    Pass 2:  только улица (без номера), принимаем только street/road типы.
    """
    coords = _CITY_COORDS.get(record.city.upper(), ())
    base_params: dict = {"lang": "ru", "limit": 5}
    if coords:
        base_params["lat"] = str(coords[0])
        base_params["lon"] = str(coords[1])

    # Pass 1a — оригинальное (казахское) название
    feats_orig = await _photon_get(client, {**base_params, "q": record.photon_query}, record.sicid)
    exact = _photon_exact_match(feats_orig, record.house)
    if exact:
        return exact

    # Pass 1b — транслитерированное название (латинизированный вариант)
    translit_query = f"{record.street_type} {record.street_name_translit} {record.house}, {record.city}"
    if translit_query != record.photon_query:
        feats_translit = await _photon_get(client, {**base_params, "q": translit_query}, record.sicid)
        exact = _photon_exact_match(feats_translit, record.house)
        if exact:
            return exact
        # Запомним для pass 1c
    else:
        feats_translit = []

    # Pass 1c — допускаем base-match из обоих наборов
    match = _photon_any_match(feats_orig, record.house) or _photon_any_match(feats_translit, record.house)
    if match:
        return match

    # Pass 2 — только улица (без номера), принимаем только street/road
    street_query = f"{record.street_type} {record.street_name}, {record.city}"
    feats2 = await _photon_get(client, {**base_params, "q": street_query, "limit": 3}, record.sicid)
    for feat in feats2:
        props = feat.get("properties", {})
        if props.get("type", "") in _PHOTON_STREET_TYPES:
            return feat

    # Если Pass 1a нашёл street/road — вернём лучший
    for feat in feats_orig:
        props = feat.get("properties", {})
        if props.get("type", "") in _PHOTON_STREET_TYPES:
            return feat

    return None


# ---------------------------------------------------------------------------
# old_name_index lookup (Postgres direct)
# ---------------------------------------------------------------------------

_pg_conn = None
_pg_lock: asyncio.Lock | None = None
_pg_available = True  # set to False if psycopg not installed or connection fails


async def _ensure_pg():
    global _pg_conn, _pg_lock, _pg_available
    if not _pg_available:
        return None
    if _pg_lock is None:
        _pg_lock = asyncio.Lock()
    try:
        import psycopg  # noqa: PLC0415
        async with _pg_lock:
            if _pg_conn is None or _pg_conn.closed:
                _pg_conn = await psycopg.AsyncConnection.connect(POSTGRES_DSN)
        return _pg_conn
    except Exception as e:
        logger.warning("Postgres not available, disabling old_name lookup: %s", e)
        _pg_available = False
        return None


async def _old_name_lookup(street_name: str, city: str) -> Optional[tuple[float, float]]:
    """Query the old_name_index table built by sql/build_old_name_index.sql."""
    conn = await _ensure_pg()
    if conn is None:
        return None
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT lat, lon FROM old_name_index
                WHERE lower(old_name) = lower(%s)
                LIMIT 1
                """,
                (street_name,),
            )
            row = await cur.fetchone()
            return (row[0], row[1]) if row else None
    except Exception as e:
        logger.warning("old_name_index query failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Fuzzy fallback
# ---------------------------------------------------------------------------

_street_index: dict[str, list[tuple[str, float, float]]] = {}  # city → [(name, lat, lon)]


def load_street_index(city: str) -> list[tuple[str, float, float]]:
    """Load pre-exported street list from data/streets_index/{city}.jsonl."""
    if city in _street_index:
        return _street_index[city]

    index_path = Path(__file__).parent.parent / "data" / "streets_index" / f"{city}.jsonl"
    if not index_path.exists():
        _street_index[city] = []
        return []

    entries: list[tuple[str, float, float]] = []
    with index_path.open(encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            entries.append((obj["name"], obj["lat"], obj["lon"]))
    _street_index[city] = entries
    return entries


async def _fuzzy_lookup(
    street_name: str,
    city: str,
) -> Optional[tuple[float, float, str]]:
    """Return (lat, lon, matched_name) or None if no match above threshold."""
    try:
        from rapidfuzz import process, fuzz  # noqa: PLC0415
    except ImportError:
        logger.warning("rapidfuzz not installed, skipping fuzzy fallback")
        return None

    entries = load_street_index(city)
    if not entries:
        return None

    choices = [e[0] for e in entries]
    match = process.extractOne(
        street_name,
        choices,
        scorer=fuzz.token_set_ratio,
        score_cutoff=FUZZY_THRESHOLD,
    )
    if match is None:
        return None

    matched_name, score, idx = match
    lat, lon = entries[idx][1], entries[idx][2]
    logger.debug("fuzzy match: %r → %r (score=%d)", street_name, matched_name, score)
    return lat, lon, matched_name


# ---------------------------------------------------------------------------
# Main geocode function
# ---------------------------------------------------------------------------

async def geocode(
    record: AddressRecord,
    client: httpx.AsyncClient,
) -> GeoResult:
    """
    Full resolution pipeline for a single AddressRecord.
    Returns GeoResult with best available coordinates and confidence level.
    """
    addr_hash = make_hash(record.street_name, record.house, record.city)

    # 1. Cache
    cached = cache_get(addr_hash)
    if cached:
        return GeoResult(
            sicid=record.sicid,
            lat=cached.lat,
            lon=cached.lon,
            confidence=cached.confidence,
            source="cache",
            name_remapped=record.name_remapped,
            original_street=record.original_street,
            street_used=record.street_name,
            house_used=record.house,
            city=record.city,
        )

    # 2. Nominatim
    nom_data = await _nominatim_query(client, record)
    if nom_data:
        nom_lat = float(nom_data["lat"])
        nom_lon = float(nom_data["lon"])
        if not _in_city_bbox(nom_lat, nom_lon, record.city):
            logger.debug(
                "Nominatim result outside city bbox sicid=%s (%.4f, %.4f), city=%s",
                record.sicid, nom_lat, nom_lon, record.city,
            )
            nom_data = None  # отбрасываем — другой город

    if nom_data:
        confidence = _nominatim_confidence(nom_data, record.house)
        if confidence == "high":
            result = GeoResult(
                sicid=record.sicid,
                lat=float(nom_data["lat"]),
                lon=float(nom_data["lon"]),
                confidence="high",
                source="nominatim",
                name_remapped=record.name_remapped,
                original_street=record.original_street,
                street_used=record.street_name,
                house_used=record.house,
                city=record.city,
                raw_osm_id=str(nom_data.get("osm_id", "")),
            )
            _save_to_cache(addr_hash, result, json.dumps(nom_data))
            return result

        # Keep nominatim result as fallback but try Photon first
        nom_fallback = (
            float(nom_data["lat"]),
            float(nom_data["lon"]),
            confidence,
            json.dumps(nom_data),
            str(nom_data.get("osm_id", "")),
        )
    else:
        nom_fallback = None

    # 3. Photon
    photon_data = await _photon_query(client, record)
    if photon_data:
        coords = photon_data.get("geometry", {}).get("coordinates", [None, None])
        if len(coords) == 2 and coords[0] is not None:
            ph_lat, ph_lon = float(coords[1]), float(coords[0])
            if not _in_city_bbox(ph_lat, ph_lon, record.city):
                logger.debug(
                    "Photon result outside city bbox sicid=%s (%.4f, %.4f), city=%s",
                    record.sicid, ph_lat, ph_lon, record.city,
                )
                photon_data = None  # отбрасываем — другой город

    if photon_data:
        props = photon_data.get("properties", {})
        coords = photon_data.get("geometry", {}).get("coordinates", [None, None])
        if len(coords) == 2 and coords[0] is not None:
            ph_confidence = _photon_confidence(props, record.house)
            if ph_confidence == "high":
                result = GeoResult(
                    sicid=record.sicid,
                    lat=float(coords[1]),
                    lon=float(coords[0]),
                    confidence="high",
                    source="photon",
                    name_remapped=record.name_remapped,
                    original_street=record.original_street,
                    street_used=record.street_name,
                    house_used=record.house,
                    city=record.city,
                )
                _save_to_cache(addr_hash, result, json.dumps(photon_data))
                return result

    # 4. old_name_index (Postgres)
    old_name_coords = await _old_name_lookup(record.street_name, record.city)
    if old_name_coords:
        o_lat, o_lon = old_name_coords[0], old_name_coords[1]
        if _in_city_bbox(o_lat, o_lon, record.city):
            result = GeoResult(
                sicid=record.sicid,
                lat=o_lat,
                lon=o_lon,
                confidence="medium",
                source="old_name",
                name_remapped=record.name_remapped,
                original_street=record.original_street,
                street_used=record.street_name,
                house_used=record.house,
                city=record.city,
            )
            _save_to_cache(addr_hash, result, json.dumps({"old_name_lookup": record.street_name}))
            return result

    # 5. Fuzzy fallback
    fuzzy = await _fuzzy_lookup(record.street_name, record.city)
    if fuzzy is None and record.street_name_translit != record.street_name:
        fuzzy = await _fuzzy_lookup(record.street_name_translit, record.city)
    if fuzzy and _in_city_bbox(fuzzy[0], fuzzy[1], record.city):
        result = GeoResult(
            sicid=record.sicid,
            lat=fuzzy[0],
            lon=fuzzy[1],
            confidence="low",
            source="fuzzy",
            name_remapped=record.name_remapped,
            original_street=record.original_street,
            street_used=fuzzy[2],
            house_used=record.house,
            city=record.city,
        )
        _save_to_cache(addr_hash, result, json.dumps({"fuzzy_match": fuzzy[2]}))
        return result

    # 6. Use best Nominatim/Photon result we have (medium/low) rather than full miss
    if nom_fallback:
        lat, lon, conf, raw_json, osm_id = nom_fallback
        result = GeoResult(
            sicid=record.sicid,
            lat=lat,
            lon=lon,
            confidence=conf,
            source="nominatim",
            name_remapped=record.name_remapped,
            original_street=record.original_street,
            street_used=record.street_name,
            house_used=record.house,
            city=record.city,
            raw_osm_id=osm_id,
        )
        _save_to_cache(addr_hash, result, raw_json)
        return result

    if photon_data:
        coords = photon_data.get("geometry", {}).get("coordinates", [None, None])
        if len(coords) == 2 and coords[0] is not None:
            props = photon_data.get("properties", {})
            ph_conf = _photon_confidence(props, record.house)
            # Если Photon нашёл только POI (не улицу, не дом с совпадением) — miss
            if ph_conf == "low":
                ph_conf = "miss"
            result = GeoResult(
                sicid=record.sicid,
                lat=float(coords[1]) if ph_conf != "miss" else None,
                lon=float(coords[0]) if ph_conf != "miss" else None,
                confidence=ph_conf,
                source="photon" if ph_conf != "miss" else "miss",
                name_remapped=record.name_remapped,
                original_street=record.original_street,
                street_used=record.street_name,
                house_used=record.house,
                city=record.city,
            )
            _save_to_cache(addr_hash, result, json.dumps(photon_data))
            return result

    # 7. Miss
    result = GeoResult(
        sicid=record.sicid,
        lat=None,
        lon=None,
        confidence="miss",
        source="miss",
        name_remapped=record.name_remapped,
        original_street=record.original_street,
        street_used=record.street_name,
        house_used=record.house,
        city=record.city,
    )
    _save_to_cache(addr_hash, result, "{}")
    return result


def _save_to_cache(addr_hash: str, result: GeoResult, raw_json: str) -> None:
    cache_put(CacheEntry(
        addr_hash=addr_hash,
        lat=result.lat or 0.0,
        lon=result.lon or 0.0,
        confidence=result.confidence,
        source=result.source,
        json_blob=raw_json,
    ))
