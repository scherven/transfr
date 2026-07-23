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

# One physical station complex can span several OSM station nodes -- Zürich HB is a
# "Hauptbahnhof" node plus a "HB SZU" node ~80 m away -- and the harvest keys a
# platform to whichever node is nearest, so the complex ends up split across a few
# overlay entries. When serving markers we therefore MERGE every entry within this
# radius of the nearest one, so a query for the station returns its whole platform
# set, not just the sub-node it happened to resolve to. Kept well under the spacing
# to the next distinct station (Zürich HB -> Selnau is ~840 m) to avoid over-merging.
MERGE_RADIUS_M = 300.0

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
                     max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
                     merge_radius_m: float = MERGE_RADIUS_M
                     ) -> Optional[Tuple[str, List[dict]]]:
    """(station_name, [ {track, lat, lon, n}, ... ]) for the station complex nearest
    (lat, lon), or None when nothing is near / no overlay is loaded.

    Aggregates every overlay entry within `merge_radius_m` of the nearest one (one
    station split across several OSM nodes -- see MERGE_RADIUS_M), deduping platforms
    by track. The returned name is the richest entry in the cluster (the main
    station, e.g. "Zürich Hauptbahnhof" rather than its "HB SZU" sub-node)."""
    anchor = nearest_station(lat, lon, max_distance_m)
    if anchor is None:
        return None
    _, aentry = anchor
    alat, alon = aentry["lat"], aentry["lon"]
    merged: Dict[str, dict] = {}
    disp_name, disp_count = None, -1
    for name, entry in _load().items():
        try:
            if _haversine_m(alat, alon, entry["lat"], entry["lon"]) > merge_radius_m:
                continue
        except (KeyError, TypeError):
            continue
        plats = entry.get("platforms", [])
        for p in plats:
            merged.setdefault(p.get("track"), p)  # first (nearest-ish) wins on a track clash
        if len(plats) > disp_count:
            disp_name, disp_count = name, len(plats)
    return disp_name, list(merged.values())


def track_coord(lat: float, lon: float, track: str,
                max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
                merge_radius_m: float = MERGE_RADIUS_M) -> Optional[Tuple[float, float]]:
    """The harvested coordinate of platform `track` at the station nearest
    (lat, lon), or None (no overlay here / no such track / it carries no coord).

    This is the seam that lets a walk be ROUTED to a platform OSM does not label.
    The station map draws a track's marker at this coordinate; handing the SAME
    coordinate to core/'s Tier-3 snap means anything we can draw, we can also
    route to -- otherwise the map advertises platforms `/walk` then rejects with
    `platform_not_found` (Zürich HB labels only 3 of ~40 platforms in OSM, so
    tracks like 8 and 10 exist on the map but nowhere in the routing graph)."""
    found = platform_markers(lat, lon, max_distance_m, merge_radius_m)
    if found is None:
        return None
    _, plats = found
    want = str(track)
    for p in plats:
        if str(p.get("track")) == want and p.get("lat") is not None and p.get("lon") is not None:
            return (p["lat"], p["lon"])
    return None
