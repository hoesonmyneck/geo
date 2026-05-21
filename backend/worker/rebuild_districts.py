"""
Пересобирает districts_l4.geojson и districts_l68.geojson
прямо из исходного districts.geojson:
  1. RDP-упрощение геометрии
  2. Добавление pre-computed bbox
  3. Фильтрация: только Казахстан (ring-centroid point-in-polygon)

Запуск: docker compose exec worker python /app/worker/rebuild_districts.py
"""
import gzip, json, math
from pathlib import Path

OUTPUT = Path("/geo-data")

# ── Геометрия: RDP ───────────────────────────────────────────────────────────

def _rdp(pts, eps):
    if len(pts) <= 2:
        return list(pts)
    stack = [(0, len(pts) - 1)]
    keep  = {0, len(pts) - 1}
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        dx = pts[hi][0] - pts[lo][0]
        dy = pts[hi][1] - pts[lo][1]
        d2 = dx * dx + dy * dy
        md, mi = 0.0, lo
        for i in range(lo + 1, hi):
            if d2 == 0:
                dd = math.hypot(pts[i][0]-pts[lo][0], pts[i][1]-pts[lo][1])
            else:
                t = max(0.0, min(1.0, ((pts[i][0]-pts[lo][0])*dx +
                                       (pts[i][1]-pts[lo][1])*dy) / d2))
                dd = math.hypot(pts[i][0]-pts[lo][0]-t*dx, pts[i][1]-pts[lo][1]-t*dy)
            if dd > md:
                md, mi = dd, i
        if md > eps:
            keep.add(mi)
            stack.append((lo, mi))
            stack.append((mi, hi))
    return [pts[i] for i in sorted(keep)]


def simplify(ring, eps=0.0003, prec=4):
    if len(ring) < 4:
        return ring
    closed = (abs(ring[0][0]-ring[-1][0]) < 1e-8 and
              abs(ring[0][1]-ring[-1][1]) < 1e-8)
    pts = ring[:-1] if closed else ring
    s   = _rdp(pts, eps)
    r   = [[round(c[0], prec), round(c[1], prec)] for c in s]
    d   = [r[0]]
    for c in r[1:]:
        if c != d[-1]:
            d.append(c)
    if d[0] != d[-1]:
        d.append(d[0])
    return d if len(d) >= 4 else ring


def bbox(ring):
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    return [min(lons), min(lats), max(lons), max(lats)]


def ring_centroid(ring):
    """Среднее арифметическое по точкам кольца — устойчиво для L-образных фигур."""
    n = len(ring)
    if n == 0:
        return None, None
    return sum(c[0] for c in ring) / n, sum(c[1] for c in ring) / n


# ── Полигон Казахстана (lon, lat) ────────────────────────────────────────────
# Ключевые исправления:
#   • Север поднят до 55.45°N (lon 65°E) — Костанай/СКО/Акмола
#   • Юг поднят до 42.5-43.1°N на lon 72-76°E — исключает Бишкек и Талас (КГ)
#   • Восток: 83.5°E — ВКО фильтруется по centroid (не bbox), попадает правильно
KZ_POLY = [
    # Северная граница (Россия): запад → восток
    (50.27, 51.28),
    (51.20, 51.70),
    (52.07, 52.00),
    (53.01, 51.56),
    (54.12, 51.10),
    (55.80, 50.75),
    (57.84, 51.07),
    (59.00, 51.80),
    (61.00, 53.60),
    (62.50, 54.40),
    (64.00, 55.10),
    (65.50, 55.45),   # САМАЯ СЕВЕРНАЯ ТОЧКА
    (68.00, 55.20),
    (70.00, 55.20),
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
    (83.38, 50.35),
    # Восточная граница (Китай): север → юг
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
    (75.70, 42.48),
    # Южная граница (Кыргызстан → Узбекистан → Туркмения): восток → запад
    # Поднято на lon 72–76°E чтобы исключить Бишкек (42.87°N) и Талас (42.4°N)
    (74.21, 43.10),   # ↑ было 42.99 — выше Бишкека
    (73.55, 42.90),   # ↑ было 42.78
    (73.14, 42.85),   # ↑ было 42.83
    (72.66, 42.60),   # ↑ было 42.50 — выше Таласа
    (71.85, 42.17),
    (71.38, 41.78),
    (70.94, 41.19),
    (70.48, 41.15),
    (69.97, 41.44),
    (69.07, 41.39),
    (68.37, 40.74),
    (67.98, 41.30),
    (66.52, 42.00),   # ↑ поднято — исключаем узб. Навоий/Бухару
    (65.51, 42.30),
    (64.54, 42.60),
    (63.59, 42.60),
    (62.12, 42.20),
    (60.47, 42.00),
    (58.59, 41.80),
    (57.05, 41.32),
    (55.98, 41.05),
    (55.23, 41.25),
    (54.61, 40.89),
    (54.28, 40.22),
    (53.64, 39.92),
    (53.28, 40.28),
    (52.52, 41.78),
    # Западная граница (Каспий): юг → север
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
    (50.27, 51.28),   # замыкаем
]


def point_in_poly(lon, lat, poly):
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


# ── Фильтрация ───────────────────────────────────────────────────────────────

