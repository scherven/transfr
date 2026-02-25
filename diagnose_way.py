"""
Diagnose why a way might be missing from pathfinding (node_to_ways incomplete).
Usage: python diagnose_way.py [way_id] [node_id]
Default: way_id=695710426, node_id=3757354115
"""
import sys
import json
import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": "localhost",
    "database": "openrailwaymap",
    "user": "simonchervenak",
    "password": "",
    "port": 5432,
}


def main():
    way_id = int(sys.argv[1]) if len(sys.argv) > 1 else 695710426
    node_id = int(sys.argv[2]) if len(sys.argv) > 2 else 3757354115

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    try:
        print("=== 1. Raw way in planet_osm_ways ===")
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nodes, array_length(nodes, 1) AS n_nodes, tags FROM planet_osm_ways WHERE id = %s",
            (way_id,),
        )
        row = cur.fetchone()
        if not row:
            print(f"Way {way_id} not found in planet_osm_ways.")
        else:
            nodes = row["nodes"]
            n_nodes = len(nodes) if nodes else 0
            has_node = node_id in (nodes or [])
            print(f"way_id={way_id} nodes_count={n_nodes} node {node_id} in way: {has_node}")
            if nodes:
                print(f"  First 5 nodes: {list(nodes)[:5]} ... last 5: {list(nodes)[-5:]}")
            print(f"  tags: {json.dumps(dict(row['tags']) if row.get('tags') else {}, default=str)[:200]}")

        print("\n=== 2. Is this way in station_ways_with_nodes? (relation members) ===")
        cur.execute(
            """
            SELECT relation_id, station_name, way_id, array_length(nodes, 1) AS n_nodes
            FROM station_ways_with_nodes
            WHERE way_id = %s
            """,
            (way_id,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"Way {way_id} is NOT in station_ways_with_nodes (not a member of any stop_area relation).")
        else:
            for r in rows:
                print(f"  relation_id={r['relation_id']} station={r['station_name']!r} n_nodes={r['n_nodes']}")

        print("\n=== 3. Is this way in station_pedestrian_ways_with_nodes? (highway + shares node) ===")
        cur.execute(
            """
            SELECT relation_id, station_name, way_id, array_length(w.nodes, 1) AS n_nodes,
                   w.tags->>'highway' AS highway
            FROM planet_osm_ways w
            JOIN station_ways_with_nodes s ON w.nodes && s.nodes
            WHERE w.id = %s
              AND w.tags->>'highway' IN (
                'footway', 'steps', 'corridor', 'pedestrian', 'path', 'cycleway', 'crossing'
              )
            """,
            (way_id,),
        )
        rows = cur.fetchall()
        if not rows:
            cur.execute(
                "SELECT tags->>'highway' AS highway FROM planet_osm_ways WHERE id = %s",
                (way_id,),
            )
            h = cur.fetchone()
            highway = h["highway"] if h else None
            print(f"Way {way_id} is NOT in station_pedestrian_ways_with_nodes.")
            print(f"  (highway tag = {highway!r}; must be one of: footway, steps, corridor, pedestrian, path, cycleway, crossing)")
            print("  Or it does not share any node with station_ways_with_nodes.")
        else:
            for r in rows:
                print(f"  relation_id={r['relation_id']} station={r['station_name']!r} highway={r['highway']} n_nodes={r['n_nodes']}")

        print("\n=== 4. Is this way in station_ways_with_nodes_plus_pedestrian (MV)? ===")
        cur.execute(
            """
            SELECT relation_id, station_name, way_id, array_length(nodes, 1) AS n_nodes
            FROM station_ways_with_nodes_plus_pedestrian
            WHERE way_id = %s
            """,
            (way_id,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"Way {way_id} is NOT in the materialized view station_ways_with_nodes_plus_pedestrian.")
            print("  So it will never appear in station_way_segments, and our code will never see it.")
        else:
            for r in rows:
                print(f"  relation_id={r['relation_id']} station={r['station_name']!r} n_nodes={r['n_nodes']}")

        print("\n=== 5. Segments for this way in station_way_segments (any relation) ===")
        cur.execute(
            """
            SELECT relation_id, station_name, way_id, node_from, node_to
            FROM station_way_segments
            WHERE way_id = %s
            LIMIT 20
            """,
            (way_id,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"No segments for way_id={way_id} in station_way_segments.")
            print("  So build_bipartite_index() never sees this way -> node_to_ways will not include it.")
        else:
            has_node_in_segments = any(
                r["node_from"] == node_id or r["node_to"] == node_id for r in rows
            )
            print(f"  Found {len(rows)} segment rows (showing up to 20). Node {node_id} in any segment: {has_node_in_segments}")
            for r in rows[:5]:
                print(f"    relation_id={r['relation_id']} node_from={r['node_from']} node_to={r['node_to']}")

        print("\n=== 6. Why our code might miss it ===")
        cur.execute(
            "SELECT 1 FROM station_ways_with_nodes_plus_pedestrian WHERE way_id = %s LIMIT 1",
            (way_id,),
        )
        in_mv = cur.fetchone() is not None
        if not in_mv:
            print("  - Way is not in station_ways_with_nodes_plus_pedestrian.")
            print("    Add it by either:")
            print("    1) Making it a member of the stop_area relation (then it appears in station_ways_with_nodes), or")
            print("    2) Tagging it highway=footway|steps|corridor|pedestrian|path|cycleway|crossing AND ensuring it shares a node with a station way (then it appears in station_pedestrian_ways_with_nodes).")
            print("    Then run: REFRESH MATERIALIZED VIEW station_ways_with_nodes_plus_pedestrian;")
        else:
            print("  - Way IS in the MV. If we still don't see it for a given pathfinding run, we only load segments for one relation_id.")
            print("    So we only get this way when pathfinding in a station (relation) that includes this way.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
