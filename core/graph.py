"""
Build the walkable graph for a station from the database.

A station is an OSM relation (public_transport=stop_area or
stop_area_group). Building its graph means:

  1. Recursively resolve relation membership (a stop_area_group's members
     can themselves be stop_area relations) down to a flat set of member
     way/node ids.
  2. Load those ways, then repeatedly query for any *other* way sharing a
     node with what we already have, merging it in, until a round finds
     nothing new. No cap on the number of rounds: we want the full
     connected component, not "whatever a fixed number of hops reaches" --
     the old bipartite_search() capped this at max_expansions=10, which
     means it could give up before finding a path that genuinely exists a
     few hops further out.
  3. Turn the way collection into a plain, real-world-time-weighted graph
     (see build_time_weighted_graph) that a standard shortest-path search
     can run over directly.
"""

import math
import time
from typing import Any, Dict, List, Optional, Set, Tuple

Ways = Dict[int, Dict[str, Any]]         # way_id -> {"nodes": [...], "tags": {...}}
Coords = Dict[int, Tuple[float, float]]  # node_id -> (lat, lon)

# Ways that pass our highway/railway walkable-tag import filter but do NOT
# represent something a person actually walks along, and must be excluded
# from graph traversal even though they're present in osm_ways.
#
#   public_transport=station -- the outline of the *entire station
#   building/site*, not a path. Confirmed by hand: Strasbourg-Ville's
#   "Gare de Strasbourg" (way 1154073483, 128 nodes) and Colmar's "Colmar"
#   (way 1314402541, 90 nodes) are both direct stop_area relation members
#   tagged this way, and were being walked as if their entire perimeter
#   were a single corridor -- producing a "shortcut" through the building
#   footprint instead of routing through the real, mapped pedestrian
#   infrastructure inside it.
#
# NOT_WALKABLE_WAY_SQL is a SQL fragment (references `tags`) for queries
# that fetch ways directly; is_walkable_way() is the equivalent Python
# predicate for ways already fetched.
NOT_WALKABLE_WAY_SQL = "tags->>'public_transport' IS DISTINCT FROM 'station'"


def is_walkable_way(tags: Dict[str, str]) -> bool:
    return tags.get("public_transport") != "station"


# ---------------------------------------------------------------------------
# Relation resolution
# ---------------------------------------------------------------------------

def resolve_relation_ways_and_nodes(
    cur, relation_id: int, _seen: Optional[Set[int]] = None
) -> Tuple[Set[int], Set[int]]:
    """Recursively resolve a relation's way and node members, following
    nested relation members (e.g. stop_area_group -> stop_area).
    Returns (way_ids, seed_node_ids)."""
    if _seen is None:
        _seen = set()
    if relation_id in _seen:
        return set(), set()
    _seen.add(relation_id)

    cur.execute(
        "SELECT member_type, member_ref FROM osm_relation_members WHERE relation_id = %s",
        (relation_id,),
    )
    way_ids: Set[int] = set()
    node_ids: Set[int] = set()
    child_relations: List[int] = []
    for row in cur.fetchall():
        if row["member_type"] == "W":
            way_ids.add(row["member_ref"])
        elif row["member_type"] == "N":
            node_ids.add(row["member_ref"])
        elif row["member_type"] == "R":
            child_relations.append(row["member_ref"])

    for child_id in child_relations:
        child_ways, child_nodes = resolve_relation_ways_and_nodes(cur, child_id, _seen)
        way_ids |= child_ways
        node_ids |= child_nodes

    return way_ids, node_ids


# ---------------------------------------------------------------------------
# Full-closure graph loading
# ---------------------------------------------------------------------------

DEFAULT_SEARCH_RADIUS_M = 1000.0


def _fetch_coords(cur, node_ids) -> Coords:
    if not node_ids:
        return {}
    cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (list(node_ids),))
    return {row["id"]: (row["lat"], row["lon"]) for row in cur.fetchall()}


