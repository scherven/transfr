"""
Pathfinding between railway platform edges (ways) using station ways.

Bipartite search: we alternate between ways and nodes.
- Current state is a way -> add all nodes on that way to the search space.
- Current state is a node -> add all ways that contain that node (not yet visited).
Start at platform edge 1 (way), goal is platform edge 2 (way).
Path is thus: way_1 -> node -> way_2 -> node -> ... -> way_k.
"""

import math
import heapq
from typing import Dict, List, Any, Optional, Set, Tuple, Callable

import psycopg2
from psycopg2.extras import RealDictCursor
from tqdm import tqdm


# Approximate meters per degree at mid-latitudes for heuristic
METERS_PER_DEG_LAT = 111320
METERS_PER_DEG_LON_AT_45 = 78847


def get_way_segments_for_relation(
    conn, relation_id: int
) -> List[Tuple[int, int, int]]:
    """
    Load all way segments for a station relation from station_way_segments.
    Returns list of (node_from, node_to, way_id). Segments are directed;
    we'll build bidirectional edges when constructing the graph.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT node_from, node_to, way_id
            FROM station_way_segments
            WHERE relation_id = %s
            """,
            (relation_id,),
        )
        return [(r["node_from"], r["node_to"], r["way_id"]) for r in cur.fetchall()]


def get_node_coordinates(
    conn, node_ids: List[int]
) -> Optional[Dict[int, Tuple[float, float]]]:
    """
    Load lat/lon for nodes if planet_osm_nodes exists.
    Returns {node_id: (lat, lon)} or None if table unavailable.
    """
    if not node_ids:
        return {}
    with conn.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT id, lat, lon
                FROM planet_osm_nodes
                WHERE id = ANY(%s)
                """,
                (node_ids,),
            )
            return {r["id"]: (float(r["lat"]), float(r["lon"])) for r in cur.fetchall()}
        except psycopg2.ProgrammingError:
            return None


PEDESTRIAN_HIGHWAY_TYPES = (
    'footway', 'steps', 'corridor', 'pedestrian', 'path',
    'cycleway', 'crossing',
)


def build_bipartite_index(
    segments: List[Tuple[int, int, int]],
) -> Tuple[Dict[int, Set[int]], Dict[int, Set[int]]]:
    """
    From way segments (node_from, node_to, way_id), build:
    - way_to_nodes: way_id -> set of node_ids on that way
    - node_to_ways: node_id -> set of way_ids that contain that node
    """
    way_to_nodes: Dict[int, Set[int]] = {}
    node_to_ways: Dict[int, Set[int]] = {}
    for node_from, node_to, way_id in segments:
        way_to_nodes.setdefault(way_id, set()).update([node_from, node_to])
        node_to_ways.setdefault(node_from, set()).add(way_id)
        node_to_ways.setdefault(node_to, set()).add(way_id)
    return way_to_nodes, node_to_ways


def _add_way_to_index(
    way_id: int,
    nodes: List[int],
    way_to_nodes: Dict[int, Set[int]],
    node_to_ways: Dict[int, Set[int]],
) -> None:
    node_set = set(nodes)
    way_to_nodes.setdefault(way_id, set()).update(node_set)
    for n in node_set:
        node_to_ways.setdefault(n, set()).add(way_id)


def query_pedestrian_ways_by_nodes(
    conn,
    frontier_node_ids: List[int],
    exclude_way_ids: Set[int],
) -> List[Tuple[int, List[int]]]:
    """
    Find pedestrian ways in planet_osm_ways whose nodes overlap with
    frontier_node_ids, excluding ways already in exclude_way_ids.
    Returns list of (way_id, nodes).
    """
    if not frontier_node_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, nodes
            FROM planet_osm_ways
            WHERE nodes && %s::bigint[]
              AND tags->>'highway' = ANY(%s)
              AND NOT (id = ANY(%s::bigint[]))
            """,
            (frontier_node_ids, list(PEDESTRIAN_HIGHWAY_TYPES), list(exclude_way_ids)),
        )
        return [(r["id"], list(r["nodes"])) for r in cur.fetchall()]


