"""
Boarding & step-off guidance for one transfer.

The platform-to-platform search seeds from EVERY node of the arrival platform
edge and returns the path from the best one, so the first node of the resulting
`node_path` is -- by construction -- the point on the arrival platform closest
(in walk time) to the departure platform: the optimal place to be standing when
the doors open. This module turns that node into actionable guidance: where along
the platform it sits, and how much platform-walking a traveller saves by being
there instead of at the far end.

WHAT IS AND ISN'T KNOWN.
  * The step-off POSITION is pure geometry transfr already has (the OSM platform
    edge + the resolved path), so it is available for any found walk on a
    cleanly-mapped platform edge.
  * The COACH that stops at that position needs a live train-formation feed
    (DB RIS::Transports, SBB, OeBB). That fetch is geo-blocked from a generic
    host (see core/boarding/live_sources.py), so `coach` stays None with a
    reason until a formation source is wired in. The normalized formation model
    (core/boarding/formation_model.py) is the drop-in target for that day; this
    module is the position half that works today.
  * The RESOLUTION + WIRING half is in place and unit-tested: given a
    formation, coach_at_offset (core/boarding/seat.py) maps the raw along-edge
    step-off offset to the coach whose span covers it. `compute_boarding` takes
    an optional `formation` / `formation_provider` (default: none, so behaviour
    is unchanged) and fills `coach` + `formation_source` the moment one resolves
    -- an injected formation in tests, or db_formation_provider() where the DB
    Wagenreihung host is reachable. Nothing more is gated on the feed than the
    feed itself.

Kept apart from the pathfinder: it consumes a resolved step-off node id plus the
arrival platform edge geometry SearchContext already loads, and never re-runs the
search. Every failure degrades to a typed reason (no fraction) rather than
raising, so a coarse-mapped platform simply yields position-less guidance.
"""

from dataclasses import dataclass, asdict
from typing import Callable, Dict, List, Optional, Tuple

from graph import WALKING_SPEED_MS, haversine_meters
from search_context import SearchContext, _ANCHOR_KEY
from seat import TrainFormation

Coord = Tuple[float, float]

# A zero-arg source of the arriving train's formation, returning None when the
# feed can't be reached (see db_formation_provider). `object` because the value
# is a NormalizedFormation (or an already-built TrainFormation) -- either resolves
# to a coach without this module importing the operator-feed layer eagerly.
FormationProvider = Callable[[], Optional[object]]

# How much platform-walking a good boarding position saves (vs the far platform
# end) before it's worth telling the traveller about. Below SIG_SOME_S any door
# is about the same walk; above SIG_HIGH_S it's a multi-half-minute difference.
SIG_HIGH_S = 40.0
SIG_SOME_S = 12.0

SIG_HIGH = "high"
SIG_SOME = "some"
SIG_LOW = "low"

# Reasons `coach` is absent / position is coarse.
NO_FORMATION_FEED = "no_formation_feed"            # position known, coach isn't (the norm)
PLATFORM_GEOMETRY_UNAVAILABLE = "platform_geometry_unavailable"  # no edge to measure along


@dataclass
class BoardingGuidance:
    """Where to be on the arriving train so you step off nearest the onward route.

    `stepoff_fraction` is oriented so 0 is the end of the platform *farthest* from
    the departure side and 1 is the end *nearest* it -- i.e. a larger fraction
    means "board further toward your connection". `time_saved_s` is the extra
    platform-walking you'd do stepping off at the far end instead; it is an upper
    bound on the benefit (it assumes one shared exit), hence framed as "up to".
    """

    arrival_platform: str
    departure_platform: str
    platform_length_m: float = 0.0
    stepoff_offset_m: float = 0.0
    stepoff_fraction: float = 0.0
    time_saved_s: float = 0.0
    significance: str = SIG_LOW
    # Filled only when a formation feed resolves the coach at the step-off point.
    coach: Optional[str] = None
    formation_source: Optional[str] = None
    # Why coach is None / guidance is coarse. Present even on success (the coach
    # gap), so the client can always explain what it is and isn't showing.
    reason: Optional[str] = None

    @property
    def has_position(self) -> bool:
        return self.platform_length_m > 0.0

    def as_dict(self) -> Dict:
        return asdict(self)


def classify_significance(time_saved_s: float) -> str:
    """How much boarding position matters, from the platform-walk it saves."""
    if time_saved_s >= SIG_HIGH_S:
        return SIG_HIGH
    if time_saved_s >= SIG_SOME_S:
        return SIG_SOME
    return SIG_LOW


