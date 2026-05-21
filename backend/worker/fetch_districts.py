"""
Скачивает административные границы Казахстана из Overpass API.

Генерирует ДВА оптимизированных файла:
  /geo-data/districts_l4.geojson.gz   — области   (admin_level=4, ~20 объектов, < 1 МБ)
  /geo-data/districts_l68.geojson.gz  — районы     (admin_level=6/8, ~4700 объектов)

Оптимизации:
  • Упрощение геометрии (Ramer-Douglas-Peucker, epsilon=0.0003°)
  • Округление координат до 4 знаков
  • Pre-computed bbox в properties — JS фильтрует за O(1)

Запуск:
    docker compose exec worker python /app/worker/fetch_districts.py
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

logger = logging.getLogger("fetch_districts")

OUTPUT_DIR = Path(os.environ.get("STATIC_DATA_DIR", "/geo-data"))

QUERY = """[out:json][timeout:180][bbox:40.5,49.5,55.5,87.5];
(
  relation["boundary"="administrative"]["admin_level"~"^[468]$"];
);
out geom;"""

_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# ─── Геометрия ────────────────────────────────────────────────────────────────

RDP_EPSILON   = 0.0003   # ~33 м — достаточно для визуализации
COORD_PREC    = 4        # 4 знака = ~11 м


def _pt_line_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / (dx*dx + dy*dy)))
    return math.hypot(p[0] - a[0] - t*dx, p[1] - a[1] - t*dy)


def _rdp(pts: list, eps: float) -> list:
    """Ramer-Douglas-Peucker simplification (iterative to avoid recursion limit)."""
    if len(pts) <= 2:
        return list(pts)
    stack = [(0, len(pts) - 1)]
    keep = {0, len(pts) - 1}
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        max_d, max_i = 0.0, lo
        for i in range(lo + 1, hi):
            d = _pt_line_dist(pts[i], pts[lo], pts[hi])
            if d > max_d:
                max_d, max_i = d, i
        if max_d > eps:
            keep.add(max_i)
            stack.append((lo, max_i))
            stack.append((max_i, hi))
    return [pts[i] for i in sorted(keep)]


def _simplify_ring(ring: list, eps: float = RDP_EPSILON, prec: int = COORD_PREC) -> list:
    if len(ring) < 4:
        return ring
    # Убираем замыкающую точку перед упрощением
    open_ring = ring[:-1] if ring[0] == ring[-1] or (
        abs(ring[0][0]-ring[-1][0]) < 1e-9 and abs(ring[0][1]-ring[-1][1]) < 1e-9
    ) else ring
    simplified = _rdp(open_ring, eps)
    # Округляем
    rounded = [[round(c[0], prec), round(c[1], prec)] for c in simplified]
    # Убираем дубликаты после округления
    deduped = [rounded[0]]
    for c in rounded[1:]:
        if c != deduped[-1]:
            deduped.append(c)
    # Замкнуть
    if deduped[0] != deduped[-1]:
        deduped.append(deduped[0])
    return deduped if len(deduped) >= 4 else ring  # fallback


def _bbox_of_ring(ring: list) -> list[float]:
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    return [min(lons), min(lats), max(lons), max(lats)]  # [W, S, E, N]


def _coords(geom_list):
    return [[g["lon"], g["lat"]] for g in geom_list]


def _close(a, b, eps=1e-6):
    return abs(a[0]-b[0]) < eps and abs(a[1]-b[1]) < eps


def _stitch(parts):
    if not parts:
        return []
    remaining = [list(p) for p in parts]
    ring = remaining.pop(0)
    for _ in range(len(remaining) * 4):
        if not remaining:
            break
        merged = False
        for i, seg in enumerate(remaining):
            if _close(ring[-1], seg[0]):
                ring.extend(seg[1:])
            elif _close(ring[-1], seg[-1]):
                ring.extend(reversed(seg[:-1]))
            elif _close(ring[0], seg[-1]):
                ring = seg[:-1] + ring
            elif _close(ring[0], seg[0]):
                ring = list(reversed(seg[1:])) + ring
            else:
                continue
            remaining.pop(i)
            merged = True
            break
        if not merged:
            ring.extend(remaining.pop(0))
    if not _close(ring[0], ring[-1]):
        ring.append(ring[0])
    return ring


def _relation_to_feature(rel: dict) -> dict | None:
    tags    = rel.get("tags", {})
    members = rel.get("members", [])

    outer_parts, inner_parts = [], []
    for m in members:
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        pts = _coords(m["geometry"])
        (inner_parts if m.get("role") == "inner" else outer_parts).append(pts)

    if not outer_parts:
        return None

    outer_ring = _stitch(outer_parts)
    if len(outer_ring) < 4:
        return None

    outer_ring = _simplify_ring(outer_ring)
    inner_rings = [_simplify_ring(_stitch([p])) for p in inner_parts
                   if len(_stitch([p])) >= 4]

    geometry = {"type": "Polygon", "coordinates": [outer_ring] + inner_rings}
    bbox = _bbox_of_ring(outer_ring)

    name = (tags.get("name") or tags.get("name:ru") or
            tags.get("name:kk") or f"osm:{rel['id']}")

    return {
        "type": "Feature",
        "properties": {
            "name":        name,
            "name_ru":     tags.get("name:ru", ""),
            "name_kk":     tags.get("name:kk", ""),
            "admin_level": tags.get("admin_level", ""),
            "osm_id":      rel["id"],
            # Pre-computed bbox [W, S, E, N] — используется JS для O(1) фильтрации по viewport
            "bbox":        bbox,
        },
        "geometry": geometry,
    }


# ─── Overpass ─────────────────────────────────────────────────────────────────

def _fetch_overpass() -> dict:
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "geo-districts-fetcher/1.0",
    }
    last_err: Exception | None = None
    for url in _OVERPASS_MIRRORS:
        try:
            with httpx.Client(timeout=200, follow_redirects=True) as client:
                r = client.post(url, content=body, headers=headers)
                r.raise_for_status()
                logger.info("Overpass OK: %s", url)
                return r.json()
        except Exception as exc:
            logger.warning("Overpass %s failed: %s", url, exc)
            last_err = exc
    raise last_err or RuntimeError("All Overpass mirrors failed")


# ─── Сохранение ───────────────────────────────────────────────────────────────

def _save(name: str, features: list[dict]) -> None:
    body = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False, separators=(",", ":")
    ).encode()

    plain = OUTPUT_DIR / name
    gz    = OUTPUT_DIR / (name + ".gz")

    tmp_p = plain.with_suffix(".tmp")
    tmp_g = gz.with_suffix(".tmp.gz")

    tmp_p.write_bytes(body)
    tmp_p.replace(plain)

    with gzip.open(tmp_g, "wb", compresslevel=6) as fh:
        fh.write(body)
    tmp_g.replace(gz)

    logger.info("Saved %d features → %s (%.1f KB plain, %.1f KB gzip)",
                len(features), name, plain.stat().st_size/1024, gz.stat().st_size/1024)


# ─── Главная функция ──────────────────────────────────────────────────────────

def generate() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Fetching Kazakhstan admin boundaries from Overpass...")
    t0  = time.time()
    osm = _fetch_overpass()
    logger.info("Overpass %.1fs — %d elements", time.time() - t0, len(osm.get("elements", [])))

    relations = [e for e in osm["elements"] if e["type"] == "relation"]
    logger.info("Converting %d relations (RDP ε=%.4f)...", len(relations), RDP_EPSILON)

    l4_features, l68_features = [], []
    for rel in relations:
        f = _relation_to_feature(rel)
        if not f:
            continue
        lvl = f["properties"].get("admin_level", "")
        if lvl == "4":
            l4_features.append(f)
        else:
            l68_features.append(f)

    logger.info("L4 (oblasts): %d  |  L6/8 (districts): %d", len(l4_features), len(l68_features))

    _save("districts_l4.geojson",  l4_features)
    _save("districts_l68.geojson", l68_features)

    total = len(l4_features) + len(l68_features)
    logger.info("Done. Total: %d features.", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        count = generate()
        print(f"Done: {count} features saved.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
