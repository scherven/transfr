import psycopg2
import json
from typing import List, Dict, Any, Optional

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
                tags
            FROM platform_edges_indexed
            WHERE station_name = %s
                AND edge_ref = %s
            LIMIT 1
        """, (station_name, str(edge_number)))
        
        result = cur.fetchone()
        
        if not result:
            return None
        
        rel_id, way_id, nodes, tags = result
        tags_data = json.loads(tags) if isinstance(tags, str) else tags
        
        return {
            'relation_id': rel_id,
            'way_id': way_id,
            'nodes': nodes,
            'tags': tags_data
        }
        
    finally:
        cur.close()
        conn.close()


def check_opposite_platform_optimized(
    db_config: Dict[str, str], 
    edge_1: Dict[str, Any], 
    edge_2: Dict[str, Any]
) -> bool:
    """
    Optimized check for opposite platform sides using materialized view.
    """
    if edge_1['relation_id'] != edge_2['relation_id']:
        return False
    
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    
    try:
        relation_id = edge_1['relation_id']
        edge_1_nodes = edge_1['nodes']
        edge_2_nodes = edge_2['nodes']
        
        # Use the materialized view to find connecting ways
        # This is much faster than the original query
        cur.execute("""
            SELECT way_id, nodes
            FROM station_ways_with_nodes
            WHERE relation_id = %s
                AND nodes && %s::bigint[]
                AND nodes && %s::bigint[]
            LIMIT 1
        """, (relation_id, edge_1_nodes, edge_2_nodes))
        
        result = cur.fetchone()
        
        if result:
            way_id, _ = result
            print(f"  Found connecting way {way_id} with nodes from both edges")
            return True
        
        return False
        
    finally:
        cur.close()
        conn.close()


def find_path_optimized(db_config: Dict[str, str], s1: str, e1: int, s2: str, e2: int) -> Optional[Dict[str, Any]]:
    """
    Find path between two platform edges using optimized queries.
    """
    edge_1 = find_optimized(db_config, s1, e1)
    edge_2 = find_optimized(db_config, s2, e2)
    
    if not edge_1 or not edge_2:
        return None

    # Case 1: Opposite side of platform
    if check_opposite_platform_optimized(db_config, edge_1, edge_2):
        return {
            'type': 'opposite_platform',
            'edge_1': edge_1,
            'edge_2': edge_2,
            'description': f"Platform edges {e1} and {e2} are on opposite sides of the same platform"
        }

    # Case 2: Buffer stops
    # Case 3: Crossings
    # Case 4: Stairs (hardest)
    
    return None

# Utility function to refresh materialized views
def refresh_views(db_config: Dict[str, str]):
    """
    Refresh all materialized views. Run this periodically or after database updates.
    """
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    
    try:
        print("Refreshing materialized views...")
        
        views = [
            'station_platform_ways',
            'station_platform_nodes',
            'platform_edges_indexed',
            'station_ways_with_nodes'
        ]
        
        for view in views:
            print(f"  Refreshing {view}...")
            cur.execute(f"REFRESH MATERIALIZED VIEW {view}")
        
        conn.commit()
        print("All views refreshed successfully!")
        
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    db_config = {
        'host': 'localhost',
        'database': 'openrailwaymap',
        'user': 'simonchervenak',
        'password': '',
        'port': 5432
    }

    # Uncomment to refresh views after database updates
    # refresh_views(db_config)

    s = "München Hauptbahnhof"
    e1 = 20
    e2 = 22
    
    edge1 = find_optimized(db_config, s, e1)
    edge2 = find_optimized(db_config, s, e2)
    print(find_buffer_stop_for_edge(db_config, edge1))
    print(find_buffer_stop_for_edge(db_config, edge2))