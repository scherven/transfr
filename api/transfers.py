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

from api import config
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
# Not a real verdict -- a placeholder for a transfer whose walkability hasn't been
# computed yet. `/journeys?assess=false` returns these so the itinerary list can
# render instantly; the client then streams each real verdict in via `/assess`.
PENDING = "pending"

# `unknown` reasons owned here (core/ contributes its own: platform_not_found,
# disconnected, exceeded_plausibility_bound, no_coordinates_for_platform_nodes).
NO_PLATFORM_DATA = "no_platform_data"
STATION_UNRESOLVED = "station_unresolved"
CROSS_STATION = "cross_station"
NO_TIMING = "no_timing"
IMPLAUSIBLE_WALK = "implausible_walk"

# Plausibility guard on a resolved walk. core/ resolves a track *ref* to OSM
# geometry without knowing where the journey said the platform actually is, so a
# same-labelled feature elsewhere (a bus bay lettered "C" across town, a mistagged
# platform) can resolve and route as a real-looking walk. We have the one thing
# core/ doesn't: the two platforms' own coordinates, hence their straight-line
# separation -- a hard lower bound on any honest transfer walk. A walk grossly
# longer than that is a mis-resolution, not a transfer, so we reject it as
# `unknown` rather than report a confidently-wrong distance. core/'s own
# `exceeded_plausibility_bound` can't catch this: it's a flat walking-time budget
# and never sees these coordinates. Bound = the larger of an absolute cap and a
# generous detour multiple of the gap; real same-station transfers measure <=150 m
# here (max: Munchen Ost 3->5 = 144 m), so 800 m clears them with wide margin
# while catching the 2 km Koblenz 9->C bug.
IMPLAUSIBLE_WALK_ABS_M = 800.0
IMPLAUSIBLE_WALK_DETOUR_FACTOR = 4.0
IMPLAUSIBLE_WALK_SLACK_M = 250.0


def walk_is_implausible(walk_distance_m: Optional[float], gap_m: float) -> bool:
    """True if a resolved walk is too long to be a real transfer between two
    platforms `gap_m` apart in a straight line -- i.e. the ref resolved to the
    wrong feature. Split out so the bound is unit-tested without a DB."""
    if walk_distance_m is None:
        return False
    bound = max(IMPLAUSIBLE_WALK_ABS_M, gap_m * IMPLAUSIBLE_WALK_DETOUR_FACTOR + IMPLAUSIBLE_WALK_SLACK_M)
    return walk_distance_m > bound


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


@dataclass
class WalkResolution:
    """The delay- and time-INDEPENDENT half of a transfer assessment: which
    station/platforms the two stops resolve to and the real platform-to-platform
    walk between them (or the reason there is none). Everything here depends only
    on coordinates + platform refs, never on the journey's clock -- so it is
    identical for the same change of train across every itinerary that contains
    it, and can be memoized across a search's journeys (see resolve_walk /
    api.pipeline.enrich). `reason` is None exactly when a walk was found."""
    reason: Optional[str] = None
    walk_time_s: Optional[float] = None
    walk_distance_m: Optional[float] = None
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


