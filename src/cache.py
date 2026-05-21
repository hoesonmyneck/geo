"""
SQLite-based geocoding cache.

Key: sha1(lower(street_name)|house|city)
Value: lat, lon, confidence, source, json_blob

Thread-safe via connection-per-thread pool pattern.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "cache.sqlite"

_local = threading.local()


@dataclass
class CacheEntry:
    addr_hash: str
    lat: float
    lon: float
    confidence: str   # high | medium | low | miss
    source: str       # nominatim | photon | old_name | fuzzy | alias+nominatim | etc.
    json_blob: str    # raw API response JSON string


def _get_conn() -> sqlite3.Connection:
    """Return a per-thread SQLite connection."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _init_schema(_local.conn)
    return _local.conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            addr_hash   TEXT PRIMARY KEY,
            lat         REAL,
            lon         REAL,
            confidence  TEXT,
            source      TEXT,
            json_blob   TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def make_hash(street_name: str, house: str, city: str) -> str:
    key = f"{street_name.lower().strip()}|{house.lower().strip()}|{city.lower().strip()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def get(addr_hash: str) -> Optional[CacheEntry]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT addr_hash, lat, lon, confidence, source, json_blob "
        "FROM geocode_cache WHERE addr_hash = ?",
        (addr_hash,),
    ).fetchone()
    if row is None:
        return None
    return CacheEntry(*row)


def put(entry: CacheEntry) -> None:
    conn = _get_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO geocode_cache
            (addr_hash, lat, lon, confidence, source, json_blob)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entry.addr_hash, entry.lat, entry.lon, entry.confidence, entry.source, entry.json_blob),
    )
    conn.commit()


def close() -> None:
    if hasattr(_local, "conn"):
        _local.conn.close()
        del _local.conn
