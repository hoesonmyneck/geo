"""Показываем что потеряли при фильтрации и почему."""
import json, math
from pathlib import Path

KZ_POLY = [
    (50.27, 51.28),(51.20, 51.70),(52.07, 52.00),(53.01, 51.56),(54.12, 51.10),
    (55.80, 50.75),(57.84, 51.07),(59.00, 51.80),(61.00, 53.60),(62.50, 54.40),
    (64.00, 55.10),(65.50, 55.45),(68.00, 55.20),(70.00, 55.20),(72.50, 55.00),
    (75.00, 54.50),(76.26, 54.32),(76.58, 53.96),(77.80, 53.40),(79.38, 52.85),
    (80.52, 52.24),(81.47, 51.67),(82.49, 51.26),(83.13, 50.89),(83.38, 50.35),
    (83.17, 49.80),(82.96, 49.10),(82.42, 48.40),(81.97, 47.74),(81.33, 47.05),
    (80.87, 46.41),(80.24, 45.72),(79.57, 45.04),(79.04, 44.55),(78.37, 44.01),
    (77.73, 43.48),(76.97, 42.97),(76.28, 42.40),(75.70, 42.48),(74.21, 42.99),
    (73.55, 42.78),(73.14, 42.83),(72.66, 42.50),(71.85, 42.17),(71.38, 41.78),
    (70.94, 41.19),(70.48, 41.15),(69.97, 41.44),(69.07, 41.39),(68.37, 40.74),
    (67.98, 41.30),(66.52, 42.00),(65.51, 42.30),(64.54, 42.60),(63.59, 42.60),
    (62.12, 42.20),(60.47, 42.00),(58.59, 41.80),(57.05, 41.32),(55.98, 41.05),
    (55.23, 41.25),(54.61, 40.89),(54.28, 40.22),(53.64, 39.92),(53.28, 40.28),
    (52.52, 41.78),(52.55, 42.46),(51.81, 43.13),(51.22, 43.54),(50.74, 44.35),
    (50.28, 45.56),(49.58, 46.52),(49.18, 46.86),(49.10, 47.74),(50.03, 48.69),
    (50.27, 49.42),(50.23, 50.30),(50.27, 51.28),
]

def pip(lon, lat, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj-xi)*(lat-yi)/(yj-yi+1e-15)+xi):
            inside = not inside
        j = i
    return inside

# Читаем split файл (с bbox)
gj = json.load(open('/geo-data/districts_l4.geojson'))
print(f"districts_l4.geojson: {len(gj['features'])} features (after filter)")
for f in sorted(gj['features'], key=lambda x: x['properties'].get('name','')):
    p = f['properties']
    bb = p.get('bbox', [])
    cx = (bb[0]+bb[2])/2
    cy = (bb[1]+bb[3])/2
    print(f"  KEEP  {p.get('name','?'):<40s} center=({cx:.1f},{cy:.1f}) in_poly={pip(cx,cy,KZ_POLY)}")

# Читаем полный исходник — ищем потерянные
print()
print("Checking source for lost KZ oblasts:")
src = json.load(open('/geo-data/districts.geojson'))
kept_ids = {f['properties']['osm_id'] for f in gj['features']}

for f in src['features']:
    p = f['properties']
    if p.get('admin_level') != '4': continue
    if p['osm_id'] in kept_ids: continue  # already kept

    # Вычислим bbox из геометрии
    geom = f['geometry']
    if geom['type'] == 'Polygon':
        ring = geom['coordinates'][0]
    elif geom['type'] == 'MultiPolygon':
        ring = geom['coordinates'][0][0]
    else:
        continue
    if not ring: continue
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    bb = [min(lons), min(lats), max(lons), max(lats)]
    cx = (bb[0]+bb[2])/2
    cy = (bb[1]+bb[3])/2
    inside = pip(cx, cy, KZ_POLY)
    print(f"  {'LOST' if not inside else 'BUG!'} {p.get('name','?'):<40s} center=({cx:.1f},{cy:.1f}) in_poly={inside}")
