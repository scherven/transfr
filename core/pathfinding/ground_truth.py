"""
Ground-truth platform-to-platform pathfinder.

find_shortest_path() dispatches to a pluggable algorithm (see algorithms.py
and core/algo_*.py) via SearchContext (see search_context.py), which
resolves a station's platform edges and provides lazy, database-backed
neighbor access shared by every algorithm. Defaults to "dijkstra", the
ground-truth baseline: textbook Dijkstra over real walking-time edge
weights, provably optimal for non-negative weights.

Also kept here: find_shortest_path_eager(), the original "load a bounded
neighborhood, then search" version. It's correct, but was found to be
impractical: Berlin Hauptbahnhof's 1000m closure is ~3,500 ways / ~9,800
nodes and took ~17.6 minutes, because a major European station's
surrounding few blocks are often one dense, fully-interconnected pedestrian
mesh -- almost none of which lies on the actual shortest path between two
given platforms. Dijkstra is inherently incremental -- it only needs to
know a node's neighbors when it actually pops that node off the priority
queue, and it can stop the instant it reaches the target -- so the lazy
SearchContext-based path fetches graph structure from the database exactly
where the search goes, touching a tiny fraction of what the eager version
loaded. Same algorithm, same correctness guarantee; only the data access
pattern differs. find_shortest_path_eager() is kept for documentation and
as a cross-check in tests, not as something new code should call.
"""

from typing import Any, Dict, List, Optional, Tuple

from algorithms import ALGORITHMS, BASELINE
from dijkstra import shortest_path
from graph import (
    Coords,
    Ways,
    build_time_weighted_graph,
    collapse_port_path,
    haversine_meters,
    load_node_tags,
    load_station_ways,
)
from search_context import (
    SearchContext,
    find_platform_edges,
    find_station_relations,
)

__all__ = [
    "find_station_relations",
    "find_platform_edges",
    "find_shortest_path",
    "find_shortest_path_eager",
    "find_shortest_path_in_graph",
]


def find_shortest_path(
    conn,
    relation_id: int,
    ref_1: str,
    ref_2: str,
    algorithm: str = BASELINE,
    use_adjacency_table: bool = True,
    use_stitch_bridges: bool = False,
    avoid_elevators: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    """Find the true shortest (by walking time) path between two platform
    edges at a station.

    Always returns a dict with a "found" key so callers/tests can inspect
    *why* a path wasn't found (platform missing vs. genuinely disconnected
    vs. exceeded the plausibility bound) instead of just getting None.

    algorithm selects which search strategy to run (see algorithms.py);
    kwargs are passed through to it (e.g. max_search_seconds, progress_cb
    for "dijkstra"/"astar"). All registered algorithms are checked against
    the "dijkstra" baseline in tests/test_ground_truth.py.

    use_adjacency_table (default True) switches SearchContext's
    neighbor-fetching from a GIN bitmap-heap-scan to point lookups against
    the precomputed node_way_ids table (see core/build_node_way_ids.py) --
    an algorithm-independent speedup, measured 1.3x-11.7x across every real
    test station with identical results. Pass False to fall back to the
    GIN scan, e.g. if node_way_ids hasn't been rebuilt after a fresh
    core/etl.py load.
    """
    search_fn = ALGORITHMS[algorithm]
    with conn.cursor() as cur:
        ctx = SearchContext(cur, relation_id, ref_1, ref_2, use_adjacency_table=use_adjacency_table,
                            use_stitch_bridges=use_stitch_bridges, avoid_elevators=avoid_elevators)
        if ctx.error is not None:
            return ctx.error
        return search_fn(ctx, **kwargs)


# ---------------------------------------------------------------------------
# Eager version -- kept for documentation / cross-checking against the lazy
# version(s) in tests, not meant to be called by new code (see module
# docstring for why).
# ---------------------------------------------------------------------------

def _way_ids_for_hops(graph, node_path: List[int]) -> List[Optional[int]]:
    way_ids = []
    for a, b in zip(node_path, node_path[1:]):
        match = next((wid for (nb, _w, wid) in graph.get(a, []) if nb == b), None)
        way_ids.append(match)
    return way_ids


def find_shortest_path_in_graph(
    ways: Ways, coords: Coords, ref_1: str, ref_2: str,
    relation_id: Optional[int] = None,
    node_tags: Optional[Dict[int, Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Search logic over an *already-loaded* (eager) station graph.

    node_tags (node_id -> tags), if given, enables node-mapped vertical
    circulation cost (elevators/stairs tagged on a shared node); see
    build_time_weighted_graph. Omitting it reproduces the plain 2D graph."""
    graph = build_time_weighted_graph(ways, coords, node_tags)

    edges_1 = find_platform_edges(ways, ref_1)
    edges_2 = find_platform_edges(ways, ref_2)
    if not edges_1 or not edges_2:
        return {
            "found": False,
            "reason": "platform_not_found",
            "ref_1_matches": len(edges_1),
            "ref_2_matches": len(edges_2),
            "graph_ways": len(ways),
            "graph_nodes": len(coords),
        }

    sources = {n for _, nodes in edges_1 for n in nodes if n in coords}
    targets = {n for _, nodes in edges_2 for n in nodes if n in coords}
    if not sources or not targets:
        return {"found": False, "reason": "no_coordinates_for_platform_nodes"}

    result = shortest_path(graph, sources, targets)
    if result is None:
        return {
            "found": False,
            "reason": "disconnected",
            "graph_ways": len(ways),
            "graph_nodes": len(coords),
            "edge_1_way_ids": [w for w, _ in edges_1],
            "edge_2_way_ids": [w for w, _ in edges_2],
        }

    total_seconds, vertex_path = result
    hop_way_ids = _way_ids_for_hops(graph, vertex_path)
    distinct_way_ids = list(dict.fromkeys(w for w in hop_way_ids if w is not None))

    # vertex_path may hold (node, level) ports at split vertical nodes; collapse
    # to real node ids (their vertical self-hops drop out) for output/distance.
    node_path = collapse_port_path(vertex_path)
    total_distance = sum(
        haversine_meters(coords[a][0], coords[a][1], coords[b][0], coords[b][1])
        for a, b in zip(node_path, node_path[1:])
    )

    return {
        "found": True,
        "relation_id": relation_id,
        "edge_1_way_ids": [w for w, _ in edges_1],
        "edge_2_way_ids": [w for w, _ in edges_2],
        "walking_time_seconds": round(total_seconds, 1),
        "walking_distance_meters": round(total_distance, 1),
        "node_path": node_path,
        "way_path": distinct_way_ids,
        "graph_ways": len(ways),
        "graph_nodes": len(coords),
    }


def find_shortest_path_eager(conn, relation_id: int, ref_1: str, ref_2: str) -> Dict[str, Any]:
    """Load the station's full bounded closure, then search it. Slow on
    large/dense stations -- see module docstring. Prefer find_shortest_path()."""
    ways, coords = load_station_ways(conn, relation_id)
    node_tags = load_node_tags(conn, coords.keys())
    return find_shortest_path_in_graph(ways, coords, ref_1, ref_2, relation_id=relation_id, node_tags=node_tags)
