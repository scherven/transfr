"""
Shared setup for any graph-search algorithm.

Every pathfinding algorithm needs the same three things: the station's seed
geometry (from relation membership), the two platform edges' node sets
(source/target), and a way to fetch a node's neighbors from the database on
demand. This module does exactly that and nothing else -- no search
strategy lives here, so a new algorithm in core/algo_*.py only has to
implement *how to traverse* a graph, not *how to find* it.
"""

import re
from typing import Dict, List, Optional, Tuple

from graph import (
    Coords,
    NOT_WALKABLE_WAY_SQL,
    WALKING_SPEED_MS,
    Ways,
    bbox_from_coords,
    collapse_port_path,
    haversine_meters,
    is_elevator_way,
    is_vertical_node,
    is_walkable_way,
    node_vertical_kind,
    parse_levels,
    resolve_relation_ways_and_nodes,
    vertical_transition_cost,
    way_direction,
    way_node_levels,
    way_speed_and_penalty,
)

PLATFORM_EDGE_SEARCH_RADIUS_M = 600.0

# Plausibility bound on a platform-to-platform search (see plausibility_bound_seconds).
# A ref can resolve to the wrong OSM feature -- a same-lettered bus bay across
# town, a mistagged platform -- and route as a real-looking multi-kilometre walk.
# The straight-line distance between the two *resolved* platforms is a hard lower
# bound on any honest transfer, so a walk far longer than a generous multiple of
# it is a mis-resolution, not a transfer: the search abandons it as
# `exceeded_plausibility_bound` rather than returning it. This is a GEOMETRY bound,
# distinct from max_search_seconds (a caller-controlled compute budget); the
# search stops at whichever is tighter. Calibrated wide: real same-station
# transfers measured <=122 s of walking (max: Munchen Ost 3->5), so the 900 s
# floor clears them ~7x over, while the 2 km Koblenz 9->C bug (1501 s) is caught.
# The detour term only *relaxes* the bound further, for genuinely far-apart
# platforms at large stations.
PLAUSIBLE_TRANSFER_FLOOR_S = 900.0
PLAUSIBLE_TRANSFER_DETOUR_FACTOR = 3.0
PLAUSIBLE_TRANSFER_SLACK_S = 60.0


