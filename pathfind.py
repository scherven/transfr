"""
A* pathfinding between railway platform edges using station ways.

Graph model:
- Vertices: OSM node IDs (junctions where ways meet).
- Edges: segments from station_way_segments (consecutive node pairs along each way).
- Start: any node belonging to platform edge 1.
- Goal: any node belonging to platform edge 2.

Uses A* with Euclidean heuristic when node coordinates are available,
otherwise falls back to Dijkstra (A* with zero heuristic).
"""

import math
import heapq
from typing import Dict, List, Any, Optional, Set, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor


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


def build_graph(
    segments: List[Tuple[int, int, int]],
    cost_per_segment: float = 1.0,
) -> Dict[int, List[Tuple[int, int, float]]]:
    """
    Build bidirectional adjacency list from directed segments.
    graph[node_id] = [(neighbor_id, way_id, cost), ...]
    """
    graph: Dict[int, List[Tuple[int, int, float]]] = {}
    for node_from, node_to, way_id in segments:
        if node_from not in graph:
            graph[node_from] = []
        graph[node_from].append((node_to, way_id, cost_per_segment))
        if node_to not in graph:
            graph[node_to] = []
        graph[node_to].append((node_from, way_id, cost_per_segment))
    return graph


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


def heuristic(
    node: int,
    goal_nodes: Set[int],
    coords: Optional[Dict[int, Tuple[float, float]]],
) -> float:
    """
    Admissible heuristic: 0 if no coords; else minimum straight-line distance
    from node to any goal node (in same cost units as graph: 1 per segment).
    We use meters and then treat graph cost as ~1 per segment (so scale heuristic
    to be comparable: e.g. 1 unit per 10m so heuristic is in "segment equivalents").
    """
    if not coords or node not in coords:
        return 0.0
    if node in goal_nodes:
        return 0.0
    lat1, lon1 = coords[node]
    best = float("inf")
    for n in goal_nodes:
        if n not in coords:
            continue
        lat2, lon2 = coords[n]
        d = haversine_meters(lat1, lon1, lat2, lon2)
        # Scale to rough "segment" units (~10m per segment) so heuristic is admissible
        best = min(best, d / 10.0)
    return best if best != float("inf") else 0.0


def a_star(
    start_nodes: Set[int],
    goal_nodes: Set[int],
    graph: Dict[int, List[Tuple[int, int, float]]],
    coords: Optional[Dict[int, Tuple[float, float]]] = None,
) -> Optional[Tuple[List[int], List[Tuple[int, int, int]]]]:
    """
    A* from any of start_nodes to any of goal_nodes.
    Returns (node_path, way_segments) or None if no path.
    way_segments: list of (node_from, node_to, way_id) used along the path.
    """
    if not start_nodes or not goal_nodes:
        return None
    if start_nodes & goal_nodes:
        n = next(iter(start_nodes & goal_nodes))
        return ([n], [])

    # (f, counter, node, g, path_nodes, path_ways)
    counter = 0
    open_heap: List[Tuple[float, int, int, float, List[int], List[Tuple[int, int, int]]]] = []
    for s in start_nodes:
        h = heuristic(s, goal_nodes, coords)
        heapq.heappush(
            open_heap,
            (h, counter, s, 0.0, [s], []),
        )
        counter += 1
    closed: Set[int] = set()

    while open_heap:
        f, _, node, g, path_nodes, path_ways = heapq.heappop(open_heap)
        if node in closed:
            continue
        closed.add(node)

        if node in goal_nodes:
            return (path_nodes, path_ways)

        for neighbor, way_id, cost in graph.get(node, []):
            if neighbor in closed:
                continue
            g_new = g + cost
            h_new = heuristic(neighbor, goal_nodes, coords)
            f_new = g_new + h_new
            new_path_nodes = path_nodes + [neighbor]
            new_path_ways = path_ways + [(node, neighbor, way_id)]
            heapq.heappush(
                open_heap,
                (f_new, counter, neighbor, g_new, new_path_nodes, new_path_ways),
            )
            counter += 1

    return None


def find_path_between_platform_edges(
    db_config: Dict[str, Any],
    edge_1: Dict[str, Any],
    edge_2: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Find a path between two platform edges (from platform_edges_indexed) using
    station ways. Both edges must belong to the same station (relation_id).
    Returns a result dict with path details or None if no path.
    """
    if edge_1["relation_id"] != edge_2["relation_id"]:
        return None

    relation_id = edge_1["relation_id"]
    start_nodes = set(edge_1["nodes"])
    goal_nodes = set(edge_2["nodes"])

    conn = psycopg2.connect(**db_config, cursor_factory=RealDictCursor)
    try:
        segments = get_way_segments_for_relation(conn, relation_id)
        if not segments:
            return None

        all_node_ids = set()
        for a, b, _ in segments:
            all_node_ids.add(a)
            all_node_ids.add(b)
        coords = get_node_coordinates(conn, list(all_node_ids))

        graph = build_graph(segments)
        result = a_star(start_nodes, goal_nodes, graph, coords)
        if result is None:
            return None

        path_nodes, path_ways = result
        return {
            "type": "way_path",
            "edge_1": edge_1,
            "edge_2": edge_2,
            "relation_id": relation_id,
            "path_nodes": path_nodes,
            "path_ways": path_ways,
            "way_ids": list(dict.fromkeys(w for _, _, w in path_ways)),
        }
    finally:
        conn.close()
