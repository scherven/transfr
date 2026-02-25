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


def find_path_optimized(db_config: Dict[str, str], s1: str, e1: int, s2: str, e2: int) -> Optional[Dict[str, Any]]:
    """
    Find path between two platform edges using optimized queries.
    Uses A* over station ways to connect the two edges when they are not
    on opposite sides of the same platform.
    """
    edge_1 = find_optimized(db_config, s1, e1)
    edge_2 = find_optimized(db_config, s2, e2)

    if not edge_1 or not edge_2:
        return None

    # Case 1: Opposite side of platform (direct connection via one way)
    connecting = get_opposite_platform_connecting_way(db_config, edge_1, edge_2)
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
        path_result = find_path_between_platform_edges(db_config, edge_1, edge_2)
        if path_result:
            return path_result

    # Case 3: Buffer stops / different stations / no path
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

    edge1 = find_optimized(db_config, s, e1)
    edge2 = find_optimized(db_config, s, e2)
    print("Edge 1:", edge1)
    print("Edge 2:", edge2)

    # Path between the two platform edges (A* over station ways)
    path = find_path_between_platform_edges(db_config, edge1, edge2)
    if path:
        print("\nPath result:", path.get("type"))
        if path.get("path_nodes"):
            print("  Nodes:", len(path["path_nodes"]))
        if path.get("way_ids"):
            print("  Ways:", path["way_ids"])
    else:
        print("\nNo path found.")