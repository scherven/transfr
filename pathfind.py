"""
Pathfinding between railway platform edges using a bipartite way/node graph.

The graph alternates between two kinds of state:
  way  -> expands to all nodes on that way
  node -> expands to all ways that pass through that node

A platform edge is itself a way, so pathfinding goes:
  edge_1 (way) -> node -> way -> node -> ... -> edge_2 (way)

Because every edge cost is uniform, plain BFS is optimal and faster than
Dijkstra/A*. When the initial station-member ways don't connect the two
edges, additional pedestrian ways (footway, steps, corridor, etc.) are
discovered in batch from the DB and the search is re-run.
"""

import math
from collections import deque
from typing import Dict, List, Any, Optional, Set, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

_pool: Optional[ThreadedConnectionPool] = None


def init_pool(db_config: Dict[str, Any], minconn: int = 1, maxconn: int = 5) -> None:
    """Initialise the module-level connection pool.  Call once at startup."""
    global _pool
    if _pool is not None:
        return
    _pool = ThreadedConnectionPool(minconn, maxconn, cursor_factory=RealDictCursor, **db_config)


def get_conn():
    """Borrow a connection from the pool (caller must call put_conn)."""
    if _pool is None:
        raise RuntimeError("Connection pool not initialised — call init_pool() first")
    return _pool.getconn()


def put_conn(conn) -> None:
    """Return a connection to the pool."""
    if _pool is not None:
        _pool.putconn(conn)


def close_pool() -> None:
    """Shut down the pool and close all connections."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


# OSM way tags that represent physical walkable infrastructure.
# Used to filter expansion queries so the BFS only follows ways people
# actually walk on (not rail tracks, roads, etc.).
WALKABLE_HIGHWAY_TYPES = (
    "footway", "steps", "corridor", "pedestrian",
    "path", "cycleway", "crossing",
    "elevator", "escalator", "platform", "service",
)
WALKABLE_RAILWAY_TYPES = (
    "platform", "platform_edge",
)

# ---------------------------------------------------------------------------
# Graph state type used by the bipartite search.
# Each state is ('way', way_id) or ('node', node_id).
# ---------------------------------------------------------------------------
BipartiteState = Tuple[str, int]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_way_segments_for_relation(
    conn, relation_id: int
) -> List[Tuple[int, int, int]]:
    """Fetch every consecutive (node_from, node_to, way_id) segment for a
    station relation from the station_way_segments view.  These segments
    form the initial graph for pathfinding."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT node_from, node_to, way_id "
            "FROM station_way_segments WHERE relation_id = %s",
            (relation_id,),
        )
        return [(r["node_from"], r["node_to"], r["way_id"]) for r in cur.fetchall()]


def get_node_coordinates(
    conn, node_ids: List[int]
) -> Optional[Dict[int, Tuple[float, float]]]:
    """Load (lat, lon) for the given node IDs from planet_osm_nodes.
    Returns None if the table does not exist."""
    if not node_ids:
        return {}
    with conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT id, lat, lon FROM planet_osm_nodes WHERE id = ANY(%s)",
                (node_ids,),
            )
            return {r["id"]: (float(r["lat"]), float(r["lon"])) for r in cur.fetchall()}
        except psycopg2.ProgrammingError:
            return None


