"""
Патчит districts_l4.geojson и districts_l68.geojson:
заменяет неточные границы г.Астана и её районов
точными данными из OSM Overpass API.

Запуск ПОСЛЕ convert_local_geo.py:
    docker compose exec worker python /app/worker/convert_local_geo.py
    docker compose exec worker python /app/worker/patch_astana_districts.py
"""
from __future__ import annotations
import gzip, json, sys, time, urllib.parse
from pathlib import Path

# ── Используем проверенный код из fetch_districts.py ─────────────────────────
sys.path.insert(0, "/app")
from worker.fetch_districts import (
    _relation_to_feature,
    _OVERPASS_MIRRORS,
)
import httpx

OUTPUT = Path("/geo-data")

# Запрос:
#  1. Найти area «Астана» (admin_level=4 или 2 — городское значение)
#  2. Получить L4 полигон города и L8 районы внутри него
# Overpass area ID = relation ID + 3600000000
QUERY = """[out:json][timeout:120];
area["name"="Астана"]["boundary"="administrative"]->.city;
(
  relation["boundary"="administrative"]["admin_level"~"^[24]$"]["name"~"[Аа]стана"];
  relation["boundary"="administrative"]["admin_level"="8"](area.city);
);
out geom;"""

# OSM Казахский → имя для GeoJSON (фронтенд в RAION_MAP переводит в КАТО)
NAME_NORMALIZE = {
    "Есіл ауданы":     "Есіл ауданы",
    "Алматы ауданы":   "Алматы ауданы",
    "Сарыарқа ауданы": "Сарыарқа ауданы",
    "Байқоңыр ауданы": "Байқоңыр ауданы",
    "Нұра ауданы":     "Нұра ауданы",
}


def fetch_overpass() -> dict:
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "geo-astana-patcher/1.0",
    }
    last_err = None
    for url in _OVERPASS_MIRRORS:
        try:
            print(f"  Trying {url} ...", flush=True)
            with httpx.Client(timeout=150, follow_redirects=True, verify=False) as cl:
                r = cl.post(url, content=body, headers=headers)
                r.raise_for_status()
                data = r.json()
                print(f"  OK — {len(r.content)//1024} KB, "
                      f"{len(data.get('elements',[]))} elements", flush=True)
                return data
        except Exception as exc:
            print(f"  Failed: {exc}", flush=True)
            last_err = exc
    raise last_err or RuntimeError("All Overpass mirrors failed")


def save(name: str, features: list) -> None:
    body = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    p  = OUTPUT / name
    gz = OUTPUT / (name + ".gz")
    p.write_bytes(body)
    with gzip.open(gz, "wb", compresslevel=6) as fh:
        fh.write(body)
    print(f"  Saved {len(features):4d} features → {name}"
          f"  ({p.stat().st_size//1024} KB plain, {gz.stat().st_size//1024} KB gz)")


def main():
    # Загрузить текущие файлы
    l4  = json.loads((OUTPUT / "districts_l4.geojson").read_bytes())["features"]
    l68 = json.loads((OUTPUT / "districts_l68.geojson").read_bytes())["features"]
    print(f"Loaded: L4={len(l4)}, L68={len(l68)}\n")

    # Скачать из Overpass
    print("Fetching Astana from Overpass ...")
    t0  = time.time()
    osm = fetch_overpass()
    rels = [e for e in osm.get("elements", []) if e["type"] == "relation"]
    print(f"Got {len(rels)} relations in {time.time()-t0:.1f}s\n")

    astana_city = None   # L4 полигон города
    astana_dists = []    # L8 районы

    for rel in rels:
        tags = rel.get("tags", {})
        lvl  = tags.get("admin_level", "")
        feat = _relation_to_feature(rel)     # проверенная функция из fetch_districts
        if feat is None:
            name = tags.get("name","?")
            print(f"  SKIP (no geom): lvl={lvl!r} name={name!r}")
            continue

        name    = feat["properties"]["name"]
        osm_id  = feat["properties"]["osm_id"]
        bb      = feat["properties"]["bbox"]
        geom    = feat["geometry"]

        if lvl in ("2", "4") and any(t in name for t in ("Астана", "астана", "Нур-Султан", "Нұр-Сұлтан")):
            # Берём наименьший bbox — это и есть сам город, а не область
            if astana_city is None or (
                (bb[2]-bb[0])*(bb[3]-bb[1]) < (astana_city["bb"][2]-astana_city["bb"][0])*(astana_city["bb"][3]-astana_city["bb"][1])
            ):
                astana_city = {"name": name, "osm_id": osm_id, "bb": bb, "geom": geom}
            print(f"  [L{lvl}] City: {name!r}  bbox={[round(x,2) for x in bb]}")
        elif lvl == "8":
            norm_name = NAME_NORMALIZE.get(name, name)
            astana_dists.append({
                "name": norm_name, "osm_id": osm_id, "bb": bb, "geom": geom
            })
            print(f"  [L8] {name!r} → {norm_name!r}")

    print(f"\nCity found: {astana_city is not None}, Districts: {len(astana_dists)}\n")

    if not astana_city and not astana_dists:
        print("ERROR: Nothing found from Overpass. Check network. Aborting.")
        sys.exit(1)

    # ── Патч L4 ──────────────────────────────────────────────────────────────
    l4_new = [f for f in l4 if f["properties"].get("id_reg") != 71]
    if astana_city:
        l4_new.append({
            "type": "Feature",
            "properties": {
                "name":        "г.Астана",
                "admin_level": "4",
                "id_reg":      71,
                "bbox":        astana_city["bb"],
            },
            "geometry": astana_city["geom"],
        })
        print(f"Patched L4: replaced Astana city polygon")
    else:
        print("WARN: Astana L4 not found — keeping original raion_polygon.json version")

    # ── Патч L68 ─────────────────────────────────────────────────────────────
    l68_new = [f for f in l68 if f["properties"].get("id_reg") != 71]
    for d in astana_dists:
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
        print(f"  Added district: {d['name']!r}")
    print(f"Patched L68: removed old Astana, added {len(astana_dists)} from OSM\n")

    save("districts_l4.geojson",  l4_new)
    save("districts_l68.geojson", l68_new)
    print("\nDone! Reload the browser to see updated boundaries.")


if __name__ == "__main__":
    main()
