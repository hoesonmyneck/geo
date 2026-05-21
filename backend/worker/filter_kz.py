"""
Фильтрует districts_l4.geojson и districts_l68.geojson —
оставляет только объекты внутри Казахстана.

Метод: point-in-polygon (ray casting) по центру bbox каждого объекта.
Запуск: docker compose exec worker python /app/worker/filter_kz.py
"""
import gzip, json
from pathlib import Path

OUTPUT = Path("/geo-data")

# Полигон границы Казахстана (lon, lat), обход по часовой стрелке.
# Ключевые исправления по сравнению с предыдущей версией:
#   • Северная граница поднята до 55.4°N (lon 65–70°E) — покрывает
#     Костанайскую, Акмолинскую, СКО
#   • Южная граница поднята до 42–42.5°N на участке lon 60–67°E —
#     исключает узбекские Бухарскую, Навоийскую, Хорезмскую вилояты
KZ_POLY = [
    # Северная граница (Россия), запад → восток
    (50.27, 51.28),
    (51.20, 51.70),
    (52.07, 52.00),
    (53.01, 51.56),
    (54.12, 51.10),
    (55.80, 50.75),
    (57.84, 51.07),
    (59.00, 51.80),
    (61.00, 53.60),   # резкий подъём — Челябинская область России граничит здесь
    (62.50, 54.40),
    (64.00, 55.10),
    (65.50, 55.45),   # САМАЯ СЕВЕРНАЯ ТОЧКА КЗ (~55.4°N, Тюменская обл. РФ)
    (68.00, 55.20),
    (70.00, 55.20),   # Петропавловск (СКО) здесь — 54.9°N
    (72.50, 55.00),
    (75.00, 54.50),
    (76.26, 54.32),
    (76.58, 53.96),
    (77.80, 53.40),
    (79.38, 52.85),
    (80.52, 52.24),
    (81.47, 51.67),
    (82.49, 51.26),
    (83.13, 50.89),
    (83.38, 50.35),   # северо-восточный угол, далее — граница с Китаем
    # Восточная граница (Китай), север → юг
    (83.17, 49.80),
    (82.96, 49.10),
    (82.42, 48.40),
    (81.97, 47.74),
    (81.33, 47.05),
    (80.87, 46.41),
    (80.24, 45.72),
    (79.57, 45.04),
    (79.04, 44.55),
    (78.37, 44.01),
    (77.73, 43.48),
    (76.97, 42.97),
    (76.28, 42.40),
    (75.70, 42.48),   # начало границы с Кыргызстаном
    # Южная граница (Кыргызстан, Узбекистан, Туркменистан), восток → запад
    (74.21, 42.99),
    (73.55, 42.78),
    (73.14, 42.83),
    (72.66, 42.50),
    (71.85, 42.17),
    (71.38, 41.78),
    (70.94, 41.19),
    (70.48, 41.15),
    (69.97, 41.44),
    (69.07, 41.39),
    (68.37, 40.74),
    (67.98, 41.30),
    (66.52, 42.00),   # ↑ поднято (было 41.59) — исключаем узб. Навоий/Бухару
    (65.51, 42.30),   # ↑ поднято (было 41.52)
    (64.54, 42.60),   # ↑ поднято (было 40.46) — Кызылординская обл. здесь ~43°N
    (63.59, 42.60),   # ↑ поднято (было 39.93)
    (62.12, 42.20),   # ↑ поднято (было 40.04) — исключаем Хорезм
    (60.47, 42.00),   # ↑ поднято (было 41.22)
    (58.59, 41.80),
    (57.05, 41.32),
    (55.98, 41.05),
    (55.23, 41.25),
    (54.61, 40.89),
    (54.28, 40.22),
    (53.64, 39.92),   # граница с Туркменией (залив Кара-Богаз-Гол)
    (53.28, 40.28),
    (52.52, 41.78),
    # Западная граница (Каспийское море + степи), юг → север
    (52.55, 42.46),
    (51.81, 43.13),
    (51.22, 43.54),
    (50.74, 44.35),
    (50.28, 45.56),
    (49.58, 46.52),
    (49.18, 46.86),
    (49.10, 47.74),
    (50.03, 48.69),
    (50.27, 49.42),
    (50.23, 50.30),
    # Замыкаем полигон
    (50.27, 51.28),
]


def _point_in_poly(lon: float, lat: float, poly: list) -> bool:
    """Ray casting — нечётное число пересечений → точка внутри."""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def _center(bbox: list) -> tuple:
    """bbox = [W, S, E, N] → центр (lon, lat)."""
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def filter_file(name: str, strict: bool = True) -> None:
    path = OUTPUT / name
    if not path.exists():
        print(f"SKIP {name} — not found")
        return

    with open(path) as f:
        gj = json.load(f)

    before = len(gj["features"])
    kept = []
    for feat in gj["features"]:
        bb = feat["properties"].get("bbox")
        if bb is None:
            # Нет bbox — посчитаем из геометрии
            coords = feat["geometry"].get("coordinates", [])
            if feat["geometry"]["type"] == "Polygon":
                ring = coords[0] if coords else []
            elif feat["geometry"]["type"] == "MultiPolygon":
                ring = coords[0][0] if coords else []
            else:
                ring = []
            if not ring:
                kept.append(feat)
                continue
            lons = [c[0] for c in ring]
            lats = [c[1] for c in ring]
            bb = [min(lons), min(lats), max(lons), max(lats)]

        cx, cy = _center(bb)
        if _point_in_poly(cx, cy, KZ_POLY):
            kept.append(feat)

    after = len(kept)
    print(f"{name}: {before} → {after} features (removed {before - after})")

    gj["features"] = kept
    body = json.dumps(gj, ensure_ascii=False, separators=(",", ":")).encode()

    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)

    gz = path.with_suffix(".geojson.gz") if ".geojson" in path.name else OUTPUT / (name + ".gz")
    gz = OUTPUT / (name + ".gz")
    import gzip as _gz
    tmp_gz = gz.with_suffix(".tmp.gz")
    with _gz.open(tmp_gz, "wb", compresslevel=6) as fh:
        fh.write(body)
    tmp_gz.replace(gz)
    print(f"  → {path.stat().st_size // 1024} KB plain, {gz.stat().st_size // 1024} KB gzip")


if __name__ == "__main__":
    filter_file("districts_l4.geojson")
    filter_file("districts_l68.geojson")
    print("Done!")
