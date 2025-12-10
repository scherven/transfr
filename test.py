import psycopg2
import json
import tqdm
from typing import List, Dict, Any

def find_platform_edges(station_name: str, db_config: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Find all platform edges for a given railway station.
    
    Args:
        station_name: The name of the railway station
        db_config: Dictionary with database connection parameters
                   (host, database, user, password, port)
    
    Returns:
        List of dictionaries containing platform edge information
    """
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    
    try:
        # Step 1: Find all relations with the given station name
        cur.execute("""
            SELECT id, members
            FROM planet_osm_rels
            WHERE tags->>'name' = %s
        """, (station_name,))
        
        relations = cur.fetchall()
        
        if not relations:
            print(f"No relations found for station: {station_name}")
            return []
        
        # print(f"Found {len(relations)} relation(s) for station: {station_name}")
        
        all_platform_edges = []
        
        # Step 2: Process each relation
        for rel_id, members in relations:
            print(f"\nProcessing relation ID: {rel_id}")
            
            # Parse members JSON and extract platform ways
            members_data = json.loads(members) if isinstance(members, str) else members
            
            platform_way_refs = [
                member['ref'] 
                for member in members_data 
                if member.get('role') == 'platform' and member.get('type') == 'W'
            ]
            
            if not platform_way_refs:
                print(f"  No platform ways found in relation {rel_id}")
                continue
            
            print(f"  Found {len(platform_way_refs)} platform way(s)")
            
            # Step 3: Get all nodes from platform ways
            cur.execute("""
                SELECT ARRAY_AGG(DISTINCT unnest_nodes) as all_nodes
                FROM (
                    SELECT unnest(nodes) as unnest_nodes
                    FROM planet_osm_ways
                    WHERE id = ANY(%s)
                ) subq
            """, (platform_way_refs,))
            
            result = cur.fetchone()
            platform_nodes = result[0] if result and result[0] else []
            
            if not platform_nodes:
                # print(f"  No nodes found in platform ways")
                continue
            
            # print(f"  Found {len(platform_nodes)} unique node(s) in platforms")
            
            # Step 4: Find ways with railway=platform_edge that share nodes with platforms
            cur.execute("""
                SELECT id, nodes, tags
                FROM planet_osm_ways
                WHERE tags->>'railway' = 'platform_edge'
                AND nodes && %s::bigint[]
            """, (platform_nodes,))
            
            platform_edges = cur.fetchall()
            
            for way_id, nodes, tags in platform_edges:
                tags_data = json.loads(tags) if isinstance(tags, str) else tags
                
                # Find which nodes are shared
                shared_nodes = list(set(nodes) & set(platform_nodes))
                
                platform_edge_info = {
                    'relation_id': rel_id,
                    'way_id': way_id,
                    'nodes': nodes,
                    'shared_nodes': shared_nodes,
                    'tags': tags_data
                }
                all_platform_edges.append(platform_edge_info)
                
                # print(f"    Found platform_edge way: {way_id} with {len(nodes)} nodes "
                    #   f"({len(shared_nodes)} shared)")
        
        return all_platform_edges
        
    finally:
        cur.close()
        conn.close()

def find(db_config, station_name, edge_number):
    platform_edges = find_platform_edges(station_name, db_config)

    if not platform_edges:
        return None

    for edge in platform_edges:
        try:
            if edge['tags']['ref'] == str(edge_number):
                return edge
        except KeyError:
            continue
        
def find_path(db, s1, e1, s2, e2):
    edge_1 = find(db, s1, e1)
    edge_2 = find(db, s2, e2)
    if not edge_1 or not edge_2:
        return None

    # Case 1: Opposite side of platform
    if check_opposite_platform(db, edge_1, edge_2):
        return {
            'type': 'opposite_platform',
            'edge_1': edge_1,
            'edge_2': edge_2,
            'description': f"Platform edges {e1} and {e2} are on opposite sides of the same platform"
        }
    # Case 2: Buffer stops
    # Case 3: Crossings
    # Case 4: Stairs (hardest)

def check_opposite_platform(db_config: Dict[str, str], edge_1: Dict[str, Any], edge_2: Dict[str, Any]) -> bool:
    # Only check if both edges belong to the same relation
    # if edge_1['relation_id'] != edge_2['relation_id']:
        # return False
    
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    
    try:
        relation_id = edge_1['relation_id']
        edge_1_nodes = set(edge_1['nodes'])
        edge_2_nodes = set(edge_2['nodes'])
        
        # Get all way members from the relation
        cur.execute("""
            SELECT members
            FROM planet_osm_rels
            WHERE id = %s
        """, (relation_id,))
        
        result = cur.fetchone()
        if not result:
            return False
        
        members_data = json.loads(result[0]) if isinstance(result[0], str) else result[0]
        
        # Extract all way references from the relation
        way_refs = [
            member['ref'] 
            for member in members_data 
            if member.get('type') == 'W'
        ]
        
        if not way_refs:
            return False
        
        # Check each way to see if it contains nodes from both edges
        cur.execute("""
            SELECT id, nodes
            FROM planet_osm_ways
            WHERE id = ANY(%s)
        """, (way_refs,))
        
        ways = cur.fetchall()
        
        for way_id, nodes in ways:
            way_nodes = set(nodes)
            
            # Check if this way contains at least one node from each edge
            has_edge_1_node = bool(way_nodes & edge_1_nodes)
            has_edge_2_node = bool(way_nodes & edge_2_nodes)
            
            if has_edge_1_node and has_edge_2_node:
                print(f"  Found connecting way {way_id} with nodes from both edges")
                return True
        
        return False
        
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

    # print(find(db_config, "München Hauptbahnhof", 11))
    s = "München Hauptbahnhof"
    e1 = 20
    e2 = 22
    print(find(db_config, s, e1))
    print(find(db_config, s, e2))
    print(find_path(db_config, s, e1, s, e2))