def bbox_from_coords(coords: Coords, margin_m: float) -> Tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon), padded by margin_m in every
    direction. Longitude degrees shrink with latitude, so the margin is
    corrected by cos(latitude)."""
    lats = [c[0] for c in coords.values()]
    lons = [c[1] for c in coords.values()]
    mean_lat = sum(lats) / len(lats)
    lat_margin = margin_m / 111_320.0
    lon_margin = margin_m / (111_320.0 * max(0.1, math.cos(math.radians(mean_lat))))
    return (min(lats) - lat_margin, max(lats) + lat_margin, min(lons) - lon_margin, max(lons) + lon_margin)


def in_bbox(coord: Tuple[float, float], bbox: Tuple[float, float, float, float]) -> bool:
    lat, lon = coord
    min_lat, max_lat, min_lon, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def load_station_ways(
    conn,
    relation_id: int,
    max_rounds: Optional[int] = None,
    progress_cb=None,
    search_radius_m: Optional[float] = DEFAULT_SEARCH_RADIUS_M,
) -> Tuple[Ways, Coords]:
    """Load every way in the connected walkable component reachable from a
    station's relation closure, plus coordinates for every node touched.

    max_rounds=None (the default) means no cap on the *number of expansion
    rounds* -- we always expand until a round finds nothing new, never give
    up early the way the old bipartite_search()'s max_expansions=10 did.

    search_radius_m bounds *where* we're willing to look, not *how hard*: a
    generous (default 1000m) bounding box around the station's own seed
    geometry. 1000m of straight-line bbox already comfortably covers any
    path that would still count as "feasible" -- even 800m of *pure*
    walking distance, with no stairs/detour penalty at all, is already
    ~9.5 minutes at 1.4 m/s, right at the edge of any reasonable transfer
    time budget. This bound turned out to be necessary, not just an
    optimization -- without it, expansion doesn't taper off near real
    stations the way you'd expect. Dense European city centers are
    frequently *one single* footway-connected component with no natural
    graph boundary (verified: Berlin Hauptbahnhof's unbounded closure was
    still *accelerating* in new ways per round after 7 rounds -- 28, 18,
    20, 21, 49, 87, 122 -- because it had started walking into the
    surrounding city, not converging on the station). A round cap can't
    distinguish "found the station" from "gave up mid-search" since a
    single round's physical distance is unbounded; a distance cap can,
    because it has a real-world meaning: no legitimate same-station
    platform transfer requires walking a kilometre through the city.
    Pass search_radius_m=None to disable this and get the literal unbounded
    search (slow, or very very slow, exactly as requested -- useful for
    confirming the bound never excludes a real path on a specific station).

    progress_cb(round_num, new_ways, total_ways, total_nodes, elapsed_s), if
    given, is called after every expansion round -- purely for observability
    on large/slow stations, does not affect the result.
    """
    ways: Ways = {}
    known_node_ids: Set[int] = set()
    coords: Coords = {}

    with conn.cursor() as cur:
        way_ids, seed_node_ids = resolve_relation_ways_and_nodes(cur, relation_id)
        known_node_ids |= seed_node_ids

        if way_ids:
            cur.execute("SELECT id, nodes, tags FROM osm_ways WHERE id = ANY(%s)", (list(way_ids),))
            for row in cur.fetchall():
                tags = row["tags"] or {}
                if not is_walkable_way(tags):
                    continue
                ways[row["id"]] = {"nodes": list(row["nodes"]), "tags": tags}
                known_node_ids.update(row["nodes"])

        bbox = None
        if search_radius_m is not None:
            coords.update(_fetch_coords(cur, known_node_ids))
            if coords:
                bbox = bbox_from_coords(coords, search_radius_m)

        frontier = set(known_node_ids)
        round_num = 0
        while frontier:
            if bbox is not None:
                missing = [n for n in frontier if n not in coords]
                coords.update(_fetch_coords(cur, missing))
                frontier = {n for n in frontier if n in coords and in_bbox(coords[n], bbox)}
                if not frontier:
                    break

            round_num += 1
            if max_rounds is not None and round_num > max_rounds:
                break
            t0 = time.monotonic()
            cur.execute(
                "SELECT id, nodes, tags FROM osm_ways "
                f"WHERE nodes && %s::bigint[] AND NOT (id = ANY(%s::bigint[])) AND {NOT_WALKABLE_WAY_SQL}",
                (list(frontier), list(ways.keys()) or [0]),
            )
            new_rows = cur.fetchall()
            if not new_rows:
                break
            new_node_ids: Set[int] = set()
            for row in new_rows:
                ways[row["id"]] = {"nodes": list(row["nodes"]), "tags": row["tags"] or {}}
                for n in row["nodes"]:
                    if n not in known_node_ids:
                        new_node_ids.add(n)
                        known_node_ids.add(n)
            frontier = new_node_ids
            if progress_cb:
                progress_cb(round_num, len(new_rows), len(ways), len(known_node_ids), time.monotonic() - t0)

        missing = [n for n in known_node_ids if n not in coords]
        coords.update(_fetch_coords(cur, missing))

    return ways, coords


def merge_way(ways: Ways, coords: Coords, conn, way_id: int, node_ids: List[int]) -> None:
    """Seed the graph with a way that may not be part of the relation
    closure (e.g. a platform_edge that only touches the station via shared
    nodes, never as an explicit relation member)."""
    if way_id not in ways:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nodes, tags FROM osm_ways WHERE id = %s", (way_id,))
            row = cur.fetchone()
            if row:
                ways[way_id] = {"nodes": list(row["nodes"]), "tags": row["tags"] or {}}
    missing = [n for n in node_ids if n not in coords]
    if missing:
        with conn.cursor() as cur:
            cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (missing,))
            for row in cur.fetchall():
                coords[row["id"]] = (row["lat"], row["lon"])


# ---------------------------------------------------------------------------
# Walking-time edge weights
# ---------------------------------------------------------------------------

WALKING_SPEED_MS = 1.4     # ~5 km/h, standard pedestrian assumption
STEPS_SPEED_MS = 0.5       # stairs, roughly half walking speed
ESCALATOR_SPEED_MS = 0.9   # conveying=yes/forward/backward
ELEVATOR_SPEED_MS = 1.4    # vertical travel time itself is treated as small
ELEVATOR_WAIT_S = 30.0     # fixed wait penalty for using an elevator
FLOOR_HEIGHT_M = 4.0       # nominal metres per floor; matches viz_export's Z scale


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def way_speed_and_penalty(tags: Dict[str, str]) -> Tuple[float, float]:
    """(speed_m_per_s, fixed_penalty_s) for a way, based on its tags."""
    if tags.get("conveying") in ("yes", "forward", "backward"):
        return ESCALATOR_SPEED_MS, 0.0
    highway = tags.get("highway")
    railway = tags.get("railway")
    if highway == "steps":
        return STEPS_SPEED_MS, 0.0
    if highway == "elevator" or railway == "elevator":
        return ELEVATOR_SPEED_MS, ELEVATOR_WAIT_S
    return WALKING_SPEED_MS, 0.0


def way_direction(tags: Dict[str, str]) -> int:
    """1 = forward-only (way's node order), -1 = backward-only, 0 = both."""
    conveying = tags.get("conveying")
    if conveying == "forward":
        return 1
    if conveying == "backward":
        return -1
    oneway = tags.get("oneway")
    if oneway in ("yes", "1", "true"):
        return 1
    if oneway == "-1":
        return -1
    return 0


# ---------------------------------------------------------------------------
# Vertical circulation mapped ON a shared node
# ---------------------------------------------------------------------------
#
# OSM often maps an elevator/stairs as a single tagged NODE shared between the
# footways of the levels it serves (e.g. Berlin Hbf's OTIS node 742238019,
# level=-2;-1;0;1;2), rather than as a traversable way. A purely 2D graph makes
# that shared node one vertex on every footway, so a search walks footway(L1)
# -> node -> footway(L2) and the level change costs 0 s / 0 m (the bug in
# ISSUE-node-vertical-cost.md). The fix: split such a node into one "port"
# vertex per level -- the tuple (node_id, level) -- wire each footway to the
# port at ITS level, and add explicit vertical edges between the ports, priced
# by the mechanism the node is tagged as. Every OTHER node stays a plain int
# vertex, so nothing else about the graph or its results changes; the only
# node ids that ever become tuples are tagged elevators/stairs/escalators.


def parse_levels(raw: Optional[str]) -> List[float]:
    """OSM `level` string -> ordered list of floor numbers. Kept in sync with
    viz_export.parse_levels (the visualisation reads the same tag): single
    "1"; semicolon list "-2;-1;0"; dash range "-3-0"; half-levels; and
    untagged -> [0.0], the ground-level default ~45% of un-levelled approach
    ways rely on. Anything unparseable falls back to ground, never crashes."""
    if raw is None or raw == "":
        return [0.0]
    raw = raw.strip()
    try:
        if ";" in raw:
            return [float(p) for p in raw.split(";") if p != ""]
        inner = raw.find("-", 1)  # a '-' that is not the leading sign -> a range
        if inner != -1:
            return [float(raw[:inner]), float(raw[inner + 1:])]
        return [float(raw)]
    except ValueError:
        return [0.0]


def way_node_levels(nodes: List[int], coords: Coords, levels: List[float]) -> Dict[int, float]:
    """Per-node level for one way: flat if single-level; otherwise interpolated
    first->last along cumulative horizontal distance, so a connector way's two
    endpoints land exactly on their floors and only its (private) interior
    nodes take fractional levels. Mirrors viz_export.way_node_heights without
    the floor-height multiply."""
    present = [n for n in nodes if n in coords]
    if not present:
        return {}
    if len(levels) == 1 or len(present) == 1:
        return {n: levels[0] for n in present}
    cum = [0.0]
    for a, b in zip(present, present[1:]):
        cum.append(cum[-1] + haversine_meters(coords[a][0], coords[a][1], coords[b][0], coords[b][1]))
    total = cum[-1] or 1.0
    l0, l1 = levels[0], levels[-1]
    return {present[i]: l0 + (l1 - l0) * (cum[i] / total) for i in range(len(present))}


def node_vertical_kind(tags: Dict[str, str]) -> str:
    """Vertical-circulation class of a NODE (see viz_export.node_kind).
    'vertical' means 'not a tagged mechanism'."""
    if tags.get("highway") == "elevator" or tags.get("railway") == "elevator":
        return "elevator"
    if tags.get("conveying") in ("yes", "forward", "backward"):
        return "escalator"
    if tags.get("highway") == "steps":
        return "stairs"
    return "vertical"


def is_vertical_node(tags: Optional[Dict[str, str]]) -> bool:
    """True iff a node is tagged as a vertical-circulation mechanism -- the
    only nodes we split into per-level ports.

    Deliberately NOT triggered by a mere difference between the `level` tags of
    two ordinary ways meeting at a node: ~45% of ways carry no level tag at all
    (defaulting to ground), so an untagged approach way meeting a levelled
    platform would otherwise read as a bogus level change right at the platform.
    Requiring the node's own mechanism tag keeps the split to points OSM
    explicitly marks as where you change floors, and keeps platform/source
    nodes -- never elevators -- as plain int vertices."""
    return tags is not None and node_vertical_kind(tags) != "vertical"


def vertical_transition_cost(kind: str, delta_levels: float) -> float:
    """Seconds to move `delta_levels` floors through a node-mapped mechanism.
    Depends only on |delta|, so A->B and B->A cost the same."""
    rise_m = abs(delta_levels) * FLOOR_HEIGHT_M
    if kind == "elevator":
        return ELEVATOR_WAIT_S + rise_m / ELEVATOR_SPEED_MS
    if kind == "escalator":
        return rise_m / ESCALATOR_SPEED_MS
    # stairs, or an untagged vertical link: priced as stairs -- never free, and
    # never cheaper than the mechanism it most plausibly is.
    return rise_m / STEPS_SPEED_MS


def vertical_edges_for_levels(kind: str, levels) -> List[Tuple[float, float, float]]:
    """All-pairs (from_level, to_level, cost) among the distinct `levels` a
    split node serves. All-pairs, not just adjacent floors, so a multi-floor
    ride is a single edge = a single elevator wait, never N chained waits."""
    sl = sorted(set(levels))
    return [
        (sl[i], sl[j], vertical_transition_cost(kind, sl[i] - sl[j]))
        for i in range(len(sl))
        for j in range(len(sl))
        if i != j
    ]


def node_id_of(vertex) -> int:
    """Real OSM node id of a graph vertex, whether it's a plain int node or a
    split (node_id, level) port."""
    return vertex[0] if isinstance(vertex, tuple) else vertex


def collapse_port_path(vertex_path: List) -> List[int]:
    """Vertex path (possibly containing (node, level) ports) -> real node id
    path, dropping the consecutive duplicates a node-mapped level change
    introduces (a vertical hop is a zero-length self-loop at one node)."""
    out: List[int] = []
    for v in vertex_path:
        nid = node_id_of(v)
        if not out or out[-1] != nid:
            out.append(nid)
    return out


def load_node_tags(conn, node_ids) -> Dict[int, Dict[str, str]]:
    """Tags for a set of nodes -- needed to classify node-mapped vertical
    circulation. Used by the eager pathfinder; the lazy SearchContext caches
    tags itself as it expands."""
    node_ids = list(node_ids)
    if not node_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, tags FROM osm_nodes WHERE id = ANY(%s)", (node_ids,))
        return {row["id"]: (row["tags"] or {}) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Walking-time edge weights
# ---------------------------------------------------------------------------


def build_time_weighted_graph(ways: Ways, coords: Coords, node_tags: Optional[Dict[int, Dict[str, str]]] = None):
    """{way_id: {"nodes", "tags"}} -> adjacency list:
    vertex -> [(neighbor_vertex, weight_seconds, way_id), ...].

    A vertex is normally a plain node id; a node tagged as vertical circulation
    (see is_vertical_node) becomes one (node_id, level) "port" per level it
    serves, with explicit vertical edges between the ports so a level change
    mapped on that shared node is no longer free (see the section header above).
    Pass node_tags (node_id -> tags) to enable this; without it every node is
    treated as non-vertical and the graph is the plain 2D one as before.

    A segment whose endpoint coordinates are unknown is skipped entirely
    (we can't measure it) rather than silently costed at zero -- the old
    way_length_meters() returned None for the *whole way* on one missing
    node, and the caller substituted 0.0, which could make a genuinely
    infeasible transfer look instant.
    """
    node_tags = node_tags or {}
    graph: Dict[object, List[Tuple[object, float, Optional[int]]]] = {}

    def add_edge(a, b, weight: float, way_id: Optional[int]) -> None:
        graph.setdefault(a, []).append((b, weight, way_id))

    def vertex(node: int, level: float):
        return (node, level) if is_vertical_node(node_tags.get(node)) else node

    # node_id -> {level served at this node} -- only for split (vertical) nodes,
    # so we can add their between-level edges after wiring the horizontal ones.
    ports: Dict[int, set] = {}

    for way_id, info in ways.items():
        nodes = info["nodes"]
        tags = info["tags"] or {}
        speed, penalty = way_speed_and_penalty(tags)
        direction = way_direction(tags)
        node_levels = way_node_levels(nodes, coords, parse_levels(tags.get("level")))

        for n, lvl in node_levels.items():
            if is_vertical_node(node_tags.get(n)):
                ports.setdefault(n, set()).add(lvl)

        for i in range(len(nodes) - 1):
            a, b = nodes[i], nodes[i + 1]
            if a not in coords or b not in coords:
                continue
            dist = haversine_meters(coords[a][0], coords[a][1], coords[b][0], coords[b][1])
            weight = dist / speed + penalty if speed > 0 else penalty
            av = vertex(a, node_levels[a])
            bv = vertex(b, node_levels[b])
            if direction >= 0:
                add_edge(av, bv, weight, way_id)
            if direction <= 0:
                add_edge(bv, av, weight, way_id)

    for node, levels in ports.items():
        if len(levels) < 2:
            continue
        kind = node_vertical_kind(node_tags[node])
        for l1, l2, cost in vertical_edges_for_levels(kind, levels):
            add_edge((node, l1), (node, l2), cost, None)

    return graph