def resolve_walk(
    conn,
    *,
    arr_lat: Optional[float], arr_lon: Optional[float], arr_platform: Optional[str],
    dep_lat: Optional[float], dep_lon: Optional[float], dep_platform: Optional[str],
    algorithm: str = DEFAULT_ALGORITHM,
    max_search_seconds: Optional[float] = None,
) -> WalkResolution:
    """Resolve both platforms to a station + ref and walk-route between them --
    the expensive, clock-independent core of a transfer assessment (station
    lookup + core/ pathfind). Never raises: every gap degrades to a typed
    `reason` with `walk_time_s` left None. Split out from assess_transfer so its
    result -- the same for a given change of train regardless of when the trains
    run -- can be cached across a search's journeys."""
    arr_ref = map_track_to_ref(arr_platform)
    dep_ref = map_track_to_ref(dep_platform)
    res = WalkResolution(arrival_platform=arr_ref, departure_platform=dep_ref)

    # No platform on one side (e.g. FR/IT/ES feeds) -> nothing to route.
    if arr_ref is None or dep_ref is None:
        res.reason = NO_PLATFORM_DATA
        return res
    if arr_lat is None or arr_lon is None or dep_lat is None or dep_lon is None:
        res.reason = STATION_UNRESOLVED
        return res

    gap_m = haversine_meters(arr_lat, arr_lon, dep_lat, dep_lon)
    with conn.cursor() as cur:
        if gap_m > SAME_STATION_MAX_M:
            # Too far apart to be one station -- an inter-station walk core/
            # doesn't model. Resolve just the arrival end, for a station name.
            near = resolve_station_candidates(cur, [(arr_lat, arr_lon)], CANDIDATE_RADIUS_M, 1)
            if near:
                res.relation_id, res.station_name = near[0].relation_id, near[0].name
            res.reason = CROSS_STATION
            return res
        # Gather every stop_area relation near either end. A big station is often
        # several overlapping relations, and the two platforms can resolve closest
        # to different ones, so we try each until one relation's geometry actually
        # contains both platforms rather than giving up early.
        candidates = resolve_station_candidates(
            cur, [(arr_lat, arr_lon), (dep_lat, dep_lon)], CANDIDATE_RADIUS_M, CANDIDATE_LIMIT,
        )

    if not candidates:
        res.reason = STATION_UNRESOLVED
        return res

    # Report against the nearest station even if routing ultimately fails.
    res.relation_id = candidates[0].relation_id
    res.station_name = candidates[0].name

    kwargs = {"algorithm": algorithm, "use_stitch_bridges": config.STITCH_BRIDGES}
    if max_search_seconds is not None:
        kwargs["max_search_seconds"] = max_search_seconds

    first_reason = None
    saw_implausible = False
    for cand in candidates:
        result = find_shortest_path(conn, cand.relation_id, arr_ref, dep_ref, **kwargs)
        if result.get("found"):
            walk_m = result["walking_distance_meters"]
            if walk_is_implausible(walk_m, gap_m):
                # A "found" walk far longer than the platforms are apart means the
                # ref resolved to the wrong OSM feature (e.g. a same-lettered bus
                # bay elsewhere), not a real transfer. Don't report it; keep trying
                # other candidate relations, and fall through to an honest reason.
                saw_implausible = True
                continue
            res.relation_id = cand.relation_id
            res.station_name = cand.name
            res.walk_time_s = result["walking_time_seconds"]
            res.walk_distance_m = walk_m
            res.reason = None
            return res
        if first_reason is None:
            first_reason = result.get("reason", "not_found")

    # No candidate yielded a plausible walk. Prefer the precise diagnosis when we
    # actually resolved-and-rejected a bogus one (implausible_walk) over an
    # incidental not-found reason from another candidate (e.g. a search that ran
    # long) -- otherwise the surfaced reason would depend on candidate order.
    res.reason = IMPLAUSIBLE_WALK if saw_implausible else (first_reason or "platform_not_found")
    return res


def _resolve_walk_key(arr_lat, arr_lon, arr_platform, dep_lat, dep_lon,
                      dep_platform, algorithm, max_search_seconds):
    """Cache key for resolve_walk: exactly its inputs. Coords are rounded to ~0.1 m
    so the same stop (identical MOTIS coords across journeys) always hits."""
    def r(x):
        return round(x, 6) if x is not None else None
    return (r(arr_lat), r(arr_lon), arr_platform,
            r(dep_lat), r(dep_lon), dep_platform, algorithm, max_search_seconds)


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
    resolve_cache: Optional[dict] = None,
) -> TransferAssessment:
    """Resolve both platforms to a station + ref, walk-route between them, and
    classify against the layover. Never raises for missing data -- returns an
    `unknown` assessment carrying the reason instead.

    The walk resolution (the costly part) is clock-independent; pass a shared
    `resolve_cache` dict to memoize it across every journey in one search, so a
    change of train appearing in several itineraries is pathfound once. Only the
    layover and the final verdict are recomputed per call."""
    lay = layover_seconds(arr_time, dep_time)
    if resolve_cache is None:
        r = resolve_walk(
            conn, arr_lat=arr_lat, arr_lon=arr_lon, arr_platform=arr_platform,
            dep_lat=dep_lat, dep_lon=dep_lon, dep_platform=dep_platform,
            algorithm=algorithm, max_search_seconds=max_search_seconds,
        )
    else:
        key = _resolve_walk_key(arr_lat, arr_lon, arr_platform, dep_lat, dep_lon,
                                dep_platform, algorithm, max_search_seconds)
        r = resolve_cache.get(key)
        if r is None:
            r = resolve_walk(
                conn, arr_lat=arr_lat, arr_lon=arr_lon, arr_platform=arr_platform,
                dep_lat=dep_lat, dep_lon=dep_lon, dep_platform=dep_platform,
                algorithm=algorithm, max_search_seconds=max_search_seconds,
            )
            resolve_cache[key] = r

    base = TransferAssessment(
        verdict=UNKNOWN, layover_s=lay,
        relation_id=r.relation_id, station_name=r.station_name,
        arrival_platform=r.arrival_platform, departure_platform=r.departure_platform,
        walk_time_s=r.walk_time_s, walk_distance_m=r.walk_distance_m,
    )
    if r.walk_time_s is None:
        base.reason = r.reason
        return base
    base.verdict = classify(r.walk_time_s, lay, buffer_s)
    if base.verdict == UNKNOWN:
        base.reason = NO_TIMING  # walk known but layover wasn't parseable
    return base


