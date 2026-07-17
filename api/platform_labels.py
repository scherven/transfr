"""Serve the harvested platform-label overlay (track number + real coordinate)
for the station map, so it can show every platform -- not just the few OSM tags.

The data is produced offline by `core/dbgen/harvest_platform_labels.py` (the feed
carries a track + coordinate for every stop; that script gathers them station-wide)
and dropped in a JSON file. This module loads it lazily, refreshes when the file
changes on disk (so a re-harvest lands without a restart), and answers "the
platforms at the station nearest this coordinate".

Honest degradation, like the rest of the API: when the file isn't present (nobody
has run the harvest on this host), every lookup returns None and the endpoint
reports `found=False` with a typed reason -- never an error.
"""

from __future__ import annotations

import json
import math
import os
import threading
from typing import Dict, List, Optional, Tuple

# Where the harvest output lives. Defaults to the repo-root file the script writes;
# point TRANSFR_PLATFORM_LABELS at a deployed copy to override.
_PATH = os.environ.get("TRANSFR_PLATFORM_LABELS", "platform_labels.json")

# A query coordinate farther than this from every harvested station has no overlay
# (better an honest "not covered" than a wrong station's platforms).
DEFAULT_MAX_DISTANCE_M = 1500.0

_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None
_cache_mtime: Optional[float] = None


def _haversine_m(la1: float, lo1: float, la2: float, lo2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


def _load() -> Dict[str, dict]:
    """The parsed overlay, reloaded only when the file's mtime changes. Missing or
    unreadable file -> an empty overlay (every lookup then misses cleanly)."""
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = os.path.getmtime(_PATH)
        except OSError:
            _cache, _cache_mtime = {}, None
            return _cache
        if _cache is None or mtime != _cache_mtime:
            try:
                with open(_PATH, encoding="utf-8") as f:
                    _cache = json.load(f)
            except (OSError, ValueError):
                _cache = {}
            _cache_mtime = mtime
        return _cache


def available() -> bool:
    """True if a non-empty overlay is loaded (the harvest has been run here)."""
    return bool(_load())


def nearest_station(lat: float, lon: float,
                    max_distance_m: float = DEFAULT_MAX_DISTANCE_M
                    ) -> Optional[Tuple[str, dict]]:
    """(name, entry) of the harvested station nearest (lat, lon) within
    max_distance_m, or None. `entry` is {"lat","lon","platforms":[...]}."""
    data = _load()
    best: Optional[Tuple[str, dict]] = None
    best_d = max_distance_m
    for name, entry in data.items():
        try:
            d = _haversine_m(lat, lon, entry["lat"], entry["lon"])
        except (KeyError, TypeError):
            continue
        if d <= best_d:
            best, best_d = (name, entry), d
    return best


def platform_markers(lat: float, lon: float,
                     max_distance_m: float = DEFAULT_MAX_DISTANCE_M
                     ) -> Optional[Tuple[str, List[dict]]]:
    """(station_name, [ {track, lat, lon, n}, ... ]) for the station nearest
    (lat, lon), or None when no harvested station is near / no overlay is loaded."""
    match = nearest_station(lat, lon, max_distance_m)
    if match is None:
        return None
    name, entry = match
    return name, list(entry.get("platforms", []))
