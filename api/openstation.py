"""Serve the DB InfraGO "OpenStation" NeTEx crosswalk overlay: the German public
platform label + step-free / lift accessibility for a coordinate, DE-wide.

Produced offline by core/dbgen/ingest_openstation_netex.py from DB's CC0 NeTEx
bulk file and committed as openstation_labels.json (a sibling of the OSM-harvest
overlay platform_labels.json). This module loads it lazily, refreshes when the
file changes on disk, and answers two questions about the NeTEx quay nearest a
coordinate: its public track label, and whether it is step-free / has a lift.

It is a HIGHER-authority label source than the OSM stop-node recovery in
api/bridge.py -- it is the operator's own public signage crosswalk, so it fixes
DELFI's renumbered codes (Koeln "84-91" -> Gleis 1-11) even where OSM carries no
label. But most georeferenced NeTEx labels are ISLAND labels ("6/7"); the caller
(api/transfers) reconciles that with OSM's per-track sign, keeping the more
specific "7" when it is a component of the island "6/7".

Honest degradation, like the rest of the API: no file (nobody ran the ingest) or
a coordinate outside Germany / away from any georeferenced quay -> every lookup
returns None, never an error. Coverage caveat: DE-only, and only the ~11% of
quays that carry a coordinate (concentrated at the hubs where the renumbering
bug bites).
"""

from __future__ import annotations

import json
import math
import os
import threading
from typing import Dict, List, Optional, Tuple

# Where the ingest output lives. Defaults to the committed repo-root file; point
# TRANSFR_OPENSTATION at a deployed copy (or a test fixture) to override.
_PATH = os.environ.get("TRANSFR_OPENSTATION", "openstation_labels.json")

# How near a georeferenced quay must be for its label/accessibility to apply. The
# quay's point is a lift/stair centroid mid-platform, and the journey stop can sit
# some way along the same platform, so this matches the OSM display radius rather
# than being tight -- nearest still disambiguates adjacent islands (~17 m apart).
DEFAULT_QUAY_RADIUS_M = 160.0

_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None
_cache_mtime: Optional[float] = None
# Flattened [(lat, lon, quay, station_name), ...] rebuilt whenever _cache reloads,
# so a lookup is one linear scan rather than a nested station->quay walk.
_flat: Optional[List[Tuple[float, float, dict, Optional[str]]]] = None


def _haversine_m(la1: float, lo1: float, la2: float, lo2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


def _load() -> List[Tuple[float, float, dict, Optional[str]]]:
    """The flattened quay list, rebuilt only when the file's mtime changes. Missing
    or unreadable file -> an empty list (every lookup then misses cleanly)."""
    global _cache, _cache_mtime, _flat
    with _lock:
        try:
            mtime = os.path.getmtime(_PATH)
        except OSError:
            _cache, _cache_mtime, _flat = {}, None, []
            return _flat
        if _flat is None or mtime != _cache_mtime:
            try:
                with open(_PATH, encoding="utf-8") as f:
                    _cache = json.load(f)
            except (OSError, ValueError):
                _cache = {}
            flat: List[Tuple[float, float, dict, Optional[str]]] = []
            for name, entry in _cache.items():
                for q in entry.get("quays", []):
                    try:
                        flat.append((float(q["lat"]), float(q["lon"]), q, entry.get("name")))
                    except (KeyError, TypeError, ValueError):
                        continue
            _flat = flat
            _cache_mtime = mtime
        return _flat


def available() -> bool:
    """True if a non-empty overlay is loaded (the ingest has been run here)."""
    return bool(_load())


def nearest_quay(lat: Optional[float], lon: Optional[float],
                 max_distance_m: float = DEFAULT_QUAY_RADIUS_M
                 ) -> Optional[Tuple[dict, Optional[str], float]]:
    """(quay, station_name, distance_m) of the georeferenced NeTEx quay nearest
    (lat, lon) within max_distance_m, or None. `quay` is the stored dict
    {public_label, lat, lon, step_free, wheelchair, has_lift}."""
    if lat is None or lon is None:
        return None
    best: Optional[Tuple[dict, Optional[str], float]] = None
    best_d = max_distance_m
    for qlat, qlon, quay, station in _load():
        d = _haversine_m(lat, lon, qlat, qlon)
        if d <= best_d:
            best, best_d = (quay, station, d), d
    return best


def nearest_label(lat: Optional[float], lon: Optional[float],
                  radius_m: float = DEFAULT_QUAY_RADIUS_M) -> Optional[str]:
    """The public track label of the NeTEx quay nearest (lat, lon), or None. The
    label may be an island compound ("6/7"); the caller reconciles it with OSM."""
    hit = nearest_quay(lat, lon, radius_m)
    return hit[0].get("public_label") if hit else None


def accessibility_at(lat: Optional[float], lon: Optional[float],
                     radius_m: float = DEFAULT_QUAY_RADIUS_M) -> Optional[dict]:
    """{step_free, wheelchair, has_lift} for the NeTEx quay nearest (lat, lon), or
    None when no georeferenced quay is near / no overlay is loaded. Each flag is a
    tri-state bool | None (None = the feed didn't rate it)."""
    hit = nearest_quay(lat, lon, radius_m)
    if hit is None:
        return None
    q = hit[0]
    return {"step_free": q.get("step_free"), "wheelchair": q.get("wheelchair"),
            "has_lift": q.get("has_lift")}
