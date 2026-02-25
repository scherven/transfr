import psycopg2
import json
from typing import List, Dict, Any, Optional
from psycopg2.extras import RealDictCursor

from pathfind import (
    find_path_between_platform_edges,
    get_node_coordinates,
    way_length_meters,
    min_distance_between_node_sets_meters,
)

def find_platform_edges_optimized(station_name: str, db_config: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Find all platform edges for a given railway station using materialized views.
    Much faster than the original implementation.
    """
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    
    try:
        # Single query using the materialized view
        cur.execute("""
            SELECT 
                relation_id,
                way_id,
                nodes,
                tags,
                edge_ref
            FROM platform_edges_indexed
            WHERE station_name = %s
        """, (station_name,))
        
        results = cur.fetchall()
        
        if not results:
            print(f"No platform edges found for station: {station_name}")
            return []
        
        print(f"Found {len(results)} platform edge(s) for station: {station_name}")
        
        all_platform_edges = []
        for rel_id, way_id, nodes, tags, edge_ref in results:
            tags_data = json.loads(tags) if isinstance(tags, str) else tags
            
            platform_edge_info = {
                'relation_id': rel_id,
                'way_id': way_id,
                'nodes': nodes,
                'tags': tags_data,
                'edge_ref': edge_ref
            }
            all_platform_edges.append(platform_edge_info)
        
        return all_platform_edges
        
    finally:
        cur.close()
        conn.close()


def find_optimized(db_config: Dict[str, str], station_name: str, edge_number: int) -> Optional[Dict[str, Any]]:
    """
    Find a specific platform edge by station name and edge reference number.
    Uses materialized view for fast lookup.
    """
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT 
                relation_id,
                way_id,
                nodes,
                tags,
                edge_ref
            FROM platform_edges_indexed
            WHERE station_name = %s
                AND edge_ref = %s
            LIMIT 1
        """, (station_name, str(edge_number)))
        
        result = cur.fetchone()
        
        if not result:
            return None
        
        rel_id, way_id, nodes, tags, edge_ref = result
        tags_data = json.loads(tags) if isinstance(tags, str) else tags
        
        return {
            'relation_id': rel_id,
            'way_id': way_id,
            'nodes': nodes,
            'tags': tags_data,
            'edge_ref': edge_ref,
        }
        
    finally:
        cur.close()
        conn.close()


def check_opposite_platform_optimized(
    db_config: Dict[str, str],
    edge_1: Dict[str, Any],
    edge_2: Dict[str, Any],
) -> bool:
    """
    Optimized check for opposite platform sides using materialized view.
    """
    return get_opposite_platform_connecting_way(db_config, edge_1, edge_2) is not None


def get_opposite_platform_connecting_way(
    db_config: Dict[str, str],
    edge_1: Dict[str, Any],
    edge_2: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    If the two edges are on opposite sides of the same platform, returns the
    connecting way as {way_id, nodes}. Otherwise returns None.
    Used to compute platform width (length of crossing, min gap between edges).
    """
    if edge_1["relation_id"] != edge_2["relation_id"]:
        return None

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT way_id, nodes
            FROM station_ways_with_nodes
            WHERE relation_id = %s
                AND nodes && %s::bigint[]
                AND nodes && %s::bigint[]
            LIMIT 1
            """,
            (edge_1["relation_id"], edge_1["nodes"], edge_2["nodes"]),
        )
        result = cur.fetchone()
        if not result:
            return None
        way_id, nodes = result
        return {"way_id": way_id, "nodes": nodes}
    finally:
        cur.close()
        conn.close()


