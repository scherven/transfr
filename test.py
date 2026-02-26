"""
CLI entry point for testing platform-edge pathfinding.

Looks up two platform edges by station name and ref number, then finds a
walkable path between them — either a direct opposite-platform crossing or
a multi-way BFS path through station and pedestrian ways.
"""

import json
from typing import Dict, List, Any, Optional

from pathfind import (
    init_pool,
    close_pool,
    get_conn,
    put_conn,
    find_path_between_edges,
    get_node_coordinates,
    way_length_meters,
    min_distance_between_node_sets,
)

DB_CONFIG: Dict[str, Any] = {
    "host": "localhost",
    "database": "openrailwaymap",
    "user": "simonchervenak",
    "password": "",
    "port": 5432,
}


# ---------------------------------------------------------------------------
# Edge lookup helpers
# ---------------------------------------------------------------------------

def find_all_edges(station_name: str) -> List[Dict[str, Any]]:
    """Return every platform edge at *station_name* from the materialized view."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT relation_id, way_id, nodes, tags, edge_ref "
                "FROM platform_edges_indexed WHERE station_name = %s",
                (station_name,),
            )
            return [
                {
                    "relation_id": r[0],
                    "way_id": r[1],
                    "nodes": r[2],
                    "tags": json.loads(r[3]) if isinstance(r[3], str) else r[3],
                    "edge_ref": r[4],
                }
                for r in cur.fetchall()
            ]
    finally:
        put_conn(conn)


def find_edge(station_name: str, edge_ref: int) -> Optional[Dict[str, Any]]:
    """Look up a single platform edge by station name and ref number.

    Fast path: indexed lookup in platform_edges_indexed (edge shares a node
    directly with a station platform way).

    Fallback: find the edge by ref directly from planet_osm_ways, then
    associate it with the station by walking through pedestrian ways until
    we reach a station-member way.  This handles edges that are connected
    to the station only through intermediate footways/steps/corridors.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # --- Fast path: materialized view lookup ---
            cur.execute(
                "SELECT relation_id, way_id, nodes, tags, edge_ref "
                "FROM platform_edges_indexed "
                "WHERE station_name = %s AND edge_ref = %s LIMIT 1",
                (station_name, str(edge_ref)),
            )
            row = cur.fetchone()
            if row:
                print("got", row)
                # return {
                #     "relation_id": row[0],
                #     "way_id": row[1],
                #     "nodes": row[2],
                #     "tags": json.loads(row[3]) if isinstance(row[3], str) else row[3],
                #     "edge_ref": row[4],
                # }
                return row

            # --- Fallback: find edge by ref, associate with station ---
            return _find_edge_fallback(conn, cur, station_name, edge_ref)
    finally:
        put_conn(conn)


def _find_edge_fallback(
    conn, cur, station_name: str, edge_ref: int
) -> Optional[Dict[str, Any]]:
    """Find a platform_edge way by ref tag, then associate it with a station
    by iteratively expanding through pedestrian ways until we hit a node that
    belongs to a station-member way for the given station."""
    from pathfind import query_walkable_ways_by_nodes

    # 1. Find all platform_edge ways with this ref
    cur.execute(
        "SELECT id, nodes, tags FROM planet_osm_ways "
        "WHERE tags->>'railway' = 'platform_edge' AND tags->>'ref' = %s",
        (str(edge_ref),),
    )
    candidates = cur.fetchall()
    if not candidates:
        return None

    # 2. Get the relation_id for this station
    cur.execute(
        "SELECT DISTINCT relation_id FROM station_platform_ways "
        "WHERE station_name = %s",
        (station_name,),
    )
    relation_ids = {r[0] for r in cur.fetchall()}
    if not relation_ids:
        return None

    # 3. Load all node IDs that belong to this station's ways
    cur.execute(
        "SELECT DISTINCT unnest(nodes) AS node_id "
        "FROM station_ways_with_nodes_plus_pedestrian "
        "WHERE relation_id = ANY(%s)",
        (list(relation_ids),),
    )
    station_nodes = {r[0] for r in cur.fetchall()}

    # 4. For each candidate edge, check if it connects to the station
    #    (directly or through pedestrian way expansion)
    MAX_HOPS = 10
    for way_id, nodes, tags in candidates:
        edge_nodes = set(nodes)

        # Direct overlap?
        if edge_nodes & station_nodes:
            rel_id = _pick_relation(cur, relation_ids, station_nodes, edge_nodes)
            return _make_edge_dict(rel_id, way_id, nodes, tags, edge_ref)

        # Expand through pedestrian ways
        frontier = set(edge_nodes)
        seen_nodes = set(edge_nodes)
        seen_ways: set = {way_id}
        for _ in range(MAX_HOPS):
            new_ways = query_walkable_ways_by_nodes(conn, list(frontier), seen_ways)
            if not new_ways:
                break
            new_frontier: set = set()
            for nw_id, nw_nodes in new_ways:
                seen_ways.add(nw_id)
                for n in nw_nodes:
                    if n not in seen_nodes:
                        new_frontier.add(n)
                        seen_nodes.add(n)
            if new_frontier & station_nodes:
                rel_id = _pick_relation(cur, relation_ids, station_nodes, seen_nodes)
                return _make_edge_dict(rel_id, way_id, nodes, tags, edge_ref)
            if not new_frontier:
                break
            frontier = new_frontier

    return None


