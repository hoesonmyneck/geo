"""
Локальная утилита: разбивает существующий /geo-data/districts.geojson
на два оптимизированных файла без обращения к интернету.
"""
import gzip, json, math
from pathlib import Path

OUTPUT = Path("/geo-data")

# ── RDP simplification ──────────────────────────────────────────────────────

def rdp(pts, eps):
    if len(pts) <= 2:
        return list(pts)
    stack = [(0, len(pts) - 1)]
    keep = {0, len(pts) - 1}
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        dx = pts[hi][0] - pts[lo][0]
        dy = pts[hi][1] - pts[lo][1]
        d2 = dx * dx + dy * dy
        max_d, max_i = 0.0, lo
        for i in range(lo + 1, hi):
            if d2 == 0:
                dd = math.hypot(pts[i][0] - pts[lo][0], pts[i][1] - pts[lo][1])
            else:
                t = max(0.0, min(1.0, (
                    (pts[i][0] - pts[lo][0]) * dx +
                    (pts[i][1] - pts[lo][1]) * dy
                ) / d2))
                dd = math.hypot(
                    pts[i][0] - pts[lo][0] - t * dx,
                    pts[i][1] - pts[lo][1] - t * dy,
                )
            if dd > max_d:
                max_d, max_i = dd, i
        if max_d > eps:
            keep.add(max_i)
            stack.append((lo, max_i))
            stack.append((max_i, hi))
    return [pts[i] for i in sorted(keep)]


def simplify(ring, eps=0.0003, prec=4):
    if len(ring) < 4:
        return ring
    closed = (
        abs(ring[0][0] - ring[-1][0]) < 1e-8 and
        abs(ring[0][1] - ring[-1][1]) < 1e-8
    )
    pts = ring[:-1] if closed else ring
    s = rdp(pts, eps)
    r = [[round(c[0], prec), round(c[1], prec)] for c in s]
    d = [r[0]]
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


# ── Main ────────────────────────────────────────────────────────────────────

def save(name, features):
    body = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False, separators=(",", ":")
    ).encode()
    p  = OUTPUT / name
    gz = OUTPUT / (name + ".gz")
    p.write_bytes(body)
    with gzip.open(gz, "wb", compresslevel=6) as fh:
        fh.write(body)
    print(f"Saved {len(features):5d} features → {name:30s}  "
          f"({p.stat().st_size // 1024:6d} KB plain, "
          f"{gz.stat().st_size // 1024:5d} KB gzip)")


def main():
    src = OUTPUT / "districts.geojson"
    print(f"Reading {src} ...")
    with open(src) as f:
        gj = json.load(f)
    features = gj.get("features", [])
    print(f"Loaded {len(features)} features")

    l4, l68 = [], []
    for feat in features:
        lvl  = feat["properties"].get("admin_level", "")
        geom = feat["geometry"]
        if not geom:
            continue
        if geom["type"] == "Polygon":
            rings = [simplify(r) for r in geom["coordinates"]]
            new_geom = {"type": "Polygon", "coordinates": rings}
            bb = bbox(rings[0])
        elif geom["type"] == "MultiPolygon":
            polys = [[simplify(r) for r in poly] for poly in geom["coordinates"]]
            new_geom = {"type": "MultiPolygon", "coordinates": polys}
            bb = bbox(polys[0][0])
        else:
            continue

        new_feat = {
            "type": "Feature",
            "properties": {**feat["properties"], "bbox": bb},
            "geometry": new_geom,
        }
        (l4 if lvl == "4" else l68).append(new_feat)

    print(f"L4 (oblasts): {len(l4)}  |  L6/8 (districts): {len(l68)}")
    save("districts_l4.geojson",  l4)
    save("districts_l68.geojson", l68)
    print("Done!")


if __name__ == "__main__":
    main()