def find_path_optimized(
    db_config: Dict[str, str], s1: str, e1: int, s2: str, e2: int, debug: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Find path between two platform edges using optimized queries.
    Uses A* over station ways to connect the two edges when they are not
    on opposite sides of the same platform.
    Set debug=True to print pathfinding diagnostics.
    """
    if debug:
        print("[find_path_optimized] Looking up edge_1 ...", flush=True)
    edge_1 = find_optimized(db_config, s1, e1)
    if debug:
        print("[find_path_optimized] Looking up edge_2 ...", flush=True)
    edge_2 = find_optimized(db_config, s2, e2)

    if debug:
        print("[find_path_optimized] edge_1:", "found" if edge_1 else "NOT FOUND", f"(station={s1!r}, edge_ref={e1})", flush=True)
        print("[find_path_optimized] edge_2:", "found" if edge_2 else "NOT FOUND", f"(station={s2!r}, edge_ref={e2})", flush=True)
        if edge_1:
            print("  edge_1 relation_id:", edge_1.get("relation_id"), "way_id:", edge_1.get("way_id"), "nodes:", len(edge_1.get("nodes", [])), flush=True)
        if edge_2:
            print("  edge_2 relation_id:", edge_2.get("relation_id"), "way_id:", edge_2.get("way_id"), "nodes:", len(edge_2.get("nodes", [])), flush=True)

    if not edge_1 or not edge_2:
        return None

    # Case 1: Opposite side of platform (direct connection via one way)
    if debug:
        print("[find_path_optimized] Checking opposite_platform (DB query) ...", flush=True)
    connecting = get_opposite_platform_connecting_way(db_config, edge_1, edge_2)
    if debug:
        print("[find_path_optimized] opposite_platform check:", "yes" if connecting else "no", "(connecting way_id:" + str(connecting["way_id"]) + ")" if connecting else "", flush=True)
    if connecting:
        result = {
            "type": "opposite_platform",
            "edge_1": edge_1,
            "edge_2": edge_2,
            "connecting_way_id": connecting["way_id"],
            "description": f"Platform edges {e1} and {e2} are on opposite sides of the same platform",
        }
        # Width/distance when node coordinates are available (planet_osm_nodes)
        conn = psycopg2.connect(**db_config, cursor_factory=RealDictCursor)
        try:
            all_node_ids = list(
                set(edge_1["nodes"]) | set(edge_2["nodes"]) | set(connecting["nodes"])
            )
            coords = get_node_coordinates(conn, all_node_ids)
            if coords is not None:
                crossing_m = way_length_meters(connecting["nodes"], coords)
                if crossing_m is not None:
                    result["crossing_length_meters"] = round(crossing_m, 2)
                gap_m = min_distance_between_node_sets_meters(
                    edge_1["nodes"], edge_2["nodes"], coords
                )
                if gap_m is not None:
                    result["platform_width_meters"] = round(gap_m, 2)
        finally:
            conn.close()
        return result

    # Case 2: Same station – A* path over station ways
    if edge_1["relation_id"] == edge_2["relation_id"]:
        if debug:
            print("[find_path_optimized] Same station: running A* pathfinding ...", flush=True)
        path_result = find_path_between_platform_edges(db_config, edge_1, edge_2, debug=debug)
        if path_result:
            return path_result

    # Case 3: Buffer stops / different stations / no path
    if debug:
        print("[find_path_optimized] No path returned (tried opposite_platform and A*).")
    return None

def get_station_pedestrian_ways(
    db_config: Dict[str, str],
    station_name: str
) -> List[Dict[str, Any]]:
    """
    Get all pedestrian ways (footpaths, steps, corridors, elevators) in a station.
    
    Returns list of dicts with way info including nodes and tags.
    """
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    
    try:
        # Get pedestrian ways from the station
        # Try formal relation members first
        cur.execute("""
            SELECT 
                way_id,
                nodes,
                tags,
                highway_type,
                way_name,
                indoor,
                level
            FROM station_pedestrian_ways
            WHERE station_name = %s
        """, (station_name,))
        
        formal_ways = cur.fetchall()
        
        # Also get ways that share nodes with station but aren't formally in relation
        cur.execute("""
            SELECT 
                way_id,
                nodes,
                tags,
                highway_type,
                way_name,
                indoor,
                level
            FROM station_area_pedestrian_ways
            WHERE station_name = %s
        """, (station_name,))
        
        area_ways = cur.fetchall()
        
        # Combine and deduplicate
        all_ways = {}
        for way_id, nodes, tags, highway_type, way_name, indoor, level in formal_ways + area_ways:
            if way_id not in all_ways:
                tags_data = json.loads(tags) if isinstance(tags, str) else tags
                all_ways[way_id] = {
                    'way_id': way_id,
                    'nodes': nodes,
                    'tags': tags_data,
                    'highway_type': highway_type,
                    'name': way_name,
                    'indoor': indoor,
                    'level': level
                }
        
        result = list(all_ways.values())
        print(f"Found {len(result)} pedestrian ways for station: {station_name}")
        return result
        
    finally:
        cur.close()
        conn.close()

# psql -h localhost -d openrailwaymap -U simonchervenak -f views.sql
if __name__ == "__main__":
    db_config = {
        "host": "localhost",
        "database": "openrailwaymap",
        "user": "simonchervenak",
        "password": "",
        "port": 5432,
    }

    # Uncomment to refresh views after database updates
    # refresh_views(db_config)

    s = "München Hauptbahnhof"
    e1 = 20
    e2 = 22

    # Use find_path_optimized for full flow: opposite_platform first, then A*
    path = find_path_optimized(db_config, s, e1, s, e2, debug=True)
    if path:
        print("\n--- Result ---")
        print("Type:", path.get("type"))
        if path.get("path_nodes"):
            print("Path nodes:", len(path["path_nodes"]))
        if path.get("way_ids"):
            print("Way IDs:", path["way_ids"])
        if path.get("crossing_length_meters") is not None:
            print("Crossing length (m):", path["crossing_length_meters"])
        if path.get("platform_width_meters") is not None:
            print("Platform width (m):", path["platform_width_meters"])
    else:
        print("\nNo path found. See [pathfind] / [find_path_optimized] debug output above.")