# Для L4 (области) — whitelist по имени:
# Все казахстанские области в OSM называются с суффиксом "облысы" (казахск.),
# республиканские города — именно "Алматы", "Астана", "Шымкент".
# Соседние страны: Kyrgyz "облусу"/"шаары", Uzbek "Viloyati", Turkmen "welaýaty",
#                  Russian "область", Chinese "自治区" — ни одно не совпадает.
_KZ_L4_NAME_TOKENS = ("облысы", "Алматы", "Астана", "Шымкент")

def is_kz_l4(feature) -> bool:
    """L4: Казахстан если имя содержит казахский суффикс области/республ.города."""
    name = feature["properties"].get("name", "")
    return any(tok in name for tok in _KZ_L4_NAME_TOKENS)


def _outer_ring(feature):
    """Возвращает внешнее кольцо геометрии фичи."""
    geom = feature.get("geometry", {})
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if not coords:
        return None
    if gtype == "Polygon":
        return coords[0]
    if gtype == "MultiPolygon":
        return coords[0][0]
    return None


def is_kz_l68_by_l4(feature, l4_rings) -> bool:
    """L6/8: centroid кольца должен лежать внутри одного из 20 KZ-областных полигонов."""
    ring = _outer_ring(feature)
    if not ring:
        return False
    cx, cy = ring_centroid(ring)
    if cx is None:
        return False
    return any(point_in_poly(cx, cy, r) for r in l4_rings)


# ── Сохранение ───────────────────────────────────────────────────────────────

def save(name, features):
    body = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False, separators=(",", ":")
    ).encode()
    p  = OUTPUT / name
    gz = OUTPUT / (name + ".gz")
    tmp_p = p.with_suffix(".tmp")
    tmp_g = gz.with_suffix(".tmp.gz")
    tmp_p.write_bytes(body)
    tmp_p.replace(p)
    with gzip.open(tmp_g, "wb", compresslevel=6) as fh:
        fh.write(body)
    tmp_g.replace(gz)
    print(f"  Saved {len(features):5d} → {name:<30s}  "
          f"({p.stat().st_size//1024:6d} KB, {gz.stat().st_size//1024:5d} KB gz)")


# ── Main ─────────────────────────────────────────────────────────────────────

def _process_feature(feat):
    """Упрощает геометрию и добавляет bbox. Возвращает None если геометрия неизвестного типа."""
    geom = feat.get("geometry")
    if not geom:
        return None
    if geom["type"] == "Polygon":
        rings    = [simplify(r) for r in geom["coordinates"]]
        new_geom = {"type": "Polygon", "coordinates": rings}
        bb       = bbox(rings[0])
    elif geom["type"] == "MultiPolygon":
        polys    = [[simplify(r) for r in poly] for poly in geom["coordinates"]]
        new_geom = {"type": "MultiPolygon", "coordinates": polys}
        bb       = bbox(polys[0][0])
    else:
        return None
    return {
        "type": "Feature",
        "properties": {**feat["properties"], "bbox": bb},
        "geometry": new_geom,
    }


def main():
    src = OUTPUT / "districts.geojson"
    print(f"Reading {src} ...")
    features = json.load(open(src))["features"]
    print(f"Loaded {len(features)} features")

    # ── Проход 1: L4 (области) — name-whitelist ──────────────────────────────
    l4_kz, l4_skip = [], []
    l68_raw = []   # L68 откладываем на второй проход

    for feat in features:
        lvl = feat["properties"].get("admin_level", "")
        new_feat = _process_feature(feat)
        if new_feat is None:
            continue
        if lvl == "4":
            (l4_kz if is_kz_l4(new_feat) else l4_skip).append(new_feat)
        else:
            l68_raw.append(new_feat)

    # Строим список внешних колец 20 KZ-областей для spatial join
    l4_rings = []
    for f in l4_kz:
        ring = _outer_ring(f)
        if ring:
            l4_rings.append(ring)
    print(f"L4 KZ oblasts: {len(l4_kz)}  (rings built: {len(l4_rings)})")

    # ── Проход 2: L68 (районы) — centroid должен лежать в одной из областей ──
    l68_kz, l68_skip = [], []
    for new_feat in l68_raw:
        (l68_kz if is_kz_l68_by_l4(new_feat, l4_rings) else l68_skip).append(new_feat)

    def _rc(f):
        r = _outer_ring(f)
        if not r:
            return "(?)"
        cx, cy = ring_centroid(r)
        return f"({cx:.1f},{cy:.1f})"

    print(f"\nL4  kept={len(l4_kz)}  skipped={len(l4_skip)}")
    print("  Kept:")
    for f in sorted(l4_kz, key=lambda x: x["properties"].get("name", "")):
        print(f"    ok  {f['properties'].get('name','?'):<45s}  rc={_rc(f)}")
    print("  Skipped (non-KZ):")
    for f in sorted(l4_skip, key=lambda x: x["properties"].get("name", "")):
        print(f"    --  {f['properties'].get('name','?'):<45s}  rc={_rc(f)}")

    print(f"\nL68 kept={len(l68_kz)}  skipped={len(l68_skip)}")
    # Показываем первые 20 пропущенных для контроля
    print("  First skipped L68 (sample):")
    for f in l68_skip[:20]:
        print(f"    --  {f['properties'].get('name','?'):<45s}  rc={_rc(f)}")
    print()

    save("districts_l4.geojson",  l4_kz)
    save("districts_l68.geojson", l68_kz)
    print("Done!")


if __name__ == "__main__":
    main()
