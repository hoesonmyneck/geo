"""
Export street index from Nominatim Postgres to JSONL files.
One file per city in data/streets_index/{CITY}.jsonl

Usage (run after nominatim import):
    python -m src.export_streets --city АСТАНА
    python -m src.export_streets --all
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import click

logger = logging.getLogger(__name__)

POSTGRES_DSN = os.getenv(
    "NOMINATIM_PG_DSN",
    "host=localhost port=5432 dbname=nominatim user=nominatim password=nominatim",
)

# City → (lon, lat) approximate centroid for bounding box query
CITY_CENTROIDS = {
    "АСТАНА": (71.4282, 51.1801),
    "АЛМАТЫ": (76.8890, 43.2220),
    "ШЫМКЕНТ": (69.5960, 42.3000),
    "ҚАРАҒАНДЫ": (73.1141, 49.8047),
    "АҚТӨБЕ": (57.2067, 50.2793),
    "ТАРАЗ": (71.3667, 42.9000),
    "ПАВЛОДАР": (76.9674, 52.2873),
    "ӨСКЕМЕН": (82.6276, 49.9727),
    "СЕМЕЙ": (80.2275, 50.4119),
    "АТЫРАУ": (51.9200, 47.1167),
}

RADIUS_M = 60_000  # 60 km from city center


def export_city(city: str) -> int:
    try:
        import psycopg
    except ImportError:
        logger.error("psycopg not installed. Run: pip install psycopg[binary]")
        return 0

    centroid = CITY_CENTROIDS.get(city.upper())
    if centroid is None:
        logger.error("Unknown city: %s. Add to CITY_CENTROIDS dict.", city)
        return 0

    lon, lat = centroid
    out_dir = Path(__file__).parent.parent / "data" / "streets_index"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{city.upper()}.jsonl"

    query = """
        SELECT
            coalesce(name->'name:ru', name->'name:kk', name->'name') AS name,
            ST_Y(ST_Centroid(geometry)) AS lat,
            ST_X(ST_Centroid(geometry)) AS lon
        FROM placex
        WHERE
            class = 'highway'
            AND type IN ('residential','primary','secondary','tertiary',
                         'unclassified','trunk','motorway','pedestrian',
                         'living_street','service','path','footway')
            AND (name ? 'name:ru' OR name ? 'name:kk' OR name ? 'name')
            AND ST_DWithin(
                ST_Centroid(geometry)::geography,
                ST_MakePoint(%s, %s)::geography,
                %s
            )
    """

    count = 0
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (lon, lat, RADIUS_M))
            with out_path.open("w", encoding="utf-8") as f:
                for row in cur:
                    name, row_lat, row_lon = row
                    if name:
                        f.write(json.dumps({"name": name, "lat": row_lat, "lon": row_lon}, ensure_ascii=False) + "\n")
                        count += 1

    logger.info("Exported %d streets for %s → %s", count, city, out_path)
    return count


@click.command()
@click.option("--city", default=None, help="City name (e.g. АСТАНА)")
@click.option("--all", "all_cities", is_flag=True, help="Export all known cities")
def main(city: str | None, all_cities: bool) -> None:
    logging.basicConfig(level=logging.INFO)
    cities = list(CITY_CENTROIDS.keys()) if all_cities else ([city.upper()] if city else [])
    if not cities:
        click.echo("Provide --city or --all")
        return
    for c in cities:
        export_city(c)


if __name__ == "__main__":
    main()
