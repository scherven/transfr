"""
Assess one change-of-train: can you walk from the arrival platform to the
departure platform within the layover?

This is where the two halves meet. The bridge (api/bridge.py) turns each stop's
coordinate into the OSM relation core/ needs; core/'s find_shortest_path gives
the real walking time between the two platform edges; and the layover comes from
the journey's own timestamps. The verdict compares them.

Everything degrades to a typed `unknown` + reason rather than an error, so a
missing platform (FR/IT/ES), an unresolvable station, a cross-station interchange,
or one of core/'s own not-found reasons all surface cleanly to the caller.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from graph import haversine_meters
from ground_truth import find_shortest_path

from api.bridge import map_track_to_ref, resolve_station_candidates

# Safety margin on top of the raw walk: a transfer that is walkable only with
# zero seconds to spare is "tight", not "feasible".
DEFAULT_BUFFER_S = 60.0
DEFAULT_ALGORITHM = "astar"  # verified equivalent to the dijkstra baseline, faster

# Two interchange stops farther apart than this aren't one station -- it's an
# inter-station walk core/ doesn't route (e.g. Basel Bad Bf <-> Basel SBB).
SAME_STATION_MAX_M = 750.0
# How far around the interchange to gather candidate stop_area relations (a big
# station is often several overlapping ones), and how many to try.
CANDIDATE_RADIUS_M = 600.0
CANDIDATE_LIMIT = 6

# Verdicts
FEASIBLE = "feasible"
TIGHT = "tight"
INFEASIBLE = "infeasible"
UNKNOWN = "unknown"

# `unknown` reasons owned here (core/ contributes its own: platform_not_found,
# disconnected, exceeded_plausibility_bound, no_coordinates_for_platform_nodes).
NO_PLATFORM_DATA = "no_platform_data"
STATION_UNRESOLVED = "station_unresolved"
CROSS_STATION = "cross_station"
NO_TIMING = "no_timing"


@dataclass
class TransferAssessment:
    verdict: str
    reason: Optional[str] = None
    walk_time_s: Optional[float] = None
    walk_distance_m: Optional[float] = None
    layover_s: Optional[float] = None
    relation_id: Optional[int] = None
    station_name: Optional[str] = None
    arrival_platform: Optional[str] = None
    departure_platform: Optional[str] = None


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Z-tolerant ISO parse (datetime.fromisoformat rejects 'Z' before 3.11)."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def layover_seconds(arrival_iso: Optional[str], departure_iso: Optional[str]) -> Optional[float]:
    a, d = _parse_iso(arrival_iso), _parse_iso(departure_iso)
    if a is None or d is None:
        return None
    return (d - a).total_seconds()


def classify(walk_time_s: Optional[float], layover_s: Optional[float],
             buffer_s: float = DEFAULT_BUFFER_S) -> str:
    """Pure verdict from a known walk time and layover. Split out so the
    decision boundary is unit-tested independently of any DB or network."""
    if walk_time_s is None or layover_s is None:
        return UNKNOWN
    if layover_s < walk_time_s:
        return INFEASIBLE
    if layover_s < walk_time_s + buffer_s:
        return TIGHT
    return FEASIBLE


def assess_transfer(
    conn,
    *,
    arr_lat: Optional[float], arr_lon: Optional[float],
    arr_platform: Optional[str], arr_time: Optional[str],
    dep_lat: Optional[float], dep_lon: Optional[float],
    dep_platform: Optional[str], dep_time: Optional[str],
    buffer_s: float = DEFAULT_BUFFER_S,
    algorithm: str = DEFAULT_ALGORITHM,
    max_search_seconds: Optional[float] = None,
) -> TransferAssessment:
    """Resolve both platforms to a station + ref, walk-route between them, and
    classify against the layover. Never raises for missing data -- returns an
    `unknown` assessment carrying the reason instead."""
    lay = layover_seconds(arr_time, dep_time)
    arr_ref = map_track_to_ref(arr_platform)
    dep_ref = map_track_to_ref(dep_platform)

    base = TransferAssessment(
        verdict=UNKNOWN, layover_s=lay,
        arrival_platform=arr_ref, departure_platform=dep_ref,
    )

    # No platform on one side (e.g. FR/IT/ES feeds) -> nothing to route.
    if arr_ref is None or dep_ref is None:
        base.reason = NO_PLATFORM_DATA
        return base
    if arr_lat is None or arr_lon is None or dep_lat is None or dep_lon is None:
        base.reason = STATION_UNRESOLVED
        return base

    gap_m = haversine_meters(arr_lat, arr_lon, dep_lat, dep_lon)
    with conn.cursor() as cur:
        if gap_m > SAME_STATION_MAX_M:
            # Too far apart to be one station -- an inter-station walk core/
            # doesn't model. Resolve just the arrival end, for a station name.
            near = resolve_station_candidates(cur, [(arr_lat, arr_lon)], CANDIDATE_RADIUS_M, 1)
            if near:
                base.relation_id, base.station_name = near[0].relation_id, near[0].name
            base.reason = CROSS_STATION
            return base
        # Gather every stop_area relation near either end. A big station is often
        # several overlapping relations, and the two platforms can resolve closest
        # to different ones, so we try each until one relation's geometry actually
        # contains both platforms rather than giving up early.
        candidates = resolve_station_candidates(
            cur, [(arr_lat, arr_lon), (dep_lat, dep_lon)], CANDIDATE_RADIUS_M, CANDIDATE_LIMIT,
        )

    if not candidates:
        base.reason = STATION_UNRESOLVED
        return base

    # Report against the nearest station even if routing ultimately fails.
    base.relation_id = candidates[0].relation_id
    base.station_name = candidates[0].name

    kwargs = {"algorithm": algorithm}
    if max_search_seconds is not None:
        kwargs["max_search_seconds"] = max_search_seconds

    first_reason = None
    for cand in candidates:
        result = find_shortest_path(conn, cand.relation_id, arr_ref, dep_ref, **kwargs)
        if result.get("found"):
            base.relation_id = cand.relation_id
            base.station_name = cand.name
            base.walk_time_s = result["walking_time_seconds"]
            base.walk_distance_m = result["walking_distance_meters"]
            base.verdict = classify(base.walk_time_s, lay, buffer_s)
            if base.verdict == UNKNOWN:
                base.reason = NO_TIMING  # walk known but layover wasn't parseable
            return base
        if first_reason is None:
            first_reason = result.get("reason", "not_found")

    # No nearby relation contained both platforms -- the honest reason (usually
    # platform_not_found: the refs simply aren't in the OSM data here).
    base.reason = first_reason or "platform_not_found"
    return base