def _cumulative(nodes: List[int], coords: Dict[int, Coord]) -> Tuple[List[int], List[float]]:
    """Ordered nodes that have coordinates, and the cumulative metre offset of
    each along the polyline from the first."""
    cn = [n for n in nodes if n in coords]
    cum = [0.0]
    for a, b in zip(cn, cn[1:]):
        cum.append(cum[-1] + haversine_meters(*coords[a], *coords[b]))
    return cn, cum


def offset_along_edge(
    nodes: List[int], coords: Dict[int, Coord], node: int
) -> Optional[Tuple[float, float]]:
    """(offset_m, length_m) of `node` along the edge polyline, or None if the
    node isn't on it or the edge has no length. Pure -- unit-tested without a DB."""
    cn, cum = _cumulative(nodes, coords)
    if len(cn) < 2 or node not in cn:
        return None
    length = cum[-1]
    if length <= 0.0:
        return None
    return cum[cn.index(node)], length


def _dep_anchor(coords: Dict[int, Coord], target_nodes) -> Optional[Coord]:
    """Centroid of the departure platform's nodes -- the 'toward your connection'
    reference used to orient the fraction. None if none have coordinates."""
    pts = [coords[n] for n in target_nodes if n in coords]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _orient(
    cn: List[int], offset_m: float, length_m: float,
    coords: Dict[int, Coord], dep_anchor: Optional[Coord],
) -> float:
    """Return the offset measured from the platform end FARTHEST from the
    departure side, so a larger fraction always means 'further toward your
    connection'. Without a departure anchor the raw offset is kept."""
    if dep_anchor is None:
        return offset_m
    end0, end1 = coords[cn[0]], coords[cn[-1]]
    d0 = haversine_meters(*end0, *dep_anchor)
    d1 = haversine_meters(*end1, *dep_anchor)
    # If end0 (offset 0) is the nearer-to-departure end, flip so it becomes 1.
    return (length_m - offset_m) if d0 < d1 else offset_m


def guidance_from_edge(
    arr_ref: str, dep_ref: str, edge_nodes: List[int], coords: Dict[int, Coord],
    stepoff_node: int, dep_anchor: Optional[Coord],
) -> Optional[BoardingGuidance]:
    """Build guidance from one arrival platform edge + the step-off node on it.
    Pure (no DB); returns None if the node isn't measurable on this edge."""
    measured = offset_along_edge(edge_nodes, coords, stepoff_node)
    if measured is None:
        return None
    offset_m, length_m = measured
    cn, _ = _cumulative(edge_nodes, coords)
    oriented = _orient(cn, offset_m, length_m, coords, dep_anchor)
    # Platform-walk penalty of the worst end: the longer leftover along the edge.
    time_saved = max(oriented, length_m - oriented) / WALKING_SPEED_MS
    return BoardingGuidance(
        arrival_platform=arr_ref,
        departure_platform=dep_ref,
        platform_length_m=round(length_m, 1),
        stepoff_offset_m=round(oriented, 1),
        stepoff_fraction=round(oriented / length_m, 3),
        time_saved_s=round(time_saved, 1),
        significance=classify_significance(time_saved),
        reason=NO_FORMATION_FEED,  # position known; coach needs a formation feed
    )


def _obtain_formation(formation, formation_provider: Optional[FormationProvider]):
    """The injected formation if one was given, else the provider's best-effort
    result, else None. Any provider failure (a geo-blocked feed, a network error,
    a FormationUnavailable) degrades to None so boarding stays position-only
    rather than failing the whole walk it enriches."""
    if formation is not None:
        return formation
    if formation_provider is None:
        return None
    try:
        return formation_provider()
    except Exception:  # noqa: BLE001 -- coach is progressive enhancement, never fatal
        return None


def _fill_coach(
    g: BoardingGuidance, formation, edge_nodes: List[int],
    coords: Dict[int, Coord], stepoff_node: int, sector_map,
) -> None:
    """Enrich a position guidance `g` in place with the coach at its step-off
    point. Resolves `formation` (a NormalizedFormation, or an already-built
    TrainFormation) to a coach span map on this platform, looks up the coach
    covering the step-off offset, and fills `g.coach` / `g.formation_source`,
    clearing the coach-gap `reason`. A no-op -- leaving `g` exactly as the
    position-only build produced it (reason NO_FORMATION_FEED) -- when the offset
    can't be measured or the coaches can't be placed on this platform.

    The offset used is the RAW along-edge offset from nodes[0], the same frame
    `coach_span_m` is measured in (both from the platform's reference end). It is
    deliberately NOT g.stepoff_offset_m, which has been re-oriented toward the
    departure side for display and would point at the wrong coach."""
    measured = offset_along_edge(edge_nodes, coords, stepoff_node)
    if measured is None:
        return
    raw_offset_m, length_m = measured
    try:
        tf = formation if isinstance(formation, TrainFormation) else \
            formation.to_train_formation(length_m, sector_map)
        coach = tf.coach_at_offset(raw_offset_m)
    except Exception:  # noqa: BLE001 -- an unplaceable coach / empty feed stays position-only
        return
    g.coach = str(coach)
    g.formation_source = getattr(formation, "source", None)
    g.reason = None  # coach resolved -- nothing is missing now


