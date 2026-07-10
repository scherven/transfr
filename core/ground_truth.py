"""
Ground-truth platform-to-platform pathfinder.

Deliberately simple and unoptimized, on purpose:

  * Loads the ENTIRE walkable graph reachable from a station's relation
    closure before searching at all -- no cap on expansion rounds, so we
    can never miss a real path just because we stopped expanding too soon.
  * Runs textbook Dijkstra (see dijkstra.py) over real walking-time edge
    weights -- not "fewest ways", which is a different (and wrong) thing
    to optimize for transfer feasibility.

This is meant to be slow-but-obviously-correct: the reference implementation
that faster algorithms get checked against, not the one that serves live
traffic. Every design choice here favors "easy to convince yourself this is
right" over performance.
"""

from typing import Any, Dict, List, Optional, Tuple

from dijkstra import shortest_path
from graph import build_time_weighted_graph, haversine_meters, load_station_ways, Ways


def find_station_relations(conn, name: str) -> List[int]:
    """Relation ids for stop_area/stop_area_group relations with this exact
    name. Can return more than one -- OSM station names are not unique (see
    core/NOTES.md) -- so callers should disambiguate deliberately rather
    than have this function silently pick one."""
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


def find_platform_edges(ways: Ways, ref: str) -> List[Tuple[int, List[int]]]:
    """All platform_edge ways in *ways* whose ref (or, failing that,
    railway:track_ref) matches. Returns every match rather than picking one,
    since some stations tag more than one way with the same ref."""
    ref = str(ref)
    exact = [
        (way_id, info["nodes"])
        for way_id, info in ways.items()
        if info["tags"].get("railway") == "platform_edge" and info["tags"].get("ref") == ref
    ]
    if exact:
        return exact
    return [
        (way_id, info["nodes"])
        for way_id, info in ways.items()
        if info["tags"].get("railway") == "platform_edge"
        and _track_ref_matches(info["tags"].get("railway:track_ref"), ref)
    ]


def _way_ids_for_hops(graph, node_path: List[int]) -> List[Optional[int]]:
    way_ids = []
    for a, b in zip(node_path, node_path[1:]):
        match = next((wid for (nb, _w, wid) in graph.get(a, []) if nb == b), None)
        way_ids.append(match)
    return way_ids


def find_shortest_path(conn, relation_id: int, ref_1: str, ref_2: str) -> Dict[str, Any]:
    """Find the true shortest (by walking time) path between two platform
    edges identified by ref within a single station relation's full
    walkable closure.

    Always returns a dict with a "found" key so callers/tests can inspect
    *why* a path wasn't found (platform missing vs. genuinely disconnected)
    instead of just getting None.
    """
    ways, coords = load_station_ways(conn, relation_id)
    graph = build_time_weighted_graph(ways, coords)

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

    total_seconds, node_path = result
    hop_way_ids = _way_ids_for_hops(graph, node_path)
    distinct_way_ids = list(dict.fromkeys(w for w in hop_way_ids if w is not None))

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