# ---------------------------------------------------------------------------
# Live re-assessment under delay
#
# The real platform-to-platform walk is delay-invariant -- a late train doesn't
# move platform 8 away from platform 5. So the pathfinder (core/) runs ONCE at
# plan time and the walk is cached in a LiveTransfer. On every live update we
# only recompute the layover and re-classify -- pure arithmetic, no DB, no core/.
# The one exception is a platform change (a re-tracked train), which re-runs the
# pathfinder a single time against the new refs.
#
#     effective_layover = scheduled_layover - inbound_delay + outbound_delay
#     verdict           = classify(real_walk, effective_layover)
#
# `rescued` is the product moment: MOTIS's conservative minimum interchange time
# would drop the connection, but the real walk still makes it.
# ---------------------------------------------------------------------------


@dataclass
class LiveTransfer:
    """Static, delay-invariant facts about one change of train, computed once at
    plan time so re-assessment never re-runs the pathfinder unless a platform
    itself changes."""

    relation_id: Optional[int]
    arr_ref: Optional[str]
    dep_ref: Optional[str]
    walk_time_s: Optional[float]        # from core/, cached
    scheduled_layover_s: Optional[float]
    motis_assumed_s: Optional[float] = None   # MOTIS's own required transfer, if known
    buffer_s: float = DEFAULT_BUFFER_S
    station_name: Optional[str] = None

    @classmethod
    def from_assessment(cls, a: "TransferAssessment", *,
                        motis_assumed_s: Optional[float] = None,
                        buffer_s: float = DEFAULT_BUFFER_S) -> "LiveTransfer":
        """Build from an assess_transfer result (the plan-time -> live handoff)."""
        return cls(
            relation_id=a.relation_id, arr_ref=a.arrival_platform, dep_ref=a.departure_platform,
            walk_time_s=a.walk_time_s, scheduled_layover_s=a.layover_s,
            motis_assumed_s=motis_assumed_s, buffer_s=buffer_s, station_name=a.station_name,
        )


@dataclass
class LiveVerdict:
    verdict: str
    effective_layover_s: Optional[float]
    margin_s: Optional[float]        # slack over the raw walk -- the live countdown
    absorb_s: Optional[float]        # extra inbound delay still survivable (keeping the buffer)
    rescued: bool = False            # MOTIS would drop it; the real walk still makes it
    walk_time_s: Optional[float] = None
    replanned_walk: bool = False     # a platform change forced a fresh pathfind


def reassess(
    t: LiveTransfer,
    *,
    inbound_delay_s: float = 0.0,
    outbound_delay_s: float = 0.0,
    arr_track_now: Optional[str] = None,
    dep_track_now: Optional[str] = None,
    conn=None,
    algorithm: str = DEFAULT_ALGORITHM,
) -> LiveVerdict:
    """Re-score one transfer against live delays. Pure arithmetic unless a
    platform changed (then one fresh find_shortest_path). Never raises.

    inbound_delay_s  -- lateness of the arriving train (eats into the layover)
    outbound_delay_s -- lateness of the departing train (gives you more time)
    arr/dep_track_now -- current platform from realtime; if it differs from the
                         planned ref and a conn is given, the walk is recomputed.
    """
    walk = t.walk_time_s
    replanned = False

    # A platform change is the ONLY event that re-runs core/.
    new_arr = map_track_to_ref(arr_track_now) if arr_track_now is not None else None
    new_dep = map_track_to_ref(dep_track_now) if dep_track_now is not None else None
    changed = (new_arr is not None and new_arr != t.arr_ref) or \
              (new_dep is not None and new_dep != t.dep_ref)
    if changed and conn is not None and t.relation_id is not None:
        r = find_shortest_path(conn, t.relation_id,
                               new_arr or t.arr_ref, new_dep or t.dep_ref, algorithm=algorithm,
                               use_stitch_bridges=config.STITCH_BRIDGES)
        if r.get("found"):
            walk = r["walking_time_seconds"]
            t.walk_time_s = walk
            t.arr_ref = new_arr or t.arr_ref
            t.dep_ref = new_dep or t.dep_ref
            replanned = True

    if walk is None or t.scheduled_layover_s is None:
        return LiveVerdict(verdict=UNKNOWN, effective_layover_s=None, margin_s=None,
                           absorb_s=None, walk_time_s=walk, replanned_walk=replanned)

    eff = t.scheduled_layover_s - inbound_delay_s + outbound_delay_s
    verdict = classify(walk, eff, t.buffer_s)
    motis_would_drop = t.motis_assumed_s is not None and eff < t.motis_assumed_s
    return LiveVerdict(
        verdict=verdict,
        effective_layover_s=eff,
        margin_s=eff - walk,
        absorb_s=eff - walk - t.buffer_s,
        rescued=motis_would_drop and verdict in (FEASIBLE, TIGHT),
        walk_time_s=walk,
        replanned_walk=replanned,
    )
