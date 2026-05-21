"""Тест конкретного Photon запроса как его делает geocode()."""
import asyncio, sys
import httpx
sys.stdout.reconfigure(encoding="utf-8")

from src.normalize import normalize_row
from src.geocode import PHOTON_URL, _CITY_COORDS, _house_matches

rec = normalize_row(1, "г.Астана", "", "УЛИЦА ӘБІКЕН БЕКТҰРОВ", "4/4")
print(f"photon_query: {rec.photon_query!r}")
print(f"house: {rec.house!r}")
print()


async def main():
    coords = _CITY_COORDS.get(rec.city.upper(), ())
    base_params = {"lang": "ru", "limit": 5}
    if coords:
        base_params["lat"] = str(coords[0])
        base_params["lon"] = str(coords[1])

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        print("=== Pass 1: с номером дома (как в geocode) ===")
        r = await client.get(
            f"{PHOTON_URL}/api",
            params={**base_params, "q": rec.photon_query},
        )
        feats = r.json().get("features", [])
        for feat in feats:
            p = feat.get("properties", {})
            coords_f = feat.get("geometry", {}).get("coordinates", [])
            h = p.get("housenumber", "N/A")
            street = p.get("street", "?")
            lat = coords_f[1] if len(coords_f) >= 2 else 0
            lon = coords_f[0] if len(coords_f) >= 2 else 0
            matches = _house_matches(h, rec.house)
            print(f"  house={h!r}  street={street!r}  lat={lat}  lon={lon}  matches={matches}")

        print()
        print("=== Pass 1 с транслитом ===")
        q_translit = f"{rec.street_type} {rec.street_name_translit} {rec.house}, {rec.city}"
        print(f"  query: {q_translit!r}")
        r2 = await client.get(
            f"{PHOTON_URL}/api",
            params={**base_params, "q": q_translit},
        )
        feats2 = r2.json().get("features", [])
        for feat in feats2:
            p = feat.get("properties", {})
            coords_f = feat.get("geometry", {}).get("coordinates", [])
            h = p.get("housenumber", "N/A")
            street = p.get("street", "?")
            lat = coords_f[1] if len(coords_f) >= 2 else 0
            print(f"  house={h!r}  street={street!r}  lat={lat}  matches={_house_matches(h, rec.house)}")


asyncio.run(main())
