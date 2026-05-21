"""
Патчит districts_l4.geojson и districts_l68.geojson:
заменяет неточные границы г.Астана и её районов
точными данными из OSM Overpass API (admin_level=4/8).

Запуск ПОСЛЕ convert_local_geo.py:
    docker compose exec worker python /app/worker/convert_local_geo.py
    docker compose exec worker python /app/worker/patch_astana_districts.py
"""
from __future__ import annotations
import gzip, json, math, sys, time, urllib.parse
from pathlib import Path

try:
    import httpx
except ImportError:
    import subprocess, sys as _sys
    subprocess.check_call([_sys.executable, "-m", "pip", "install",
                           "--trusted-host", "pypi.org",
                           "--trusted-host", "files.pythonhosted.org", "httpx"])
    import httpx

OUTPUT = Path("/geo-data")

# Bbox Астаны с запасом
ASTANA_BBOX = "50.8,71.0,51.55,72.1"   # S,W,N,E — формат Overpass

# Запрос: город Астана (L4) + его районы (L8)
QUERY = f"""[out:json][timeout:90][bbox:{ASTANA_BBOX}];
(
  relation["boundary"="administrative"]["admin_level"~"^[48]$"]["name"~"[Аа]стана|[Нн]ур.?[Сс]улт"];
  relation["boundary"="administrative"]["admin_level"="8"];
);
out geom;"""

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Маппинг OSM-названий (казахский) → отображаемое имя в GeoJSON
# Фронтенд в RAION_MAP переводит их в КАТО-названия
NAME_NORMALIZE = {
    "Есіл ауданы":    "Есіл ауданы",
    "Алматы ауданы":  "Алматы ауданы",
    "Сарыарқа ауданы":"Сарыарқа ауданы",
    "Байқоңыр ауданы":"Байқоңыр ауданы",
    "Нұра ауданы":    "Нұра ауданы",
}


# ── геометрия ────────────────────────────────────────────────────────────────