def _pick_relation(cur, relation_ids: set, station_nodes: set, edge_nodes: set) -> int:
    """When a station has multiple relation_ids, pick the one whose ways
    share the most nodes with the edge's reachable nodes."""
    if len(relation_ids) == 1:
        return next(iter(relation_ids))
    best_id, best_count = next(iter(relation_ids)), 0
    for rel_id in relation_ids:
        cur.execute(
            "SELECT unnest(nodes) AS node_id "
            "FROM station_ways_with_nodes WHERE relation_id = %s",
            (rel_id,),
        )
        rel_nodes = {r[0] for r in cur.fetchall()}
        overlap = len(rel_nodes & edge_nodes)
        if overlap > best_count:
            best_id, best_count = rel_id, overlap
    return best_id


def _make_edge_dict(rel_id: int, way_id: int, nodes, tags, edge_ref: int) -> Dict[str, Any]:
    return {
        "relation_id": rel_id,
        "way_id": way_id,
        "nodes": list(nodes),
        "tags": json.loads(tags) if isinstance(tags, str) else tags,
        "edge_ref": str(edge_ref),
    }


# ---------------------------------------------------------------------------
# Opposite-platform detection (two edges on the same platform)
# ---------------------------------------------------------------------------

def get_opposite_platform_connecting_way(
    edge_1: Dict[str, Any],
    edge_2: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """If two edges sit on opposite sides of the same platform, a single way
    in the station relation will contain nodes from both edges.  Returns that
    connecting way as {way_id, nodes}, or None."""
    if edge_1["relation_id"] != edge_2["relation_id"]:
        return None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT way_id, nodes FROM station_ways_with_nodes "
                "WHERE relation_id = %s "
                "  AND nodes && %s::bigint[] "
                "  AND nodes && %s::bigint[] "
                "LIMIT 1",
                (edge_1["relation_id"], edge_1["nodes"], edge_2["nodes"]),
            )
            row = cur.fetchone()
            return row
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# Main pathfinding entry point
# ---------------------------------------------------------------------------

def find_path(
    station_1: str, ref_1: int,
    station_2: str, ref_2: int,
    debug: bool = False,
) -> Optional[Dict[str, Any]]:
    """Find a walkable path between two platform edges.

    Tries in order:
      1. Opposite-platform shortcut (edges on the same platform, connected
         by a single way).  Also computes crossing length / platform width
         when node coordinates are available.
      2. BFS over station + pedestrian ways (with iterative batch expansion).
    """
    if debug:
        print(f"[find_path] Looking up edges: {station_1}#{ref_1} -> {station_2}#{ref_2}", flush=True)

    edge_1 = find_edge(station_1, ref_1)
    edge_2 = find_edge(station_2, ref_2)

    if not edge_1 or not edge_2:
        if debug:
            print("[find_path] Edge not found:", "edge_1" if not edge_1 else "edge_2", flush=True)
        return None

    if debug:
        print(f"[find_path] edge_1: relation={edge_1['relation_id']} way={edge_1['way_id']} nodes={len(edge_1['nodes'])}", flush=True)
        print(f"[find_path] edge_2: relation={edge_2['relation_id']} way={edge_2['way_id']} nodes={len(edge_2['nodes'])}", flush=True)

    # --- Case 1: opposite platform ---
    # connecting = get_opposite_platform_connecting_way(edge_1, edge_2)
    # if connecting:
    #     result: Dict[str, Any] = {
    #         "type": "opposite_platform",
    #         "edge_1": edge_1,
    #         "edge_2": edge_2,
    #         "connecting_way_id": connecting["way_id"],
    #     }
    #     conn = get_conn()
    #     try:
    #         all_node_ids = list(
    #             set(edge_1["nodes"]) | set(edge_2["nodes"]) | set(connecting["nodes"])
    #         )
    #         coords = get_node_coordinates(conn, all_node_ids)
    #         if coords:
    #             crossing = way_length_meters(connecting["nodes"], coords)
    #             if crossing is not None:
    #                 result["crossing_length_meters"] = round(crossing, 2)
    #             gap = min_distance_between_node_sets(edge_1["nodes"], edge_2["nodes"], coords)
    #             if gap is not None:
    #                 result["platform_width_meters"] = round(gap, 2)
    #     finally:
    #         put_conn(conn)
    #     return result

    # --- Case 2: BFS path through station / pedestrian ways ---
    if edge_1["relation_id"] == edge_2["relation_id"]:
        if debug:
            print("[find_path] Same station — running BFS pathfinding ...", flush=True)
        return find_path_between_edges(edge_1, edge_2, debug=debug)

    if debug:
        print("[find_path] Different stations with no opposite-platform link — no path.", flush=True)
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_pool(DB_CONFIG)
    try:
        # station = "München Hauptbahnhof"
        # e1 = 20
        # e2 = 22
        station = "Strasbourg-Ville"
        e1 = 1
        e2 = 7

        result = find_path(station, e1, station, e2, debug=True)

        if result:
            print("\n--- Result ---")
            print("Type:", result["type"])
            if result.get("way_ids"):
                print("Way IDs:", result["way_ids"])
            if result.get("path_nodes"):
                print("Path nodes:", len(result["path_nodes"]))
            if result.get("crossing_length_meters") is not None:
                print("Crossing length (m):", result["crossing_length_meters"])
            if result.get("platform_width_meters") is not None:
                print("Platform width (m):", result["platform_width_meters"])
        else:
            print("\nNo path found.")
    finally:
        close_pool()