def expand_pedestrian_ways(
    conn,
    way_to_nodes: Dict[int, Set[int]],
    node_to_ways: Dict[int, Set[int]],
    debug: bool = False,
    max_iterations: int = 10,
) -> None:
    """
    Iteratively discover pedestrian ways that share nodes with the current
    known set. Mutates way_to_nodes and node_to_ways in place.
    Stops when no new ways/nodes are found or max_iterations is reached.
    """
    known_way_ids = set(way_to_nodes.keys())
    all_known_nodes: Set[int] = set()
    for nodes in way_to_nodes.values():
        all_known_nodes.update(nodes)

    frontier_nodes = set(all_known_nodes)

    for iteration in range(1, max_iterations + 1):
        new_ways = query_pedestrian_ways_by_nodes(conn, list(frontier_nodes), known_way_ids)
        if not new_ways:
            if debug:
                print(f"[pathfind] Pedestrian expansion: iteration {iteration}, no new ways found. Done.", flush=True)
            break

        new_nodes: Set[int] = set()
        for way_id, nodes in new_ways:
            _add_way_to_index(way_id, nodes, way_to_nodes, node_to_ways)
            known_way_ids.add(way_id)
            for n in nodes:
                if n not in all_known_nodes:
                    new_nodes.add(n)

        all_known_nodes.update(new_nodes)

        if debug:
            print(
                f"[pathfind] Pedestrian expansion: iteration {iteration}, "
                f"+{len(new_ways)} ways, +{len(new_nodes)} new nodes",
                flush=True,
            )

        if not new_nodes:
            break
        frontier_nodes = new_nodes


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in meters between two WGS84 points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(min(1.0, a)))
    return 6371000 * c  # Earth radius in meters


def way_length_meters(
    node_ids: List[int], coords: Optional[Dict[int, Tuple[float, float]]]
) -> Optional[float]:
    """
    Total length of a way in meters (sum of segment lengths between consecutive nodes).
    Returns None if coords is None or any node is missing.
    """
    if not coords or len(node_ids) < 2:
        return None
    total = 0.0
    for i in range(len(node_ids) - 1):
        a, b = node_ids[i], node_ids[i + 1]
        if a not in coords or b not in coords:
            return None
        lat1, lon1 = coords[a]
        lat2, lon2 = coords[b]
        total += haversine_meters(lat1, lon1, lat2, lon2)
    return total


def min_distance_between_node_sets_meters(
    nodes_a: List[int],
    nodes_b: List[int],
    coords: Optional[Dict[int, Tuple[float, float]]],
) -> Optional[float]:
    """
    Minimum distance in meters between any node in set A and any node in set B.
    Useful for "platform width" (shortest gap between the two edges).
    Returns None if coords is None or any node is missing.
    """
    if not coords or not nodes_a or not nodes_b:
        return None
    best = float("inf")
    for a in nodes_a:
        if a not in coords:
            return None
        lat1, lon1 = coords[a]
        for b in nodes_b:
            if b not in coords:
                return None
            lat2, lon2 = coords[b]
            best = min(best, haversine_meters(lat1, lon1, lat2, lon2))
    return best if best != float("inf") else None


# State in bipartite search: ('way', way_id) or ('node', node_id)
BipartiteState = Tuple[str, int]