def _pt_dist(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    if dx == dy == 0:
        return math.hypot(p[0]-a[0], p[1]-a[1])
    t = max(0.0, min(1.0, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)

def _rdp(pts, eps=0.0002):
    if len(pts) <= 2:
        return list(pts)
    stack, keep = [(0, len(pts)-1)], {0, len(pts)-1}
    while stack:
        lo, hi = stack.pop()
        if hi-lo < 2:
            continue
        md, mi = 0.0, lo
        for i in range(lo+1, hi):
            d = _pt_dist(pts[i], pts[lo], pts[hi])
            if d > md:
                md, mi = d, i
        if md > eps:
            keep.add(mi); stack.append((lo,mi)); stack.append((mi,hi))
    return [pts[i] for i in sorted(keep)]

def simplify(ring):
    if len(ring) < 4:
        return ring
    closed = abs(ring[0][0]-ring[-1][0]) < 1e-8 and abs(ring[0][1]-ring[-1][1]) < 1e-8
    pts = ring[:-1] if closed else ring
    s = _rdp(pts)
    r = [[round(c[0],4), round(c[1],4)] for c in s]
    d = [r[0]]
    for c in r[1:]:
        if c != d[-1]: d.append(c)
    if d[0] != d[-1]: d.append(d[0])
    return d if len(d) >= 4 else ring

def coords(geom_list):
    return [[g["lon"], g["lat"]] for g in geom_list]

def close(a, b, eps=1e-6):
    return abs(a[0]-b[0]) < eps and abs(a[1]-b[1]) < eps

def stitch(parts):
    if not parts:
        return []
    remaining = [list(p) for p in parts]
    ring = remaining.pop(0)
    for _ in range(len(remaining)*4):
        if not remaining:
            break
        merged = False
        for i, seg in enumerate(remaining):
            if   close(ring[-1], seg[0]):  ring.extend(seg[1:])
            elif close(ring[-1], seg[-1]): ring.extend(reversed(seg[:-1]))
            elif close(ring[0],  seg[-1]): ring = seg[:-1] + ring
            elif close(ring[0],  seg[0]):  ring = list(reversed(seg[1:])) + ring
            else: continue
            remaining.pop(i); merged = True; break
        if not merged:
            ring.extend(remaining.pop(0))
    if not close(ring[0], ring[-1]):
        ring.append(ring[0])
    return ring

def relation_to_polygon(rel):
    outer, inner = [], []
    for m in rel.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        pts = coords(m["geometry"])
        (inner if m.get("role") == "inner" else outer).append(pts)
    if not outer:
        return None, None
    outer_ring = simplify(stitch(outer))
    if len(outer_ring) < 4:
        return None, None
    inner_rings = [simplify(stitch([p])) for p in inner if len(stitch([p])) >= 4]
    geom = {"type": "Polygon", "coordinates": [outer_ring] + inner_rings}
    lons = [c[0] for c in outer_ring]
    lats = [c[1] for c in outer_ring]
    bb = [min(lons), min(lats), max(lons), max(lats)]
    return geom, bb


# ── Overpass ─────────────────────────────────────────────────────────────────

def fetch_overpass():
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "User-Agent": "geo-astana-patcher/1.0"}
    last_err = None
    for url in OVERPASS_MIRRORS:
        try:
            print(f"  Trying {url} ...", flush=True)
            with httpx.Client(timeout=120, follow_redirects=True,
                              verify=False) as client:
                r = client.post(url, content=body, headers=headers)
                r.raise_for_status()
                print(f"  OK — {len(r.content)//1024} KB", flush=True)
                return r.json()
        except Exception as exc:
            print(f"  Failed: {exc}", flush=True)
            last_err = exc
    raise last_err or RuntimeError("All Overpass mirrors failed")


# ── Сохранение ───────────────────────────────────────────────────────────────

def save(name, features):
    body = json.dumps({"type":"FeatureCollection","features":features},
                      ensure_ascii=False, separators=(",",":")).encode("utf-8")
    p  = OUTPUT / name
    gz = OUTPUT / (name + ".gz")
    p.write_bytes(body)
    with gzip.open(gz, "wb", compresslevel=6) as fh:
        fh.write(body)
    print(f"  Saved {len(features):4d} features → {name}  "
          f"({p.stat().st_size//1024} KB plain, {gz.stat().st_size//1024} KB gz)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Загрузить текущие файлы
    l4  = json.loads((OUTPUT / "districts_l4.geojson").read_bytes())["features"]
    l68 = json.loads((OUTPUT / "districts_l68.geojson").read_bytes())["features"]
    print(f"Loaded: L4={len(l4)}, L68={len(l68)}")

    # Получить данные из OSM
    print("\nFetching Astana districts from Overpass ...")
    t0  = time.time()
    osm = fetch_overpass()
    rels = [e for e in osm["elements"] if e["type"] == "relation"]
    print(f"Got {len(rels)} relations in {time.time()-t0:.1f}s")

    # Разобрать по уровням
    astana_l4  = None   # город-область Астана
    astana_l8  = []     # районы Астаны

    for rel in rels:
        tags = rel.get("tags", {})
        lvl  = tags.get("admin_level", "")
        name = (tags.get("name") or tags.get("name:ru") or
                tags.get("name:kk") or "")
        geom, bb = relation_to_polygon(rel)
        if geom is None:
            print(f"  SKIP (no geometry): {name!r} lvl={lvl}")
            continue

        if lvl == "4" and any(t in name for t in ("Астана","астана","Нур-Султан","Нұр-Сұлтан")):
            astana_l4 = {"geom": geom, "bb": bb, "name": name}
            print(f"  [L4] Found city: {name!r}")
        elif lvl == "8":
            normalized = NAME_NORMALIZE.get(name, name)
            astana_l8.append({"geom": geom, "bb": bb, "name": normalized, "osm_id": rel["id"]})
            print(f"  [L8] District: {name!r} → {normalized!r}")

    print(f"\nAstana L4: {'found' if astana_l4 else 'NOT FOUND'}")
    print(f"Astana L8: {len(astana_l8)} districts")

    # ── Патч L4 (убрать старую Астану, добавить OSM) ─────────────────────────
    l4_new = [f for f in l4 if f["properties"].get("id_reg") != 71]
    if astana_l4:
        l4_new.append({
            "type": "Feature",
            "properties": {
                "name":        "г.Астана",
                "admin_level": "4",
                "id_reg":      71,
                "bbox":        astana_l4["bb"],
            },
            "geometry": astana_l4["geom"],
        })
        print(f"\nPatched L4: replaced Astana city polygon")
    else:
        print("\nWARN: Astana L4 not found, keeping original")

    # ── Патч L68 (убрать старые районы Астаны id_reg=71, добавить OSM) ───────
    l68_new = [f for f in l68 if f["properties"].get("id_reg") != 71]
    for d in astana_l8:
        l68_new.append({
            "type": "Feature",
            "properties": {
                "name":        d["name"],
                "admin_level": "6",
                "id_rai":      d["osm_id"],
                "id_reg":      71,
                "region":      "г.Астана",
                "bbox":        d["bb"],
            },
            "geometry": d["geom"],
        })
    print(f"Patched L68: removed old Astana districts, added {len(astana_l8)} from OSM")

    # Сохранить
    print()
    save("districts_l4.geojson",  l4_new)
    save("districts_l68.geojson", l68_new)
    print("\nDone! Reload the browser to see updated boundaries.")


if __name__ == "__main__":
    main()
