"""
Патчит districts_l4.geojson и districts_l68.geojson:
заменяет неточные границы г.Астана и её районов
точными данными из astana_districts_patch.json (захардкожены из OSM).

Запуск ПОСЛЕ convert_local_geo.py:
    docker compose exec worker python /app/worker/convert_local_geo.py
    docker compose exec worker python /app/worker/patch_astana_districts.py
"""
from __future__ import annotations
import gzip, json
from pathlib import Path

OUTPUT    = Path("/geo-data")
PATCH_SRC = Path(__file__).parent / "astana_districts_patch.json"

# Маппинг: OSM казахское название → имя которое ожидает фронтенд (RAION_MAP)
NAME_NORMALIZE = {
    "Есіл ауданы":     "Есіл ауданы",
    "Алматы ауданы":    "Алматы ауданы",
    "Сарыарқа ауданы":  "Сарыарқа ауданы",
    "Байқоңыр ауданы":  "Байқоңыр ауданы",
    "Нұра ауданы":      "Нұра ауданы",
    "Сарайшық ауданы":  "Сарайшық ауданы",
}


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
    # Загрузить текущие сгенерированные файлы
    l4  = json.loads((OUTPUT / "districts_l4.geojson").read_bytes())["features"]
    l68 = json.loads((OUTPUT / "districts_l68.geojson").read_bytes())["features"]
    print(f"Loaded: L4={len(l4)}, L68={len(l68)}")

    # Загрузить захардкоженные районы Астаны
    patch_data = json.loads(PATCH_SRC.read_text(encoding="utf-8-sig"))
    astana_dists = patch_data["features"]
    print(f"Patch source: {len(astana_dists)} Astana districts from {PATCH_SRC.name}\n")

    for d in astana_dists:
        name = d["properties"].get("name", "?")
        bb   = d["properties"].get("bbox", [])
        print(f"  {name!r}  id_rai={d['properties'].get('id_rai')}  bbox={[round(x,2) for x in bb]}")

    # ── Патч L4 (г.Астана — взять bbox из данных районов) ────────────────────
    # Строим bbox города как объединение bbox всех районов
    all_lons = [c for d in astana_dists for c in [d["properties"]["bbox"][0], d["properties"]["bbox"][2]]]
    all_lats = [c for d in astana_dists for c in [d["properties"]["bbox"][1], d["properties"]["bbox"][3]]]
    city_bbox = [min(all_lons), min(all_lats), max(all_lons), max(all_lats)]

    # Ищем polygon города в l4 — если там уже есть нормальный (от patch_astana),
    # используем его; иначе оставляем заглушку из convert_local_geo
    l4_astana = next((f for f in l4 if f["properties"].get("id_reg") == 71), None)
    l4_new = [f for f in l4 if f["properties"].get("id_reg") != 71]

    if l4_astana:
        # Обновить только bbox, геометрию сохранить
        l4_astana["properties"]["bbox"] = city_bbox
        l4_new.append(l4_astana)
        print(f"\nPatched L4: updated Astana city bbox → {[round(x,2) for x in city_bbox]}")
    else:
        print("\nWARN: Astana L4 not found — nothing to patch in L4")

    # ── Патч L68 (убрать только те районы Астаны которые мы ЗАМЕНЯЕМ,
    #             остальные id_reg=71 — например Сарайшық — сохранить) ──────────
    replace_names = set(NAME_NORMALIZE.keys()) | set(NAME_NORMALIZE.values())
    l68_new = [
        f for f in l68
        if not (
            f["properties"].get("id_reg") == 71
            and f["properties"].get("name", "") in replace_names
        )
    ]
    kept = len(l68) - len(l68_new)
    print(f"Removed {kept} old Astana districts being replaced")
    added = 0
    for d in astana_dists:
        props = d["properties"]
        name  = NAME_NORMALIZE.get(props.get("name", ""), props.get("name", ""))
        l68_new.append({
            "type": "Feature",
            "properties": {
                "name":        name,
                "admin_level": "6",
                "id_rai":      props.get("id_rai"),
                "id_reg":      71,
                "region":      "г.Астана",
                "bbox":        props.get("bbox"),
            },
            "geometry": d["geometry"],
        })
        added += 1
        print(f"  Added: {name!r}")

    print(f"\nPatched L68: removed old Astana districts, added {added} from patch file\n")

    save("districts_l4.geojson",  l4_new)
    save("districts_l68.geojson", l68_new)
    print("\nDone! Reload the browser to see updated boundaries.")


if __name__ == "__main__":
    main()