def query_walkable_ways_by_nodes(
    conn,
    frontier_node_ids: List[int],
    exclude_way_ids: Set[int],
) -> List[Tuple[int, List[int]]]:
    """Find walkable ways in planet_osm_ways that share at least one node
    with *frontier_node_ids*, excluding ways already in *exclude_way_ids*.
    Matches highway, railway, and conveying tags that represent physical
    infrastructure people walk on.  Used to iteratively expand the search
    graph beyond the initial station relation members."""
    if not frontier_node_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, nodes FROM planet_osm_ways "
            "WHERE nodes && %s::bigint[] "
            "  AND NOT (id = ANY(%s::bigint[])) "
            "  AND ("
            "    tags->>'highway' = ANY(%s) "
            "    OR tags->>'railway' = ANY(%s) "
            "    OR tags ? 'conveying'"
            "  ) "
            "  AND tags->>'access' IS DISTINCT FROM 'private'",
            (
                frontier_node_ids,
                list(exclude_way_ids),
                list(WALKABLE_HIGHWAY_TYPES),
                list(WALKABLE_RAILWAY_TYPES),
            ),
        )
        return [(r["id"], list(r["nodes"])) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Bipartite index (in-memory graph representation)
# ---------------------------------------------------------------------------

def build_bipartite_index(
    segments: List[Tuple[int, int, int]],
) -> Tuple[Dict[int, Set[int]], Dict[int, Set[int]]]:
    """Build two lookup dicts from way-segment rows:
      way_to_nodes[way_id]  -> {node_id, ...}  (all nodes on that way)
      node_to_ways[node_id] -> {way_id, ...}    (all ways through that node)
    """
    way_to_nodes: Dict[int, Set[int]] = {}
    node_to_ways: Dict[int, Set[int]] = {}
    for node_from, node_to, way_id in segments:
        way_to_nodes.setdefault(way_id, set()).update((node_from, node_to))
        node_to_ways.setdefault(node_from, set()).add(way_id)
        node_to_ways.setdefault(node_to, set()).add(way_id)
    return way_to_nodes, node_to_ways


def _add_way_to_index(
    way_id: int,
    nodes: List[int],
    way_to_nodes: Dict[int, Set[int]],
    node_to_ways: Dict[int, Set[int]],
) -> None:
    """Insert a single way (with its node list) into the bipartite index."""
    node_set = set(nodes)
    way_to_nodes.setdefault(way_id, set()).update(node_set)
    for n in node_set:
        node_to_ways.setdefault(n, set()).add(way_id)


# ---------------------------------------------------------------------------
# BFS search over the bipartite graph
# ---------------------------------------------------------------------------

def _bipartite_bfs(
    start_way_id: int,
    goal_way_id: int,
    way_to_nodes: Dict[int, Set[int]],
    node_to_ways: Dict[int, Set[int]],
) -> Optional[List[BipartiteState]]:
    """BFS from (way, start_way_id) to (way, goal_way_id) over the bipartite
    index.  All transitions have equal cost so BFS guarantees the shortest
    path (fewest way/node hops).  Uses a came_from dict for O(1) memory per
    visited state instead of storing full paths on the queue."""
    start: BipartiteState = ("way", start_way_id)
    goal: BipartiteState = ("way", goal_way_id)
    if start == goal:
        return [start]

    came_from: Dict[BipartiteState, Optional[BipartiteState]] = {start: None}
    queue: deque[BipartiteState] = deque([start])

    while queue:
        state = queue.popleft()
        kind, id_ = state

        if kind == "way":
            for node_id in way_to_nodes.get(id_, ()):
                neighbor: BipartiteState = ("node", node_id)
                if neighbor not in came_from:
                    came_from[neighbor] = state
                    if neighbor == goal:
                        return _reconstruct(came_from, neighbor)
                    queue.append(neighbor)
        else:
            for way_id in node_to_ways.get(id_, ()):
                neighbor = ("way", way_id)
                if neighbor not in came_from:
                    came_from[neighbor] = state
                    if neighbor == goal:
                        return _reconstruct(came_from, neighbor)
                    queue.append(neighbor)

    return None


def _reconstruct(
    came_from: Dict[BipartiteState, Optional[BipartiteState]],
    target: BipartiteState,
) -> List[BipartiteState]:
    """Walk the came_from chain backwards to build the full path."""
    path: List[BipartiteState] = []
    cur: Optional[BipartiteState] = target
    while cur is not None:
        path.append(cur)
        cur = came_from[cur]
    path.reverse()
    return path


def bipartite_search(
    start_way_id: int,
    goal_way_id: int,
    way_to_nodes: Dict[int, Set[int]],
    node_to_ways: Dict[int, Set[int]],
    conn=None,
    max_expansions: int = 10,
    debug: bool = False,
) -> Optional[List[BipartiteState]]:
    """Run BFS with iterative batch expansion of pedestrian ways.

    1. Run BFS over the current in-memory index.
    2. If no path is found and *conn* is provided, collect every known node
       that has not yet been used as an expansion frontier, query the DB for
       pedestrian ways through those nodes in one batch, and merge them into
       the index.
    3. Re-run BFS on the now-larger graph.
    4. Repeat until a path is found, no new ways are discovered, or
       *max_expansions* rounds have been performed.
    """
    expanded_node_ids: Set[int] = set()

    for iteration in range(max_expansions + 1):
        result = _bipartite_bfs(start_way_id, goal_way_id, way_to_nodes, node_to_ways)
        if result is not None:
            return result

        if conn is None:
            break

        frontier: Set[int] = set()
        for nodes in way_to_nodes.values():
            frontier.update(nodes)
        frontier -= expanded_node_ids
        if not frontier:
            break

        new_ways = query_walkable_ways_by_nodes(
            conn, list(frontier), set(way_to_nodes.keys()),
        )
        expanded_node_ids.update(frontier)

        if not new_ways:
            if debug:
                print(f"[pathfind] Expansion {iteration + 1}: no new ways. Done.", flush=True)
            break

        for way_id, nodes in new_ways:
            _add_way_to_index(way_id, nodes, way_to_nodes, node_to_ways)

        if debug:
            print(
                f"[pathfind] Expansion {iteration + 1}: +{len(new_ways)} ways, "
                f"index now {len(way_to_nodes)} ways / {len(node_to_ways)} nodes",
                flush=True,
            )

    return None


# ---------------------------------------------------------------------------
# Distance / geometry helpers (used for platform-width calculations)
# ---------------------------------------------------------------------------

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 6_371_000 * 2 * math.asin(math.sqrt(min(1.0, a)))


def way_length_meters(
    node_ids: List[int],
    coords: Dict[int, Tuple[float, float]],
) -> Optional[float]:
    """Sum of haversine distances along consecutive nodes of a way.
    Returns None if any node is missing from *coords*."""
    if len(node_ids) < 2:
        return None
    total = 0.0
    for i in range(len(node_ids) - 1):
        a, b = node_ids[i], node_ids[i + 1]
        if a not in coords or b not in coords:
            return None
        total += haversine_meters(*coords[a], *coords[b])
    return total


def min_distance_between_node_sets(
    nodes_a: List[int],
    nodes_b: List[int],
    coords: Dict[int, Tuple[float, float]],
) -> Optional[float]:
    """Minimum haversine distance (metres) between any node in A and any
    node in B.  Useful for estimating platform width.
    Returns None if any node is missing from *coords*."""
    if not nodes_a or not nodes_b:
        return None
    best = math.inf
    for a in nodes_a:
        if a not in coords:
            return None
        for b in nodes_b:
            if b not in coords:
                return None
            best = min(best, haversine_meters(*coords[a], *coords[b]))
    return best if best != math.inf else None


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def find_path_between_edges(
    edge_1: Dict[str, Any],
    edge_2: Dict[str, Any],
    debug: bool = False,
) -> Optional[Dict[str, Any]]:
    """Find a walkable path between two platform edges within the same
    station (same relation_id).

    Steps:
      1. Load the station's way segments from the materialized view.
      2. Build the bipartite index and seed it with the two platform edges.
      3. Run BFS with iterative batch expansion of pedestrian ways.
      4. Return a result dict describing the path, or None.

    Uses the module-level connection pool (call init_pool() first).
    """
    if edge_1["relation_id"] != edge_2["relation_id"]:
        if debug:
            print("[pathfind] Different relation_ids — cannot connect.", flush=True)
        return None

    relation_id = edge_1["relation_id"]
    start_way = edge_1["way_id"]
    goal_way = edge_2["way_id"]

    conn = get_conn()
    try:
        if debug:
            print(f"[pathfind] Loading segments for relation {relation_id} ...", flush=True)
        segments = get_way_segments_for_relation(conn, relation_id)
        if debug:
            print(f"[pathfind] {len(segments)} segments, edge ways: {start_way} -> {goal_way}", flush=True)
        if not segments:
            return None

        way_to_nodes, node_to_ways = build_bipartite_index(segments)

        # Seed the index with the platform-edge ways themselves (they may not
        # be formal members of the station relation).
        for edge in (edge_1, edge_2):
            _add_way_to_index(edge["way_id"], edge["nodes"], way_to_nodes, node_to_ways)

        if debug:
            print(f"[pathfind] Initial index: {len(way_to_nodes)} ways, {len(node_to_ways)} nodes", flush=True)

        path = bipartite_search(
            start_way, goal_way,
            way_to_nodes, node_to_ways,
            conn=conn, debug=debug,
        )
        if path is None:
            if debug:
                print("[pathfind] No path found.", flush=True)
            return None

        return {
            "type": "way_path",
            "edge_1": edge_1,
            "edge_2": edge_2,
            "relation_id": relation_id,
            "path_sequence": path,
            "path_nodes": [id_ for k, id_ in path if k == "node"],
            "way_ids": [id_ for k, id_ in path if k == "way"],
        }
    finally:
        put_conn(conn)
