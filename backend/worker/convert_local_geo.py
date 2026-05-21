"""
Конвертирует локальные GeoJSON (regions_polygon.json, raion_polygon.json)
в формат districts_l4.geojson и districts_l68.geojson:
  - добавляет bbox (pre-computed bounding box)
  - переименовывает поля в name/admin_level (совместимо с фронтендом)
  - применяет лёгкое RDP-упрощение (eps=0.0002) и округление до 4 знаков
  - сохраняет .geojson + .geojson.gz в /geo-data/

Запуск: docker compose exec worker python /app/worker/convert_local_geo.py
"""
import gzip, json, math
from pathlib import Path

INPUT  = Path("/input")
OUTPUT = Path("/geo-data")


# ── RDP-упрощение ─────────────────────────────────────────────────────────────

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
                dd = math.hypot(pts[i][0] - pts[lo][0], pts[i][1] - pts[lo][1])
            else:
                t = max(0.0, min(1.0, ((pts[i][0] - pts[lo][0]) * dx +
                                       (pts[i][1] - pts[lo][1]) * dy) / d2))
                dd = math.hypot(pts[i][0] - pts[lo][0] - t * dx,
                                pts[i][1] - pts[lo][1] - t * dy)
            if dd > md:
                md, mi = dd, i
        if md > eps:
            keep.add(mi)
            stack.append((lo, mi))
            stack.append((mi, hi))
    return [pts[i] for i in sorted(keep)]


def simplify(ring, eps=0.0002, prec=4):
    if len(ring) < 4:
        return ring
    closed = (abs(ring[0][0] - ring[-1][0]) < 1e-8 and
              abs(ring[0][1] - ring[-1][1]) < 1e-8)
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


def process_geom(geom):
    if geom["type"] == "Polygon":
        rings    = [simplify(r) for r in geom["coordinates"]]
        new_geom = {"type": "Polygon", "coordinates": rings}
        bb       = bbox(rings[0])
    elif geom["type"] == "MultiPolygon":
        polys    = [[simplify(r) for r in poly] for poly in geom["coordinates"]]
        new_geom = {"type": "MultiPolygon", "coordinates": polys}
        bb       = bbox(polys[0][0])
    else:
        return geom, None
    return new_geom, bb


# ── Сохранение ────────────────────────────────────────────────────────────────

def save(name, features):
    body = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    p  = OUTPUT / name
    gz = OUTPUT / (name + ".gz")
    p.write_bytes(body)
    with gzip.open(gz, "wb", compresslevel=6) as fh:
        fh.write(body)
    print(f"  Saved {len(features):4d} features → {name:<30s}"
          f"  ({p.stat().st_size // 1024:6d} KB plain, {gz.stat().st_size // 1024:5d} KB gz)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # L4: регионы (области)
    print("Processing regions_polygon.json ...")
    with open(INPUT / "regions_polygon.json", encoding="utf-8") as f:
        src_regions = json.load(f)["features"]

    l4_features = []
    for feat in src_regions:
        p        = feat["properties"]
        new_geom, bb = process_geom(feat["geometry"])
        if bb is None:
            continue
        l4_features.append({
            "type": "Feature",
            "properties": {
                "name":        p.get("region", ""),
                "admin_level": "4",
                "id_reg":      int(p.get("id_reg", 0)),
                "bbox":        bb,
            },
            "geometry": new_geom,
        })
        print(f"  {p.get('region','?'):<30s}  bbox={[round(x,2) for x in bb]}")

    save("districts_l4.geojson", l4_features)

    # L68: районы
    print("\nProcessing raion_polygon.json ...")
    with open(INPUT / "raion_polygon.json", encoding="utf-8") as f:
        src_raions = json.load(f)["features"]

    l68_features = []
    for feat in src_raions:
        p        = feat["properties"]
        new_geom, bb = process_geom(feat["geometry"])
        if bb is None:
            continue
        l68_features.append({
            "type": "Feature",
            "properties": {
                "name":        p.get("raion", ""),
                "admin_level": "6",
                "id_rai":      int(p.get("id_rai", 0)),
                "id_reg":      int(p.get("id_reg", 0)),
                "region":      p.get("region", ""),
                "bbox":        bb,
            },
            "geometry": new_geom,
        })

    print(f"  {len(l68_features)} raions processed")
    save("districts_l68.geojson", l68_features)
    print("\nDone!")


if __name__ == "__main__":
    main()
