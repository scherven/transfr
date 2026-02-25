"""
CLI entry point for testing platform-edge pathfinding.

Looks up two platform edges by station name and ref number, then finds a
walkable path between them — either a direct opposite-platform crossing or
a multi-way BFS path through station and pedestrian ways.
"""

import json
from typing import Dict, List, Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from pathfind import (
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
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT relation_id, way_id, nodes, tags, edge_ref "
            "FROM platform_edges_indexed WHERE station_name = %s",
            (station_name,),
        )
        edges = []
        for rel_id, way_id, nodes, tags, edge_ref in cur.fetchall():
            edges.append({
                "relation_id": rel_id,
                "way_id": way_id,
                "nodes": nodes,
                "tags": json.loads(tags) if isinstance(tags, str) else tags,
                "edge_ref": edge_ref,
            })
        return edges
    finally:
        cur.close()
        conn.close()


def find_edge(station_name: str, edge_ref: int) -> Optional[Dict[str, Any]]:
    """Look up a single platform edge by station name and ref number."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT relation_id, way_id, nodes, tags, edge_ref "
            "FROM platform_edges_indexed "
            "WHERE station_name = %s AND edge_ref = %s LIMIT 1",
            (station_name, str(edge_ref)),
        )
        row = cur.fetchone()
        if not row:
            return None
        rel_id, way_id, nodes, tags, ref = row
        return {
            "relation_id": rel_id,
            "way_id": way_id,
            "nodes": nodes,
            "tags": json.loads(tags) if isinstance(tags, str) else tags,
            "edge_ref": ref,
        }
    finally:
        cur.close()
        conn.close()


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
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT way_id, nodes FROM station_ways_with_nodes "
            "WHERE relation_id = %s "
            "  AND nodes && %s::bigint[] "
            "  AND nodes && %s::bigint[] "
            "LIMIT 1",
            (edge_1["relation_id"], edge_1["nodes"], edge_2["nodes"]),
        )
        row = cur.fetchone()
        return {"way_id": row[0], "nodes": row[1]} if row else None
    finally:
        cur.close()
        conn.close()


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
    connecting = get_opposite_platform_connecting_way(edge_1, edge_2)
    if connecting:
        result: Dict[str, Any] = {
            "type": "opposite_platform",
            "edge_1": edge_1,
            "edge_2": edge_2,
            "connecting_way_id": connecting["way_id"],
        }
        # Compute width metrics when coordinates are available
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        try:
            all_node_ids = list(
                set(edge_1["nodes"]) | set(edge_2["nodes"]) | set(connecting["nodes"])
            )
            coords = get_node_coordinates(conn, all_node_ids)
            if coords:
                crossing = way_length_meters(connecting["nodes"], coords)
                if crossing is not None:
                    result["crossing_length_meters"] = round(crossing, 2)
                gap = min_distance_between_node_sets(edge_1["nodes"], edge_2["nodes"], coords)
                if gap is not None:
                    result["platform_width_meters"] = round(gap, 2)
        finally:
            conn.close()
        return result

    # --- Case 2: BFS path through station / pedestrian ways ---
    if edge_1["relation_id"] == edge_2["relation_id"]:
        if debug:
            print("[find_path] Same station — running BFS pathfinding ...", flush=True)
        return find_path_between_edges(DB_CONFIG, edge_1, edge_2, debug=debug)

    if debug:
        print("[find_path] Different stations with no opposite-platform link — no path.", flush=True)
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    station = "München Hauptbahnhof"
    e1 = 20
    e2 = 22

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