def bipartite_a_star(
    start_way_id: int,
    goal_way_id: int,
    way_to_nodes: Dict[int, Set[int]],
    node_to_ways: Dict[int, Set[int]],
    progress_interval: int = 0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Optional[List[BipartiteState]]:
    """
    Search alternating way -> nodes on that way -> ways through that node -> ...
    - From a way: add all its nodes to the search space.
    - From a node: add all ways that contain that node (that we haven't looked at yet).
    Returns path as list of (kind, id), e.g. [('way', w1), ('node', n1), ('way', w2), ...],
    or None if no path. Path starts at start_way_id and ends at goal_way_id.
    """
    start_state: BipartiteState = ("way", start_way_id)
    goal_state: BipartiteState = ("way", goal_way_id)
    if start_state == goal_state:
        return [start_state]

    counter = 0
    open_heap: List[Tuple[float, int, BipartiteState, float, List[BipartiteState]]] = [
        (0.0, counter, start_state, 0.0, [start_state])
    ]
    counter += 1
    closed: Set[BipartiteState] = set()
    cost_per_step = 1.0

    pbar = tqdm(desc="A* way/node expanded", unit=" steps") if progress_interval else None
    iterations = 0

    while open_heap:
        f, _, state, g, path = heapq.heappop(open_heap)
        if state in closed:
            continue
        closed.add(state)
        iterations += 1
        # if pbar:
        pbar.update(1)
        if progress_interval and progress_callback:
            progress_callback(len(closed), len(open_heap))

        kind, id_ = state
        if state == goal_state:
            # if pbar:
            pbar.close()
            return path

        if kind == "way":
            for node_id in way_to_nodes.get(id_, set()):
                new_state: BipartiteState = ("node", node_id)
                if new_state in closed:
                    continue
                g_new = g + cost_per_step
                new_path = path + [new_state]
                heapq.heappush(open_heap, (g_new, counter, new_state, g_new, new_path))
                counter += 1
        else:
            for way_id in node_to_ways.get(id_, set()):
                new_state = ("way", way_id)
                if new_state in closed:
                    continue
                g_new = g + cost_per_step
                new_path = path + [new_state]
                heapq.heappush(open_heap, (g_new, counter, new_state, g_new, new_path))
                counter += 1

    # if pbar:
    pbar.close()
    return None



def find_path_between_platform_edges(
    db_config: Dict[str, Any],
    edge_1: Dict[str, Any],
    edge_2: Dict[str, Any],
    debug: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Find a path between two platform edges (from platform_edges_indexed) using
    station ways. Both edges must belong to the same station (relation_id).
    Returns a result dict with path details or None if no path.
    Set debug=True to print diagnostics to stderr.
    """
    if edge_1["relation_id"] != edge_2["relation_id"]:
        if debug:
            print("[pathfind] Skipping: different relation_id", edge_1["relation_id"], "vs", edge_2["relation_id"])
        return None

    relation_id = edge_1["relation_id"]
    start_way_id = edge_1["way_id"]
    goal_way_id = edge_2["way_id"]

    conn = psycopg2.connect(**db_config, cursor_factory=RealDictCursor)
    try:
        if debug:
            print("[pathfind] Querying station_way_segments for relation_id=%s ..." % relation_id, flush=True)
        segments = get_way_segments_for_relation(conn, relation_id)
        if debug:
            print("[pathfind] relation_id:", relation_id)
            print("[pathfind] segments from DB:", len(segments), flush=True)
            print("[pathfind] edge_1 way_id:", start_way_id, "edge_2 way_id:", goal_way_id)

        if not segments:
            if debug:
                print("[pathfind] No segments for this relation (station_way_segments has no rows for relation_id).")
            return None

        if debug:
            print("[pathfind] Building way<->node index from %s segments ..." % len(segments), flush=True)
        way_to_nodes, node_to_ways = build_bipartite_index(segments)

        # Include platform edge ways (they may not be in station_way_segments)
        for edge in (edge_1, edge_2):
            _add_way_to_index(edge["way_id"], edge["nodes"], way_to_nodes, node_to_ways)

        if debug:
            print("[pathfind] Index: %d ways, %d nodes. Expanding pedestrian ways ..." % (len(way_to_nodes), len(node_to_ways)), flush=True)
        expand_pedestrian_ways(conn, way_to_nodes, node_to_ways, debug=debug)
        if debug:
            print("[pathfind] After expansion: %d ways, %d nodes" % (len(way_to_nodes), len(node_to_ways)), flush=True)

        if start_way_id not in way_to_nodes:
            if debug:
                print("[pathfind] Start way_id %s has no nodes." % start_way_id)
            return None
        if goal_way_id not in way_to_nodes:
            if debug:
                print("[pathfind] Goal way_id %s has no nodes." % goal_way_id)
            return None

        if debug:
            print("[pathfind] Running bipartite A* (way -> nodes -> ways) ...", flush=True)
        path = bipartite_a_star(
            start_way_id,
            goal_way_id,
            way_to_nodes,
            node_to_ways,
            progress_interval=5000 if debug else 0,
        )
        if path is None:
            if debug:
                print("[pathfind] No path (ways are not connected via shared nodes).")
            return None

        # path = [('way', w1), ('node', n1), ('way', w2), ...]
        path_way_ids = [id_ for (k, id_) in path if k == "way"]
        path_node_ids = [id_ for (k, id_) in path if k == "node"]
        # way->node steps for compatibility: (way_id, node_id) for each step from a way to a node
        path_ways = []
        for i in range(len(path) - 1):
            (k1, id1), (k2, id2) = path[i], path[i + 1]
            if k1 == "way" and k2 == "node":
                path_ways.append((id1, id2))

        return {
            "type": "way_path",
            "edge_1": edge_1,
            "edge_2": edge_2,
            "relation_id": relation_id,
            "path_sequence": path,
            "path_nodes": path_node_ids,
            "path_ways": path_ways,
            "way_ids": path_way_ids,
        }
    finally:
        conn.close()
