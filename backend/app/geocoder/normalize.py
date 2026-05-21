"""
Address normalization for Kazakhstan geocoding pipeline.

Input columns: REGNAME, RAINAME, REG_ADDRESS_STREET, REG_ADDRESS_BUILDING
Output: AddressRecord dataclass ready for geocoding.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Street type prefixes (Russian + Kazakh variants found in the dataset)
# ---------------------------------------------------------------------------
STREET_TYPE_PREFIXES = re.compile(
    r"^("
    r"ПРОСПЕКТ|ПРОСПЕКТ\.|ПРОСП\.|ПРОСП|"
    r"УЛИЦА|УЛ\.|УЛ|"
    r"ШОССЕ|Ш\.|"
    r"ПЕРЕУЛОК|ПЕР\.|ПЕР|"
    r"БУЛЬВАР|БУЛЬВ\.|БУЛ\.|"
    r"ПЛОЩАДЬ|ПЛ\.|"
    r"НАБЕРЕЖНАЯ|НАБ\.|"
    r"МИКРОРАЙОН|МКР\.|МКР|"
    r"КВАРТАЛ|КВ\.|"
    r"ТУПИК|ТУП\.|"
    r"ПРОЕЗД|ПР-ЗД\.|"
    r"КВАРTAL|"
    r"ДОРОГА|"
    r"АЛЛЕЯ"
    r")\s+",
    re.IGNORECASE,
)

# Казахские варианты типов улиц
KZ_STREET_TYPE_PREFIXES = re.compile(
    r"^("
    r"ДАҢҒЫЛ|"           # проспект
    r"КӨШЕ|"             # улица
    r"ТҰЙЫҚ КӨШ|"        # тупик
    r"ШАҒЫН АУДАН"       # микрорайон
    r")\s+",
    re.IGNORECASE,
)

# Словарь транслитерации казахских специфических букв → близкие русские/латинские
# Вариант А: І→И  (современная норма)
KZ_TRANSLIT = str.maketrans({
    "Ә": "А", "ә": "а",
    "Ғ": "Г", "ғ": "г",
    "Қ": "К", "қ": "к",
    "Ң": "Н", "ң": "н",
    "Ө": "О", "ө": "о",
    "Ұ": "У", "ұ": "у",
    "Ү": "У", "ү": "у",
    "Х": "Х", "х": "х",
    "І": "И", "і": "и",
})

# Вариант Б: І→Ы  (историческое написание казахских имён в OSM:
# Едіге→Едыге, Тұяқберді→Туякберды, Шәмелов без изменений)
KZ_TRANSLIT_ALT = str.maketrans({
    "Ә": "А", "ә": "а",
    "Ғ": "Г", "ғ": "г",
    "Қ": "К", "қ": "к",
    "Ң": "Н", "ң": "н",
    "Ө": "О", "ө": "о",
    "Ұ": "У", "ұ": "у",
    "Ү": "У", "ү": "у",
    "Х": "Х", "х": "х",
    "І": "Ы", "і": "ы",   # ← ключевое отличие
})

# Нормализация пробелов и дефисов в номерах типа «Е-51» / «Е 51»
ROAD_CODE_RE = re.compile(r"^([А-ЯA-Z])\s*[-–]\s*(\d+)$", re.IGNORECASE)


@dataclass
class AddressRecord:
    sicid: int
    city: str                        # REGNAME нормализованный
    district: str                    # RAINAME
    street_raw: str                  # оригинал REG_ADDRESS_STREET
    building_raw: str                # оригинал REG_ADDRESS_BUILDING
    street_type: str                 # ПРОСПЕКТ / УЛИЦА / ШОССЕ и т.п.
    street_name: str                 # имя без типа
    street_name_translit: str        # имя с заменёнными казахскими буквами І→И
    street_name_translit_alt: str    # то же, но І→Ы (историческое написание в OSM)
    house: str                       # нормализованный номер дома
    name_remapped: bool = False      # True если применён alias
    original_street: str = ""        # сохраняем оригинал при remapping

    @property
    def nominatim_street(self) -> str:
        """«43 Қабанбай Батыр» — формат Nominatim structured query."""
        return f"{self.house} {self.street_name}"

    @property
    def photon_query(self) -> str:
        """«Қабанбай Батыр 43, Астана» — свободный текст для Photon."""
        return f"{self.street_type} {self.street_name} {self.house}, {self.city}"


# ---------------------------------------------------------------------------
# Alias dictionary
# ---------------------------------------------------------------------------

_ALIASES: dict[tuple[str, str], tuple[str, str]] | None = None

def _load_aliases() -> dict[tuple[str, str], tuple[str, str]]:
    """Load street_aliases.csv once. Key: (city_upper, old_name_upper). Value: (current_name, street_type)."""
    aliases_path = Path(__file__).parent.parent / "data" / "street_aliases.csv"
    result: dict[tuple[str, str], tuple[str, str]] = {}
    if not aliases_path.exists():
        return result
    with aliases_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["city"].strip().upper(), row["old_name"].strip().upper())
            result[key] = (row["current_name"].strip(), row["street_type"].strip().upper())
    return result


def get_aliases() -> dict[tuple[str, str], tuple[str, str]]:
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = _load_aliases()
    return _ALIASES


# ---------------------------------------------------------------------------
# Core normalization helpers
# ---------------------------------------------------------------------------

def _clean_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _normalize_city(city: str) -> str:
    """Унифицируем написание города."""
    city = _clean_whitespace(city.upper())
    # Убираем префиксы г., г , город, с., с , село и т.п.
    city = re.sub(r"^(Г\.|Г\s+|ГОРОД\s+|С\.|С\s+|СЕЛО\s+|АУЛ\s+|П\.|П\s+|ПОС\.\s*)", "", city).strip()
    # Дефисные варианты
    city = city.replace("НУР-СУЛТАН", "АСТАНА").replace("НУР СУЛТАН", "АСТАНА")
    return city


_OBLAST_RE = re.compile(
    r"(ОБЛАСТЬ|ОБЛЫСЫ|ОБЛЫСЫ|REGION|PROVINCE)$", re.IGNORECASE
)

# Паттерны которые указывают на город в RAINAME
_CITY_FROM_RAIN_RE = re.compile(
    r"^(Г\.|Г\s+|ГОРОД\s+|ҚАЛА\s+|ҚАЛАСЫ|ҚАЛАСЫ$)",
    re.IGNORECASE,
)
_CITY_SUFFIX_RE = re.compile(r"\s+ҚАЛАСЫ$", re.IGNORECASE)


def _city_from_rainame(regname: str, rainame: str) -> str:
    """
    Если regname — область («Карагандинская область»), пытаемся извлечь
    настоящий город из rainame («г. Балхаш», «БАЛХАШ ҚАЛАСЫ», «Каркаралинский район»).
    Иначе возвращаем нормализованный regname.
    """
    reg_norm = _normalize_city(regname)

    # Если regname не похож на область — возвращаем как есть
    if not _OBLAST_RE.search(regname.strip()):
        return reg_norm

    # regname — область, ищем город в rainame
    rain_orig = _clean_whitespace(rainame)
    rain = rain_orig.upper()

    # «г. Балхаш», «ГОРОД ЖЕЗКАЗГАН», «город Балхаш»
    m = re.match(r"^(?:Г\.|Г\s+|ГОРОД\s+)([\w\s\-]+)", rain_orig, re.IGNORECASE)
    if m:
        return _normalize_city(m.group(1).strip())

    # «БАЛХАШ ҚАЛАСЫ» или «ЖЕЗҚАЗҒАН ҚАЛАСЫ»
    m2 = _CITY_SUFFIX_RE.search(rain)
    if m2:
        return _normalize_city(rain[:m2.start()].strip())

    # «Каркаралинский район» → «Каркаралинск»
    m3 = re.match(r"^(.+?)(СКИЙ|СКАЯ|СКОЕ)\s+(РАЙОН|АУДАН)$", rain)
    if m3:
        root = m3.group(1).strip()
        return _normalize_city(root + "СК")

    # «Абайский район», «Шетский» — убираем суффикс район/аудан
    m4 = re.sub(r"\s+(РАЙОН|АУДАН|DISTRICT)$", "", rain).strip()
    if m4 and m4 != rain:
        return _normalize_city(m4)

    # rainame само по себе без «район» — вероятно город
    if rain and "РАЙОН" not in rain and "АУДАН" not in rain and len(rain.split()) <= 3:
        return _normalize_city(rain_orig)

    # Не смогли — используем rainame целиком (лучше чем «Карагандинская область»)
    return _normalize_city(rain_orig) if rain_orig else reg_norm


def _normalize_building(building: str) -> str:
    """Нормализуем номер дома: убираем лишние пробелы, строчные → заглавные."""
    b = _clean_whitespace(building).upper()
    # «43 б» → «43Б», «43 /1» → «43/1»
    b = re.sub(r"(\d)\s+([А-ЯA-Z])", r"\1\2", b)
    b = re.sub(r"\s*/\s*", "/", b)
    return b


def _extract_street_type(street: str) -> tuple[str, str]:
    """
    Split «ПРОСПЕКТ Қабанбай Батыр» → ('ПРОСПЕКТ', 'Қабанбай Батыр').
    Returns (type_upper, name_without_type).
    """
    m = STREET_TYPE_PREFIXES.match(street)
    if m:
        stype = _clean_whitespace(m.group(1).upper())
        name = _clean_whitespace(street[m.end():])
        return stype, name

    m = KZ_STREET_TYPE_PREFIXES.match(street)
    if m:
        stype = _clean_whitespace(m.group(1).upper())
        name = _clean_whitespace(street[m.end():])
        return stype, name

    # Нет известного префикса — возвращаем как есть
    return "УЛИЦА", _clean_whitespace(street)


def _normalize_road_code(name: str) -> str:
    """«Е 51» / «Е-51» → «Е-51» (единый формат для alias lookup)."""
    m = ROAD_CODE_RE.match(name.strip())
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return name


# Паттерн: «ЖИЛОЙ МАССИВ ЧТО-ТО, УЛИЦА ИМЯ» или «ЖМ ЧТО-ТО, УЛИЦА ИМЯ»
# Захватываем тип улицы (УЛИЦА/ПРОСПЕКТ/...) и само название после запятой
_COMPOUND_ADDRESS_RE = re.compile(
    r"^(?:ЖИЛОЙ\s+МАССИВ|ЖИЛМАССИВ|ЖМ|ЖИЛОЙ\s+КВАРТАЛ|ЖК)"
    r"\s+[^,]+,\s*"
    r"(ПРОСПЕКТ|УЛИЦА|УЛ\.?|ПЕРЕУЛОК|ПЕР\.?|БУЛЬВАР|ШОССЕ|АЛЛЕЯ|ДАҢҒЫЛ|КӨШЕ)"
    r"\s+(.+)$",
    re.IGNORECASE,
)


def _strip_compound_prefix(street: str) -> str:
    """
    «ЖИЛОЙ МАССИВ ҮРКЕР, УЛИЦА ҮКІЛІ ЫБЫРАЙ»
    → «УЛИЦА ҮКІЛІ ЫБЫРАЙ»

    Возвращает строку без префикса жилмассива чтобы дальнейший
    _extract_street_type правильно распознал тип и имя улицы.
    """
    m = _COMPOUND_ADDRESS_RE.match(street.strip())
    if m:
        stype = m.group(1).strip()
        sname = m.group(2).strip()
        return f"{stype} {sname}"
    return street


def _to_translit(name: str) -> str:
    """Replace Kazakh-specific chars for fuzzy matching (І→И)."""
    return name.translate(KZ_TRANSLIT)


def _to_translit_alt(name: str) -> str:
    """Replace Kazakh-specific chars — historical variant (І→Ы)."""
    return name.translate(KZ_TRANSLIT_ALT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_row(
    sicid: int | str,
    regname: str,
    rainame: str,
    street_raw: str,
    building_raw: str,
) -> AddressRecord:
    """
    Convert a raw row from the xlsx into a structured AddressRecord.
    Applies alias remapping before returning.
    """
    city = _city_from_rainame(str(regname), str(rainame))
    district = _clean_whitespace(str(rainame))
    street_clean = _clean_whitespace(str(street_raw)).upper()
    building = _normalize_building(str(building_raw))

    street_clean = _strip_compound_prefix(street_clean)
    street_type, street_name = _extract_street_type(street_clean)

    # Normalize road codes like «Е 51» → «Е-51»
    street_name_norm = _normalize_road_code(street_name)

    # Alias lookup
    aliases = get_aliases()
    lookup_key = (city, street_name_norm.upper())
    name_remapped = False
    original_street = ""

    if lookup_key in aliases:
        current_name, mapped_type = aliases[lookup_key]
        original_street = street_name_norm
        street_name_norm = current_name
        street_type = mapped_type
        name_remapped = True
    else:
        # Try without road code normalization (raw upper)
        lookup_key2 = (city, street_name.strip().upper())
        if lookup_key2 in aliases:
            current_name, mapped_type = aliases[lookup_key2]
            original_street = street_name
            street_name_norm = current_name
            street_type = mapped_type
            name_remapped = True

    return AddressRecord(
        sicid=int(sicid),
        city=city,
        district=district,
        street_raw=str(street_raw),
        building_raw=str(building_raw),
        street_type=street_type,
        street_name=street_name_norm,
        street_name_translit=_to_translit(street_name_norm),
        street_name_translit_alt=_to_translit_alt(street_name_norm),
        house=building,
        name_remapped=name_remapped,
        original_street=original_street,
    )
