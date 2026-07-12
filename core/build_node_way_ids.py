#!/usr/bin/env python3
"""
Build (or rebuild) the node_way_ids materialized adjacency table from the
current osm_ways table. Run this once after core/etl.py loads/reloads data.

Idempotent: TRUNCATEs and rebuilds from scratch, so there's no partial-
staleness state -- either it reflects the current osm_ways, or you haven't
run it yet.
"""
import sys
import time

from db import connect


def main() -> int:
    conn = connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            print("Creating table (if not exists) ...", flush=True)
            cur.execute(
                "CREATE TABLE IF NOT EXISTS node_way_ids ("
                "  node_id BIGINT PRIMARY KEY,"
                "  way_ids BIGINT[] NOT NULL"
                ")"
            )
            conn.commit()

            print("Truncating for a clean rebuild ...", flush=True)
            cur.execute("TRUNCATE TABLE node_way_ids")
            conn.commit()

            print("Populating from osm_ways (this scans the full table once) ...", flush=True)
            t0 = time.monotonic()
            cur.execute(
                "INSERT INTO node_way_ids (node_id, way_ids) "
                "SELECT node_id, array_agg(DISTINCT way_id) "
                "FROM ("
                "  SELECT id AS way_id, unnest(nodes) AS node_id FROM osm_ways"
                ") x "
                "GROUP BY node_id"
            )
            conn.commit()
            print(f"Done in {time.monotonic() - t0:.1f}s, {cur.rowcount:,} nodes indexed.", flush=True)

            print("Building index ...", flush=True)
            t0 = time.monotonic()
            cur.execute("ANALYZE node_way_ids")
            conn.commit()
            print(f"Analyzed in {time.monotonic() - t0:.1f}s.", flush=True)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted -- rolling back partial work (table is empty/unaffected).", flush=True)
        conn.rollback()
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
