"""
Coordinate-based bridge from a MOTIS journey stop to the OSM station core/ needs.

A MOTIS stop carries a real lat/lon but a *name* that does not reliably match
OSM's ("Köln", "München Ostbahnhof", "Zürich HB" don't resolve by name; a hub
like Frankfurt Hbf is several overlapping relations). So we resolve by geography:
find the nearest stop_area centroid in the `station_points` index
(core/build_station_index.py) to the stop's coordinate.

No PostGIS on this deployment, so nearest-station is a btree bbox prefilter (a
small box around the coordinate -> a handful of candidate rows) plus an exact
haversine to pick the closest. `station_points` is ~333k rows, so this is cheap.
"""

import re
from dataclasses import dataclass
from math import cos, radians
from typing import List, Optional, Sequence, Tuple

from graph import haversine_meters

# A MOTIS stop farther than this from every known station centroid is treated as
# unresolvable (better a clean "station_unresolved" than a confidently wrong
# station). Platforms sit well within this of their station centroid.
DEFAULT_MAX_DISTANCE_M = 1200.0

_M_PER_DEG_LAT = 111_320.0  # ~metres per degree of latitude


@dataclass
class StationMatch:
    relation_id: int
    name: Optional[str]
    lat: float
    lon: float
    distance_m: float


def _bbox(lat: float, lon: float, radius_m: float):
    """A lat/lon bounding box of half-size `radius_m` around a point. Longitude
    degrees shrink with latitude, so scale them by cos(lat); clamp near the
    poles to avoid division blow-ups (irrelevant for European rail, but cheap)."""
    dlat = radius_m / _M_PER_DEG_LAT
    dlon = radius_m / (_M_PER_DEG_LAT * max(cos(radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def resolve_station_candidates(
    cur,
    points: Sequence[Tuple[float, float]],
    radius_m: float = DEFAULT_MAX_DISTANCE_M,
    limit: int = 6,
) -> List[StationMatch]:
    """The stop_area relations within `radius_m` of ANY of `points`, nearest
    first (distance = min over the points).

    A single physical station is often several overlapping OSM stop_area
    relations (München Ost, Basel SBB, Stuttgart Hbf), and its arrival and
    departure platforms can sit closest to *different* ones. Returning the whole
    nearby set -- rather than just the single nearest -- lets the caller try each
    until one relation's geometry actually contains both platforms, instead of
    wrongly giving up because the two ends resolved to different relations."""
    pts = [(la, lo) for la, lo in points if la is not None and lo is not None]
    if not pts:
        return []
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    dlat = radius_m / _M_PER_DEG_LAT
    worst_lat = max(abs(min(lats)), abs(max(lats)))
    dlon = radius_m / (_M_PER_DEG_LAT * max(cos(radians(worst_lat)), 0.01))
    cur.execute(
        "SELECT relation_id, name, lat, lon FROM station_points "
        "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
        (min(lats) - dlat, max(lats) + dlat, min(lons) - dlon, max(lons) + dlon),
    )
    out: List[StationMatch] = []
    for row in cur.fetchall():
        d = min(haversine_meters(la, lo, row["lat"], row["lon"]) for la, lo in pts)
        if d <= radius_m:
            out.append(StationMatch(row["relation_id"], row["name"], row["lat"], row["lon"], d))
    out.sort(key=lambda m: m.distance_m)
    return out[:limit]


def resolve_station(
    cur,
    lat: float,
    lon: float,
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
) -> Optional[StationMatch]:
    """The single nearest stop_area relation to (lat, lon), or None if none is
    within `max_distance_m`."""
    matches = resolve_station_candidates(cur, [(lat, lon)], max_distance_m, limit=1)
    return matches[0] if matches else None


# Leading platform-label words some feeds prepend to the bare number/letter OSM
# actually tags (DE "Gleis"/"Gl", regional "Regio", FR "Voie", IT "Binario",
# NL "Spoor"). Stripping to the bare ref is safe -- stations that already send a
# bare ref ("7", "3a", "A") don't match this and pass through unchanged -- and it
# also avoids core/'s track_ref LIKE fallback matching the wrong padded number.
_TRACK_PREFIX_RE = re.compile(
    r"^(gl|gleis|regio|bstg\.?|voie|quai|binario|bin\.?|spoor|via)\s+", re.IGNORECASE
)


def map_track_to_ref(track: Optional[str]) -> Optional[str]:
    """MOTIS platform/track label -> the OSM platform ref core/ matches on.

    Usually identical ("7", "3a"). A missing/empty track means 'no platform'.
    A known leading label word (e.g. Swiss "Gl 1", regional "Regio 3") is
    stripped to the bare ref OSM tags. core/'s _find_platform_edges_near still
    handles ref vs railway:track_ref and zero-padding from there.
    """
    if track is None:
        return None
    ref = str(track).strip()
    if not ref:
        return None
    stripped = _TRACK_PREFIX_RE.sub("", ref).strip()
    return stripped or ref


# The real platform sign a traveller reads lives on a stop_position / railway=stop
# NODE tagged with the public track number. When a feed labels the platform with a
# code OSM doesn't carry (Koeln Hbf's DELFI "84-91" for public tracks 1-11), the
# journey's own coordinate still sits on the right platform, so the nearest such
# node recovers the number to show. Radius must exceed the platform half-length --
# these stop nodes sit at one point along a 400 m platform, so the journey
# coordinate can be ~70 m away (measured at Koeln) -- hence a generous default.
_DISPLAY_LABEL_RADIUS_M = 160.0


def nearest_platform_label(cur, lat: float, lon: float,
                           radius_m: float = _DISPLAY_LABEL_RADIUS_M) -> Optional[str]:
    """The public track number of the heavy-rail platform nearest (lat, lon), for
    DISPLAY only -- recovering the real Gleis a feed mislabelled. Nearest numbered
    stop_position/railway=stop node within `radius_m`, excluding tram/subway stops
    (those renumber independently). None when none is near. This is a signage
    lookup, deliberately separate from routing, which anchors on walkable geometry
    (see core/ Tier 3); it is never used to resolve a path."""
    if lat is None or lon is None:
        return None
    min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, radius_m)
    cur.execute(
        "SELECT tags->>'ref' AS ref, lat, lon FROM osm_nodes "
        "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
        "  AND (tags->>'railway' IN ('stop','stop_position') "
        "       OR tags->>'public_transport' = 'stop_position') "
        "  AND tags->>'railway' IS DISTINCT FROM 'tram_stop' "
        "  AND tags->>'ref' ~ '^[0-9]+$'",
        (min_lat, max_lat, min_lon, max_lon),
    )
    best_ref, best_d = None, radius_m
    for row in cur.fetchall():
        d = haversine_meters(lat, lon, row["lat"], row["lon"])
        if d <= best_d:
            best_ref, best_d = row["ref"], d
    return best_ref
