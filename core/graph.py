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
from typing import Any, Dict, List, Optional, Set, Tuple

Ways = Dict[int, Dict[str, Any]]         # way_id -> {"nodes": [...], "tags": {...}}
Coords = Dict[int, Tuple[float, float]]  # node_id -> (lat, lon)

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

def load_station_ways(
    conn, relation_id: int, max_rounds: Optional[int] = None
) -> Tuple[Ways, Coords]:
    """Load every way in the connected walkable component reachable from a
    station's relation closure, plus coordinates for every node touched.

    max_rounds=None (the default) means no cap -- expand until a round finds
    nothing new. This is deliberately the "brute force" choice: correctness
    over speed. Pass a small max_rounds only for diagnostics/timing, never
    to silently truncate a real search.
    """
    ways: Ways = {}
    known_node_ids: Set[int] = set()

    with conn.cursor() as cur:
        way_ids, seed_node_ids = resolve_relation_ways_and_nodes(cur, relation_id)
        known_node_ids |= seed_node_ids

        if way_ids:
            cur.execute("SELECT id, nodes, tags FROM osm_ways WHERE id = ANY(%s)", (list(way_ids),))
            for row in cur.fetchall():
                ways[row["id"]] = {"nodes": list(row["nodes"]), "tags": row["tags"] or {}}
                known_node_ids.update(row["nodes"])

        frontier = set(known_node_ids)
        round_num = 0
        while frontier:
            round_num += 1
            if max_rounds is not None and round_num > max_rounds:
                break
            cur.execute(
                "SELECT id, nodes, tags FROM osm_ways "
                "WHERE nodes && %s::bigint[] AND NOT (id = ANY(%s::bigint[]))",
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

        coords: Coords = {}
        if known_node_ids:
            cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (list(known_node_ids),))
            for row in cur.fetchall():
                coords[row["id"]] = (row["lat"], row["lon"])

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


def build_time_weighted_graph(ways: Ways, coords: Coords):
    """{way_id: {"nodes", "tags"}} -> adjacency list:
    node_id -> [(neighbor_id, weight_seconds, way_id), ...].

    A segment whose endpoint coordinates are unknown is skipped entirely
    (we can't measure it) rather than silently costed at zero -- the old
    way_length_meters() returned None for the *whole way* on one missing
    node, and the caller substituted 0.0, which could make a genuinely
    infeasible transfer look instant.
    """
    graph: Dict[int, List[Tuple[int, float, int]]] = {}

    def add_edge(a: int, b: int, weight: float, way_id: int) -> None:
        graph.setdefault(a, []).append((b, weight, way_id))

    for way_id, info in ways.items():
        nodes = info["nodes"]
        tags = info["tags"] or {}
        speed, penalty = way_speed_and_penalty(tags)
        direction = way_direction(tags)

        for i in range(len(nodes) - 1):
            a, b = nodes[i], nodes[i + 1]
            if a not in coords or b not in coords:
                continue
            dist = haversine_meters(coords[a][0], coords[a][1], coords[b][0], coords[b][1])
            weight = dist / speed + penalty if speed > 0 else penalty

            if direction >= 0:
                add_edge(a, b, weight, way_id)
            if direction <= 0:
                add_edge(b, a, weight, way_id)

    return graph