def find_station_relations(conn, name: str) -> List[int]:
    """Relation ids for stop_area/stop_area_group relations with this exact
    name. Can return more than one -- OSM station names are not unique --
    so callers should disambiguate deliberately rather than have this
    function silently pick one."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM osm_relations "
            "WHERE tags->>'name' = %s "
            "  AND tags->>'public_transport' IN ('stop_area', 'stop_area_group')",
            (name,),
        )
        return [r["id"] for r in cur.fetchall()]


def _track_ref_matches(track_ref: Optional[str], ref: str) -> bool:
    """railway:track_ref sometimes encodes composite refs like '412/422'
    where the trailing digits are the logical track number, so ref '12'
    should match '412/422'."""
    if not track_ref:
        return False
    return ref.zfill(2) in track_ref or ref in track_ref


# Platforms are tagged three ways in OSM, in decreasing precision / increasing
# breadth: railway=platform_edge (the boardable edge -- rarest, ~10k with a ref,
# but the most reliable), railway=platform (the rail platform surface, ~40k with
# a ref), and public_transport=platform (broadest, ~67k with a ref, but
# mode-agnostic -- can be a bus/tram platform). All are walkable (see
# graph.is_walkable_way), so any can anchor a platform-to-platform search. We try
# them precise-first so stations mapped with platform_edge are unaffected, and
# only fall back to the broader (area) tags when the precise lookup finds nothing.
_PLATFORM_AREA_SQL = "(tags->>'railway' = 'platform' OR tags->>'public_transport' = 'platform')"

# Any platform feature (edge OR area), for the compound-ref fallback below. Must
# match the partial predicate of the GIN token indexes in schema.sql verbatim so
# the planner uses them.
_ANY_PLATFORM_SQL = (
    "(tags->>'railway' IN ('platform', 'platform_edge') "
    "OR tags->>'public_transport' = 'platform')"
)
# SQL twin of _ref_tokens(): split ';'-joined refs into per-track tokens,
# trimming whitespace around the ';'. Kept byte-identical to the GIN index
# expressions in schema.sql (idx_osm_ways_ref_tokens / _local_ref_tokens) so the
# `@> ARRAY[track]` containment lookup is an index scan, not a seq scan over the
# whole ways table (EU has ~4.9k such compound platforms).
_REF_TOKENS_SQL = r"regexp_split_to_array(tags->>'ref', '\s*;\s*')"
_LOCAL_REF_TOKENS_SQL = r"regexp_split_to_array(tags->>'local_ref', '\s*;\s*')"


def _is_platform_area(tags: Dict[str, str]) -> bool:
    return tags.get("railway") == "platform" or tags.get("public_transport") == "platform"


# A platform serving several tracks is tagged with a ';'-joined ref OSM-style
# ('3;4' = one island platform, tracks 3 and 4). This is the norm for KR/JP
# island platforms and appears ~4.9k times in EU too, so a single track number
# ("3") must match either side of the ';'. Whitespace adjacent to the ';' is
# trimmed ('3; 4'), but an embedded space with NO ';' is preserved as one token
# -- 'Steig 1' / 'Voie 2' are single labels, not "track 1"/"track 2", and must
# never false-match the bare number. A plain ref yields a one-element list, so
# single-ref stations are matched byte-for-byte as before. The split must stay
# identical to the SQL one in _find_platform_edges_near (_REF_TOKENS_SQL) and the
# GIN token indexes in schema.sql.
_REF_SEPARATOR_RE = re.compile(r"\s*;\s*")


def _ref_tokens(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [t for t in _REF_SEPARATOR_RE.split(value) if t]


def _ref_token_matches(value: Optional[str], ref: str) -> bool:
    return ref in _ref_tokens(value)


def _ref_or_local_ref_matches(tags: Dict[str, str], ref: str) -> bool:
    return _ref_token_matches(tags.get("ref"), ref) or _ref_token_matches(tags.get("local_ref"), ref)


def find_platform_edges(ways: Ways, ref: str) -> List[Tuple[int, List[int]]]:
    """All ways in *ways* that represent platform `ref`: a platform_edge by ref
    or composite railway:track_ref first, else a railway=platform /
    public_transport=platform area by ref or local_ref. Returns every match
    rather than picking one, since some stations tag more than one way the same.

    Operates on an already-loaded Ways dict -- used by the eager algorithm
    and by tests. Live searches use _find_platform_edges_near() instead,
    which looks ways up directly rather than requiring them to already be
    loaded."""
    ref = str(ref)
    exact = [
        (way_id, info["nodes"])
        for way_id, info in ways.items()
        if info["tags"].get("railway") == "platform_edge" and _ref_token_matches(info["tags"].get("ref"), ref)
    ]
    if exact:
        return exact
    track_ref = [
        (way_id, info["nodes"])
        for way_id, info in ways.items()
        if info["tags"].get("railway") == "platform_edge"
        and _track_ref_matches(info["tags"].get("railway:track_ref"), ref)
    ]
    if track_ref:
        return track_ref
    return [
        (way_id, info["nodes"])
        for way_id, info in ways.items()
        if _is_platform_area(info["tags"]) and _ref_or_local_ref_matches(info["tags"], ref)
    ]


# Tier 2: some stations record a track number only on a stop_position /
# railway=stop NODE, which sits on the (un-imported) track and is isolated from
# the walkable graph. We find that node by ref near the station, then snap its
# coordinate to the nearest node that IS in the walkable graph within this radius
# -- the platform surface (a footway or a platform area) beside the track -- and
# route from there. See core/PLATFORM-RESOLUTION.md. Needs the station_stops table
# and an osm_nodes coordinate index (core/build_platform_index.py).
STOP_SNAP_RADIUS_M = 40.0

# When a platform is resolved by snapping (Tier 2) we return the snap node's
# WHOLE way -- so SearchContext loads real geometry to traverse -- but tag the
# result with the single anchor node the source/target should actually be, so two
# tracks on one island platform anchor to different points (the real cross-platform
# walk) instead of the whole shared way collapsing to a zero-distance overlap.
_ANCHOR_KEY = "_snap_anchor"


def _nearest_stop_coord(cur, ref: str, bbox) -> Optional[Tuple[float, float]]:
    """The coordinate of a stop_position/railway=stop node tagged with this track
    ref, within the station-seed bbox -- i.e. 'where is track `ref` here'."""
    min_lat, max_lat, min_lon, max_lon = bbox
    cur.execute(
        "SELECT lat, lon FROM station_stops "
        "WHERE ref = %s AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
        (ref, min_lat, max_lat, min_lon, max_lon),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    clat, clon = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2
    best = min(rows, key=lambda r: haversine_meters(clat, clon, r["lat"], r["lon"]))
    return (best["lat"], best["lon"])


def _nearest_walkable_way(cur, lat: float, lon: float, coord_cache: Coords):
    """The walkable way owning the node nearest to (lat, lon) within
    STOP_SNAP_RADIUS_M, as a full (way_id, nodes, tags) triple with its node
    coords cached -- i.e. the platform surface (a footway or a platform area)
    beside where this track's train stops.

    The track's stop node is isolated (on the un-imported track), and so are any
    other track nodes nearby, so we skip candidate nodes that touch no walkable
    way; the first walkable one is the platform edge. We return that node's whole
    way (not just the node) so SearchContext loads the real geometry to route
    across; two different tracks usually own different ways, so the transfer is
    the real platform-to-platform walk. (Two tracks on a single island platform
    resolve to the same way and read as ~0 m -- correctly 'feasible', just not the
    few-metre cross-platform figure.)"""
    min_lat, max_lat, min_lon, max_lon = bbox_from_coords({0: (lat, lon)}, STOP_SNAP_RADIUS_M)
    cur.execute(
        "SELECT id, lat, lon FROM osm_nodes "
        "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
        (min_lat, max_lat, min_lon, max_lon),
    )
    rows = sorted(cur.fetchall(), key=lambda r: haversine_meters(lat, lon, r["lat"], r["lon"]))
    for r in rows:
        if haversine_meters(lat, lon, r["lat"], r["lon"]) > STOP_SNAP_RADIUS_M:
            break
        cur.execute("SELECT way_ids FROM node_way_ids WHERE node_id = %s", (r["id"],))
        nw = cur.fetchone()
        if not nw or not nw["way_ids"]:
            continue
        cur.execute(
            f"SELECT id, nodes, tags FROM osm_ways WHERE id = ANY(%s) AND {NOT_WALKABLE_WAY_SQL} LIMIT 1",
            (list(nw["way_ids"]),),
        )
        w = cur.fetchone()
        if w:
            nodes = list(w["nodes"])
            missing = [n for n in nodes if n not in coord_cache]
            if missing:
                cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (missing,))
                for x in cur.fetchall():
                    coord_cache[x["id"]] = (x["lat"], x["lon"])
            return (w["id"], nodes, {**(w["tags"] or {}), _ANCHOR_KEY: r["id"]})
    return None


# The per-ref platform-edge lookup is a SearchContext method
# (SearchContext._find_platform_edges_near): it needs the station's resolved
# footprint (its in-bbox node coords and the set of way ids touching them),
# computed once in _setup and shared by both refs. See that method for the tag
# ladder and why candidates are bounded to the station's own ways.

# The six tag predicates tried, precise-first (see the _PLATFORM_AREA_SQL
# comment): railway=platform_edge by ref then composite railway:track_ref, then
# -- only if nothing precise matched -- a railway=platform /
# public_transport=platform AREA by ref then local_ref, then the compound-ref
# ('3;4') token-containment fallbacks (GIN-indexed, see schema.sql). Each is a
# fragment of a WHERE clause with a single %s for the ref-derived parameter; the
# lookup ANDs an `id = ANY(station_way_ids)` bound in front so a common track
# number never matches same-numbered platforms elsewhere in Europe.
def _platform_edge_attempts(ref: str):
    return (
        ("tags->>'railway' = 'platform_edge' AND tags->>'ref' = %s", str(ref)),
        ("tags->>'railway' = 'platform_edge' AND tags->>'railway:track_ref' LIKE %s", f"%{str(ref).zfill(2)}%"),
        (f"{_PLATFORM_AREA_SQL} AND tags->>'ref' = %s", str(ref)),
        (f"{_PLATFORM_AREA_SQL} AND tags->>'local_ref' = %s", str(ref)),
        (f"{_ANY_PLATFORM_SQL} AND {_REF_TOKENS_SQL} @> ARRAY[%s]", str(ref)),
        (f"{_ANY_PLATFORM_SQL} AND {_LOCAL_REF_TOKENS_SQL} @> ARRAY[%s]", str(ref)),
    )


# Natural sort so platform refs order the way a human reads them ("2" before
# "10", "3a" just after "3"), not lexicographically ("10" before "2"). A ref
# splits into alternating digit/non-digit chunks; digit chunks compare as ints
# and sort ahead of any purely-alphabetic chunk at the same position.
def _natural_key(ref: str):
    return [
        (0, int(tok), "") if tok.isdigit() else (1, 0, tok.lower())
        for tok in re.findall(r"\d+|\D+", ref)
    ]


def list_platform_refs(cur, relation_id: int) -> List[str]:
    """Every platform ref tagged at this station, naturally sorted.

    Resolves the station's own footprint exactly as a platform-to-platform
    search does (the same seed geometry -> padded bbox -> in-bbox ways chain as
    SearchContext._setup), then collects the ref/local_ref of every platform way
    (edge OR area) inside it. So the refs returned are precisely the ones a
    `/walk` between them can resolve -- the walk-only door lists real, routable
    platforms rather than free-form guesses. Compound island-platform refs
    ('3;4') are split into their per-track tokens (see `_ref_tokens`)."""
    seed_way_ids, seed_node_ids = resolve_relation_ways_and_nodes(cur, relation_id)
    seed_nodes = set(seed_node_ids)
    if seed_way_ids:
        cur.execute("SELECT nodes FROM osm_ways WHERE id = ANY(%s)", (list(seed_way_ids),))
        for row in cur.fetchall():
            seed_nodes.update(row["nodes"])
    if not seed_nodes:
        return []

    cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (list(seed_nodes),))
    coords = {r["id"]: (r["lat"], r["lon"]) for r in cur.fetchall()}
    if not coords:
        return []

    min_lat, max_lat, min_lon, max_lon = bbox_from_coords(coords, PLATFORM_EDGE_SEARCH_RADIUS_M)
    cur.execute(
        "SELECT id FROM osm_nodes WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
        (min_lat, max_lat, min_lon, max_lon),
    )
    node_ids = [r["id"] for r in cur.fetchall()]
    if not node_ids:
        return []

    cur.execute(
        "SELECT DISTINCT unnest(way_ids) AS w FROM node_way_ids WHERE node_id = ANY(%s)",
        (node_ids,),
    )
    way_ids = [r["w"] for r in cur.fetchall()]
    if not way_ids:
        return []

    cur.execute(
        f"SELECT tags FROM osm_ways WHERE id = ANY(%s) AND {_ANY_PLATFORM_SQL}",
        (way_ids,),
    )
    refs: set = set()
    for row in cur.fetchall():
        tags = row["tags"] or {}
        for tag_key in ("ref", "local_ref"):
            refs.update(_ref_tokens(tags.get(tag_key)))
    return sorted(refs, key=_natural_key)


class SearchContext:
    """Resolved source/target nodes for one platform-to-platform query,
    plus lazy (database-backed) neighbor access shared by every algorithm.

    If setup fails (platform not found / no coordinates), `.error` is set
    to the result dict callers should return -- check it before using
    `.sources` / `.targets`.
    """

    def __init__(self, cur, relation_id: int, ref_1: str, ref_2: str, use_adjacency_table: bool = True,
                 use_stitch_bridges: bool = False, avoid_elevators: bool = False):
        self.cur = cur
        self.relation_id = relation_id
        # Step-free routing: when True, neighbors() omits every elevator, both
        # the way-mapped kind (highway/railway=elevator) and the node-mapped kind
        # (a shared vertical node tagged as an elevator). The search then routes
        # over stairs/escalators/ramps only -- or returns `disconnected` if the
        # sole vertical link between the two platforms is an elevator. Off by
        # default, so a normal search is byte-for-byte unchanged.
        self.avoid_elevators = avoid_elevators
        # Opt-in synthetic stitch bridges (core/build_stitch_bridges.py): short
        # edges joining a pedestrian connector node that lies inside a platform
        # polygon to that platform, where OSM mapped them overlapping but sharing
        # no node. Off by default, so a search routes byte-for-byte as before
        # unless a caller explicitly asks for the stitches. node -> [(partner, weight_s)].
        self.use_stitch_bridges = use_stitch_bridges
        self.bridges: Dict[int, List[Tuple[int, float]]] = {}
        # expand() has two implementations, selectable per-instance so both
        # can be benchmarked head to head against the same fixtures:
        #   True (default): point-lookup node_way_ids (see
        #     core/build_node_way_ids.py) for the candidate way ids touching
        #     u, then fetch only those rows from osm_ways by id -- two cheap
        #     PK-ish lookups instead of one scan. Measured 1.3x-11.7x faster
        #     than the GIN path across every real test station (see
        #     tests/test_ground_truth.py's test_adjacency_table_agrees_with_gin_scan),
        #     with identical results. Requires node_way_ids to exist and be
        #     rebuilt after any osm_ways reload (see schema.sql).
        #   False: the original GIN "nodes && ARRAY[u]" bitmap-heap-scan
        #     over the full osm_ways table -- kept as a fallback/cross-check,
        #     e.g. if node_way_ids hasn't been built yet or is suspected stale.
        self.use_adjacency_table = use_adjacency_table
        self.way_cache: Ways = {}
        self.coord_cache: Coords = {}
        # node_id -> tags, cached alongside coords. Only needed to recognise
        # nodes tagged as vertical circulation (elevator/stairs/escalator),
        # which get split into per-level ports (see graph.is_vertical_node).
        self.node_tags: Dict[int, Dict[str, str]] = {}
        self.known_way_ids: set = set()
        self.node_to_ways: Dict[int, set] = {}
        # Nodes we've actually run the "find every way touching this node"
        # query for -- deliberately NOT the same thing as "node_to_ways has
        # an entry for this node". A node can pick up a node_to_ways entry
        # just by being *part of* some other way we fetched, without us
        # ever having queried *from* that node -- if we treated "has an
        # entry" as "fully expanded" we'd silently stop looking for other
        # ways through it and could report a real connection as missing.
        self.queried_nodes: set = set()
        self.error: Optional[Dict] = None
        self.edges_1: List[Tuple[int, List[int], dict]] = []
        self.edges_2: List[Tuple[int, List[int], dict]] = []
        self.sources: set = set()
        self.targets: set = set()
        # The station's own footprint, resolved once in _setup and shared by
        # both refs' platform lookups (see _load_station_footprint). The in-bbox
        # node ids bound platform candidates to this station; with the adjacency
        # table they're mapped to a way-id set the lookups filter on in SQL,
        # otherwise the (Europe-wide) ref matches are filtered against the node
        # set in Python -- either way a same-ref platform elsewhere is rejected.
        self._seed_bbox: Optional[Tuple[float, float, float, float]] = None
        self._inbbox_nodes: set = set()
        self._station_way_ids: Optional[set] = None
        self._setup(ref_1, ref_2)

    def _setup(self, ref_1: str, ref_2: str) -> None:
        cur = self.cur
        # Seed with the relation's own members -- just the station's own
        # tagged infrastructure (platforms, stop positions), not a
        # neighborhood. Everything else is discovered on demand.
        seed_way_ids, seed_node_ids = resolve_relation_ways_and_nodes(cur, self.relation_id)

        if seed_way_ids:
            cur.execute("SELECT id, nodes, tags FROM osm_ways WHERE id = ANY(%s)", (list(seed_way_ids),))
            for row in cur.fetchall():
                tags = row["tags"] or {}
                if not is_walkable_way(tags):
                    continue  # e.g. public_transport=station -- the whole station building, not a path
                self.way_cache[row["id"]] = {"nodes": list(row["nodes"]), "tags": tags}
                self.known_way_ids.add(row["id"])
                for n in set(row["nodes"]):
                    self.node_to_ways.setdefault(n, set()).add(row["id"])

        seed_nodes = set(seed_node_ids)
        for info in self.way_cache.values():
            seed_nodes.update(info["nodes"])
        self._load_nodes(seed_nodes)

        # Resolve the station's own footprint once: a bbox around its seed
        # geometry, the node ids inside it, and (with the adjacency table) the
        # way ids touching them. Every platform-edge lookup below is bounded to
        # this station. Without it, a common track number ('2') matches every
        # platform so-numbered across the whole extract and we'd load hundreds of
        # thousands of their node coordinates just to discard all but the local
        # few (the dominant cost before this bound existed).
        self._seed_bbox = self._compute_seed_bbox(seed_nodes)
        self._inbbox_nodes = self._load_inbbox_nodes(self._seed_bbox)
        if self.use_adjacency_table:
            self._station_way_ids = self._station_way_ids_from(self._inbbox_nodes)

        # platform_edge ways are usually NOT relation members -- they're
        # associated with a station only by sharing nodes with its platform
        # ways. Rather than discovering them by expanding outward from the
        # seed (slow -- see core/ground_truth.py's module docstring), look
        # them up directly by the indexed (railway=platform_edge, ref) pair,
        # restricted to the station's own ways.
        self.edges_1 = self._find_platform_edges_near(ref_1)
        self.edges_2 = self._find_platform_edges_near(ref_2)
        if not self.edges_1 or not self.edges_2:
            self.error = {
                "found": False,
                "reason": "platform_not_found",
                "ref_1_matches": len(self.edges_1),
                "ref_2_matches": len(self.edges_2),
            }
            return

        for way_id, nodes, tags in self.edges_1 + self.edges_2:
            self.way_cache[way_id] = {"nodes": nodes, "tags": tags}
            self.known_way_ids.add(way_id)
            for n in set(nodes):
                self.node_to_ways.setdefault(n, set()).add(way_id)

        self.sources = self._anchor_nodes(self.edges_1)
        self.targets = self._anchor_nodes(self.edges_2)
        if not self.sources or not self.targets:
            self.error = {"found": False, "reason": "no_coordinates_for_platform_nodes"}
            return
        if self.use_stitch_bridges:
            self._load_stitch_bridges()

    def _compute_seed_bbox(self, seed_nodes: set):
        """A bbox around the station's seed geometry (its own tagged
        infrastructure), padded by PLATFORM_EDGE_SEARCH_RADIUS_M. None when no
        seed node has a known coordinate -- then no platform can be resolved,
        exactly as before."""
        seed_coords = {n: self.coord_cache[n] for n in seed_nodes if n in self.coord_cache}
        if not seed_coords:
            return None
        return bbox_from_coords(seed_coords, PLATFORM_EDGE_SEARCH_RADIUS_M)

    def _load_inbbox_nodes(self, bbox) -> set:
        """The ids of every node inside the station bbox. These bound platform
        resolution to the station: a platform way whose ref matches but which
        touches none of these nodes belongs to a different station. Only ids are
        needed here -- matched platforms' coords are loaded lazily, and the search
        loads the rest on demand -- so a dense station's whole footprint is never
        materialised."""
        if bbox is None:
            return set()
        min_lat, max_lat, min_lon, max_lon = bbox
        self.cur.execute(
            "SELECT id FROM osm_nodes WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
            (min_lat, max_lat, min_lon, max_lon),
        )
        return {r["id"] for r in self.cur.fetchall()}

    def _station_way_ids_from(self, node_ids: set) -> set:
        """The way ids touching any in-bbox node, via node_way_ids (the same
        node->ways adjacency the adjacency-table search uses). Lets the platform
        lookups bound candidates in SQL (`id = ANY(...)`). Only used when
        use_adjacency_table is set; the GIN path filters in Python instead."""
        if not node_ids:
            return set()
        self.cur.execute(
            "SELECT DISTINCT unnest(way_ids) AS w FROM node_way_ids WHERE node_id = ANY(%s)",
            (list(node_ids),),
        )
        return {r["w"] for r in self.cur.fetchall()}

    def _find_platform_edges_near(self, ref: str) -> List[Tuple[int, List[int], Dict[str, str]]]:
        """Resolve platform `ref` to (way_id, nodes, tags) triples at THIS
        station, precise-first over the tag ladder (see _platform_edge_attempts).

        Every lookup is restricted to `self._station_way_ids`, so a common track
        number never matches same-numbered platforms elsewhere in the extract.
        Falls back to the Tier 2 stop-position snap (bbox-local) when no platform
        way carries the ref. Returns [] -> caller reports platform_not_found."""
        ref = str(ref)
        for predicate_sql, param in _platform_edge_attempts(ref):
            hit = self._candidates_in_station(predicate_sql, param)
            if hit:
                return hit

        # Tier 2: the ref wasn't on any platform way at the station; it may live
        # only on a stop_position node. Resolve that, then snap to the nearest
        # platform. Bounded by the same seed bbox.
        if self._seed_bbox is not None:
            stop = _nearest_stop_coord(self.cur, ref, self._seed_bbox)
            if stop is not None:
                snapped = _nearest_walkable_way(self.cur, stop[0], stop[1], self.coord_cache)
                if snapped is not None:
                    return [snapped]
        return []

    def _candidates_in_station(self, predicate_sql: str, param) -> List[Tuple[int, List[int], Dict[str, str]]]:
        """Ways matching one tag predicate AND belonging to this station's
        footprint. Loads coords/tags only for the (few) matched ways' nodes still
        missing -- never for the Europe-wide same-ref match set.

        With the adjacency table, candidates are bounded in SQL by the station's
        precomputed way-id set. Without it (the node_way_ids escape hatch), the
        ref predicate is run unbounded and the matches are filtered against the
        in-bbox node set in Python -- the same station bound, no giant array
        pushed into a GIN scan (which the planner mishandles into a seq scan)."""
        if not self._inbbox_nodes:
            return []
        if self.use_adjacency_table:
            if not self._station_way_ids:
                return []
            self.cur.execute(
                f"SELECT id, nodes, tags FROM osm_ways WHERE id = ANY(%s) AND {predicate_sql}",
                (list(self._station_way_ids), param),
            )
            rows = self.cur.fetchall()
        else:
            self.cur.execute(f"SELECT id, nodes, tags FROM osm_ways WHERE {predicate_sql}", (param,))
            rows = [row for row in self.cur.fetchall()
                    if any(n in self._inbbox_nodes for n in row["nodes"])]
        if not rows:
            return []
        missing = {n for row in rows for n in row["nodes"] if n not in self.coord_cache}
        if missing:
            self._load_nodes(missing)
        return [(row["id"], list(row["nodes"]), row["tags"] or {}) for row in rows]

    def _load_stitch_bridges(self) -> None:
        """Load synthetic stitch bridges near the resolved platforms into
        `self.bridges` (bidirectional), caching each endpoint's coords so the
        search and its heuristic can use them immediately. No-op if the table
        hasn't been built."""
        cur = self.cur
        cur.execute("SELECT to_regclass('synthetic_bridges') AS t")
        if cur.fetchone()["t"] is None:
            return
        anchors = {n: self.coord_cache[n] for n in (self.sources | self.targets) if n in self.coord_cache}
        if not anchors:
            return
        min_lat, max_lat, min_lon, max_lon = bbox_from_coords(anchors, PLATFORM_EDGE_SEARCH_RADIUS_M)
        cur.execute(
            "SELECT b.node_a, b.node_b, b.dist_m, "
            "na.lat AS a_lat, na.lon AS a_lon, nb.lat AS b_lat, nb.lon AS b_lon "
            "FROM synthetic_bridges b "
            "JOIN osm_nodes na ON na.id = b.node_a "
            "JOIN osm_nodes nb ON nb.id = b.node_b "
            "WHERE na.lat BETWEEN %s AND %s AND na.lon BETWEEN %s AND %s",
            (min_lat, max_lat, min_lon, max_lon),
        )
        for r in cur.fetchall():
            a, b, weight = r["node_a"], r["node_b"], r["dist_m"] / WALKING_SPEED_MS
            self.coord_cache.setdefault(a, (r["a_lat"], r["a_lon"]))
            self.coord_cache.setdefault(b, (r["b_lat"], r["b_lon"]))
            self.bridges.setdefault(a, []).append((b, weight))
            self.bridges.setdefault(b, []).append((a, weight))

    def _anchor_nodes(self, edges) -> set:
        """Source/target nodes for a resolved platform: the single snap anchor
        when it was resolved by snapping (Tier 2 tags the edge with _ANCHOR_KEY),
        else every platform node (platform_edge / platform-area matches)."""
        out: set = set()
        for _, nodes, tags in edges:
            anchor = tags.get(_ANCHOR_KEY)
            if anchor is not None:
                if anchor in self.coord_cache:
                    out.add(anchor)
            else:
                out.update(n for n in nodes if n in self.coord_cache)
        return out

    def expand(self, u: int) -> None:
        """Fetch (and cache) every way touching node u, the first time u is
        actually reached by a search. Safe to call repeatedly -- a no-op
        after the first call for a given node."""
        if u in self.queried_nodes:
            return
        self.queried_nodes.add(u)
        rows = self._expand_via_adjacency_table(u) if self.use_adjacency_table else self._expand_via_gin_scan(u)
        new_node_ids = set()
        for row in rows:
            self.way_cache[row["id"]] = {"nodes": list(row["nodes"]), "tags": row["tags"] or {}}
            self.known_way_ids.add(row["id"])
            for n in set(row["nodes"]):
                self.node_to_ways.setdefault(n, set()).add(row["id"])
                if n not in self.coord_cache:
                    new_node_ids.add(n)
        self._load_nodes(new_node_ids)

    def _load_nodes(self, node_ids) -> None:
        """Fetch and cache coords AND tags for a set of nodes in one query.
        Tags are cached so vertical-circulation nodes can be recognised the
        moment the search first sees them, without a second round trip."""
        node_ids = [n for n in node_ids if n not in self.coord_cache]
        if not node_ids:
            return
        self.cur.execute("SELECT id, lat, lon, tags FROM osm_nodes WHERE id = ANY(%s)", (node_ids,))
        for row in self.cur.fetchall():
            self.coord_cache[row["id"]] = (row["lat"], row["lon"])
            self.node_tags[row["id"]] = row["tags"] or {}

    def _expand_via_gin_scan(self, u: int):
        """Original path: one query, a GIN bitmap-heap-scan over the full
        osm_ways table for "any way containing node u"."""
        self.cur.execute(
            "SELECT id, nodes, tags FROM osm_ways "
            f"WHERE nodes && %s::bigint[] AND NOT (id = ANY(%s::bigint[])) AND {NOT_WALKABLE_WAY_SQL}",
            ([u], list(self.known_way_ids) or [0]),
        )
        return self.cur.fetchall()

    def _expand_via_adjacency_table(self, u: int):
        """Two point lookups instead of one scan: node_way_ids gives the
        candidate way ids touching u directly by PRIMARY KEY, then osm_ways
        is fetched by id (also effectively a PK lookup) for just those
        candidates. See schema.sql for node_way_ids and why this should be
        cheaper under a cold cache."""
        self.cur.execute("SELECT way_ids FROM node_way_ids WHERE node_id = %s", (u,))
        row = self.cur.fetchone()
        if not row:
            return []
        candidate_ids = [w for w in row["way_ids"] if w not in self.known_way_ids]
        if not candidate_ids:
            return []
        self.cur.execute(
            f"SELECT id, nodes, tags FROM osm_ways WHERE id = ANY(%s) AND {NOT_WALKABLE_WAY_SQL}",
            (candidate_ids,),
        )
        return self.cur.fetchall()

    def _is_vertical(self, node: int) -> bool:
        return is_vertical_node(self.node_tags.get(node))

    def _level_at(self, info: dict, node: int):
        """The level `info`'s way sits at, at `node`. Flat ways answer from a
        cached parse; only multi-level connector ways need interpolation."""
        levels = info.get("_levels")
        if levels is None:
            levels = parse_levels(info["tags"].get("level"))
            info["_levels"] = levels
        if len(levels) == 1:
            return levels[0]
        return way_node_levels(info["nodes"], self.coord_cache, levels).get(node)

    def _vertex(self, node: int, level):
        """Graph vertex for a node: a plain id, or a (node, level) port if the
        node is tagged vertical circulation."""
        return (node, level) if (level is not None and self._is_vertical(node)) else node

    def _port_levels(self, node: int) -> set:
        """Distinct levels the ways at a (vertical) node arrive on -- one port
        per level."""
        levels = set()
        for way_id in self.node_to_ways.get(node, ()):
            lvl = self._level_at(self.way_cache[way_id], node)
            if lvl is not None:
                levels.add(lvl)
        return levels

    def neighbors(self, u):
        """Yield (neighbor_vertex, weight_seconds, way_id) for vertex u,
        expanding from the database on first visit.

        u is a plain node id, or a (node_id, level) port at a tagged
        vertical-circulation node. From a port you may only walk onto ways that
        meet the node on THAT level; changing level is an explicit vertical edge
        (way_id None) priced by the mechanism -- so a level change mapped on a
        shared node is no longer free (see graph.py's vertical-circulation
        section and ISSUE-node-vertical-cost.md)."""
        u_node, u_level = (u if isinstance(u, tuple) else (u, None))
        self.expand(u_node)
        u_vertical = self._is_vertical(u_node)

        for way_id in self.node_to_ways.get(u_node, ()):
            info = self.way_cache[way_id]
            nodes = info["nodes"]
            tags = info["tags"]
            # Step-free routing: an elevator way is simply not traversable, as
            # if it weren't in the graph.
            if self.avoid_elevators and is_elevator_way(tags):
                continue
            # Standing on a port, you can only leave along a way that is on
            # this level here; other-level ways are reachable only after a
            # vertical edge.
            if u_vertical and u_level is not None and self._level_at(info, u_node) != u_level:
                continue
            speed, penalty = way_speed_and_penalty(tags)
            direction = way_direction(tags)
            for i, n in enumerate(nodes):
                if n != u_node:
                    continue
                for j, allowed in ((i + 1, direction >= 0), (i - 1, direction <= 0)):
                    if not allowed or j < 0 or j >= len(nodes):
                        continue
                    v = nodes[j]
                    if v not in self.coord_cache:
                        continue
                    dist = haversine_meters(*self.coord_cache[u_node], *self.coord_cache[v])
                    weight = dist / speed + penalty if speed > 0 else penalty
                    yield self._vertex(v, self._level_at(info, v)), weight, way_id

        # Synthetic stitch bridges (opt-in): a short edge onto a platform whose
        # polygon this node lies inside but shares no node with (see
        # core/build_stitch_bridges.py). way_id None, like a vertical edge, so it
        # drops out of the way_path. Both endpoints are plain platform/footway
        # nodes; keyed on the raw node id so it fires whether u arrived plain or
        # as a port.
        for v, weight in self.bridges.get(u_node, ()):
            if v in self.coord_cache:
                yield v, weight, None

        # Explicit vertical edges: from this port to every other level the node
        # serves, priced by the mechanism. Only when the node genuinely spans
        # >= 2 levels (a lone-level "elevator" node is just a plain junction).
        if u_vertical and u_level is not None:
            levels = self._port_levels(u_node)
            if len(levels) >= 2:
                kind = node_vertical_kind(self.node_tags[u_node])
                # Step-free routing: a node-mapped elevator offers no vertical
                # edges, so the search can't change level through it.
                if self.avoid_elevators and kind == "elevator":
                    return
                for lvl in levels:
                    if lvl != u_level:
                        yield (u_node, lvl), vertical_transition_cost(kind, lvl - u_level), None

    def plausibility_bound_seconds(self) -> float:
        """Upper bound on the walking time of any plausible transfer between the
        resolved source and target platforms, from their straight-line
        separation. A search whose cost exceeds this has resolved a wrong,
        far-away feature (not the intended platform) -- see the
        PLAUSIBLE_TRANSFER_* constants. Floors at PLAUSIBLE_TRANSFER_FLOOR_S so
        near-adjacent platforms still get a generous budget."""
        src = [self.coord_cache[s] for s in self.sources if s in self.coord_cache]
        tgt = [self.coord_cache[t] for t in self.targets if t in self.coord_cache]
        if not src or not tgt:
            return PLAUSIBLE_TRANSFER_FLOOR_S
        straight_m = min(haversine_meters(a[0], a[1], b[0], b[1]) for a in src for b in tgt)
        return max(
            PLAUSIBLE_TRANSFER_FLOOR_S,
            straight_m / WALKING_SPEED_MS * PLAUSIBLE_TRANSFER_DETOUR_FACTOR + PLAUSIBLE_TRANSFER_SLACK_S,
        )

    def edge_way_ids(self) -> Tuple[List[int], List[int]]:
        return [w for w, _, _ in self.edges_1], [w for w, _, _ in self.edges_2]

    def build_result(self, node_path: List, prev_way: Dict[object, Optional[int]], total_seconds: float,
                      expansions: int) -> Dict:
        """Shared result-shaping so every algorithm returns an identical
        schema. way_path is derived from prev_way (predecessor -> edge used
        to reach each vertex), which every algorithm populates the same way.

        node_path is a vertex path that may contain (node, level) ports at
        split vertical nodes; the vertical edges between them carry way_id None
        (dropped from way_path) and are zero-length self-loops at one node, so
        the emitted node_path collapses them back to real node ids -- leaving
        node_path/way_path/distance exactly as a pure-2D search would report,
        with only walking_time_seconds reflecting the vertical cost."""
        way_path = [prev_way.get(b) for a, b in zip(node_path, node_path[1:])]
        distinct_way_ids = list(dict.fromkeys(w for w in way_path if w is not None))
        node_path = collapse_port_path(node_path)
        total_distance = sum(
            haversine_meters(*self.coord_cache[a], *self.coord_cache[b])
            for a, b in zip(node_path, node_path[1:])
        )
        e1, e2 = self.edge_way_ids()
        return {
            "found": True,
            "relation_id": self.relation_id,
            "edge_1_way_ids": e1,
            "edge_2_way_ids": e2,
            "walking_time_seconds": round(total_seconds, 1),
            "walking_distance_meters": round(total_distance, 1),
            "node_path": node_path,
            "way_path": distinct_way_ids,
            "graph_ways_touched": len(self.way_cache),
            "graph_nodes_touched": len(self.coord_cache),
            "search_expansions": expansions,
        }

    def build_not_found(self, reason: str, expansions: int, **extra) -> Dict:
        e1, e2 = self.edge_way_ids()
        return {
            "found": False,
            "reason": reason,
            "graph_ways_touched": len(self.way_cache),
            "graph_nodes_touched": len(self.coord_cache),
            "search_expansions": expansions,
            "edge_1_way_ids": e1,
            "edge_2_way_ids": e2,
            **extra,
        }
