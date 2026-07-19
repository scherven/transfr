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

# Reorg bootstrap: this script lives in core/dbgen/ but imports the engine by
# bare name (db/graph/...). Put core/ and its submodule dirs on sys.path so it
# runs both directly and as `python -m core.dbgen.<name>`.
import os as _os
_C = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _p in (_C, _os.path.join(_C, "pathfinding"), _os.path.join(_C, "dbgen"), _os.path.join(_C, "viz")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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

            # TRUNCATE + repopulate in ONE transaction. A separate commit right
            # after the TRUNCATE would drop the old rows the instant it ran, so any
            # interrupt (Ctrl-C, crash, OOM, DB restart) during the long INSERT would
            # leave node_way_ids committed-EMPTY -- and an empty adjacency table makes
            # the engine return nothing for every platform, i.e. 100% of transfers
            # become platform_not_found. Kept atomic, the rebuild either lands whole
            # or the previous contents survive untouched.
            print("Rebuilding from osm_ways (TRUNCATE + repopulate in one transaction; "
                  "scans the full table once) ...", flush=True)
            t0 = time.monotonic()
            cur.execute("TRUNCATE TABLE node_way_ids")
            cur.execute(
                "INSERT INTO node_way_ids (node_id, way_ids) "
                "SELECT node_id, array_agg(DISTINCT way_id) "
                "FROM ("
                "  SELECT id AS way_id, unnest(nodes) AS node_id FROM osm_ways"
                ") x "
                "GROUP BY node_id"
            )
            rows = cur.rowcount
            conn.commit()
            print(f"Done in {time.monotonic() - t0:.1f}s, {rows:,} nodes indexed.", flush=True)

            print("Building index ...", flush=True)
            t0 = time.monotonic()
            cur.execute("ANALYZE node_way_ids")
            conn.commit()
            print(f"Analyzed in {time.monotonic() - t0:.1f}s.", flush=True)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted -- rolled back; the previous node_way_ids is left "
              "intact (the TRUNCATE + repopulate is a single atomic transaction).", flush=True)
        conn.rollback()
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