def db_formation_provider(
    train_number: str, when_yyyymmddhhmm: str, session=None,
) -> FormationProvider:
    """A zero-arg provider that fetches the arriving train's real DB coach
    formation on demand, returning None (never raising) when the feed can't be
    reached. THE PRODUCTION SEAM: once the arriving train's number + scheduled
    departure are threaded to the walk layer, wire this as compute_boarding's
    `formation_provider` and the coach lights up wherever the DB Wagenreihung host
    resolves (a DE egress / CI). From a generic host that host is geo-blocked, so
    this returns None and boarding stays position-only -- exactly today's
    behaviour, with zero code change on the day the feed becomes reachable."""
    def _provide():
        # Lazy import: keeps `requests` (and the whole operator-feed layer) off
        # the position-only path that runs on every walk.
        from live_sources import fetch_db_formation, FormationUnavailable
        try:
            return fetch_db_formation(train_number, when_yyyymmddhhmm, session=session)
        except FormationUnavailable:
            return None
    return _provide


def compute_boarding(
    conn, relation_id: int, arr_ref: str, dep_ref: str,
    stepoff_node: Optional[int],
    formation=None,
    formation_provider: Optional[FormationProvider] = None,
    sector_map=None,
) -> BoardingGuidance:
    """Resolve the arrival platform edge and locate the step-off node on it.

    `stepoff_node` is the first node of the resolved walk's `node_path` (echoed
    from the export the caller already built), so this never re-runs the search
    -- it only rebuilds SearchContext for the platform-edge geometry, which is
    the cheap setup half. Degrades to a position-less guidance with a reason
    when the platform isn't a measurable edge (a stop-position snap anchor, a
    single-node edge, or an unresolvable station).

    COACH ENRICHMENT (optional, best-effort). Pass the arriving train's
    formation as `formation` (a NormalizedFormation from parse_wagenreihung, or a
    TrainFormation), or a zero-arg `formation_provider` that yields one (see
    db_formation_provider). When either resolves, the coach stopping at the
    step-off point is filled into `coach` + `formation_source` and `reason` is
    cleared. Supplying neither -- the default, and the case on any non-DE host --
    leaves today's position-only behaviour untouched: `coach` None, `reason`
    no_formation_feed. `sector_map` places coaches a feed reports by sector letter
    rather than metres. The provider is only invoked once a measurable step-off
    edge is found, so a lazy network fetch never fires for a coarse platform."""
    coarse = BoardingGuidance(arrival_platform=arr_ref, departure_platform=dep_ref,
                              reason=PLATFORM_GEOMETRY_UNAVAILABLE)
    if stepoff_node is None:
        return coarse
    with conn.cursor() as cur:
        ctx = SearchContext(cur, relation_id, arr_ref, dep_ref)
        if ctx.error is not None:
            return coarse
        coords = ctx.coord_cache
        dep_anchor = _dep_anchor(coords, ctx.targets)
        for _wid, nodes, tags in ctx.edges_1:
            if _ANCHOR_KEY in tags:
                # A stop-position snap anchor collapses the platform to one node;
                # there's no edge to measure an offset along.
                continue
            if stepoff_node in nodes:
                g = guidance_from_edge(arr_ref, dep_ref, nodes, coords, stepoff_node, dep_anchor)
                if g is not None:
                    resolved = _obtain_formation(formation, formation_provider)
                    if resolved is not None:
                        _fill_coach(g, resolved, nodes, coords, stepoff_node, sector_map)
                    return g
    return coarse


def stepoff_node_of(export_doc: Dict) -> Optional[int]:
    """The resolved walk's step-off node -- the first of the path's node_ids -- or
    None when the walk wasn't found / carries no node ids."""
    path = (export_doc or {}).get("path") or {}
    if not path.get("found"):
        return None
    nodes = path.get("node_ids") or []
    return nodes[0] if nodes else None
