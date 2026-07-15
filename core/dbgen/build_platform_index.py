#!/usr/bin/env python3
"""
Build what core/ needs to resolve a track number that OSM records only on a
stop_position node (Tier 2 -- see core/PLATFORM-RESOLUTION.md):

  station_stops  -- every *rail* stop_position / railway=stop node that carries
                    a ref/local_ref (the numeric track), by coordinate. Lets us
                    find "where is track N" at a station. Bus/tram stops are
                    excluded (see STATION_STOPS_INSERT): their letter/number refs
                    collide with rail tracks and would resolve a transfer to an
                    unrelated bus bay.
  osm_nodes(lat), osm_nodes(lon) -- a coordinate index on all nodes. The track's
                    stop node is isolated (on the un-imported track), so we snap
                    its coordinate to the nearest node that IS in the walkable
                    graph -- the platform surface (footway or platform area)
                    beside the track -- to route from. This is also the general
                    spatial index the DB otherwise lacked.

    .venv/bin/python core/build_platform_index.py            # build what's missing
    .venv/bin/python core/build_platform_index.py --rebuild  # TRUNCATE station_stops first

station_stops is a single set-based INSERT committed on its own; the osm_nodes
indexes are IF NOT EXISTS. Re-running is idempotent.
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

STATION_STOPS_DDL = """
CREATE TABLE IF NOT EXISTS station_stops (
    node_id BIGINT PRIMARY KEY,
    ref     TEXT NOT NULL,
    lat     DOUBLE PRECISION NOT NULL,
    lon     DOUBLE PRECISION NOT NULL
)
"""

# stop_position / railway=stop nodes carrying a track number (ref, else local_ref).
#
# RAIL ONLY. This table resolves a *rail* track number to a coordinate, but a
# bare stop_position ref is not mode-specific: two thirds of all such nodes in
# Europe are bus/tram stops (Verkehrsverbund bays lettered A, B, C ...), and a
# single-letter or bare-number ref on one of those collides with a rail track of
# the same label. Koblenz Hbf departs rail track "C" -> without this filter the
# nearest ref='C' stop was a bus stop 500 m away ("Hüberlingsweg"), and core/
# routed a bogus 2 km "platform transfer" to it instead of failing cleanly. Bus/
# tram platforms are deliberately out of scope (see core/PLATFORM-RESOLUTION.md,
# "Won't be fixed by either tier"), so we exclude any stop advertising a non-rail
# mode and no rail mode. COALESCE keeps every clause a real boolean, so an
# ambiguous stop with no mode tags at all is kept (conservative -- a bare
# stop_position with a numeric ref is almost always rail).
STATION_STOPS_INSERT = """
INSERT INTO station_stops (node_id, ref, lat, lon)
SELECT id, COALESCE(NULLIF(tags->>'ref', ''), tags->>'local_ref'), lat, lon
FROM osm_nodes
WHERE (tags->>'railway' = 'stop' OR tags->>'public_transport' = 'stop_position')
  AND COALESCE(NULLIF(tags->>'ref', ''), tags->>'local_ref') IS NOT NULL
  AND NOT (
    (COALESCE(tags->>'bus', '') = 'yes'
     OR COALESCE(tags->>'tram', '') = 'yes'
     OR COALESCE(tags->>'trolleybus', '') = 'yes'
     OR COALESCE(tags->>'ferry', '') = 'yes'
     OR COALESCE(tags->>'highway', '') = 'bus_stop')
    AND NOT (
      COALESCE(tags->>'railway', '') = 'stop'
      OR COALESCE(tags->>'train', '') = 'yes'
      OR COALESCE(tags->>'subway', '') = 'yes'
      OR COALESCE(tags->>'light_rail', '') = 'yes')
  )
ON CONFLICT (node_id) DO NOTHING
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_station_stops_lat ON station_stops (lat)",
    "CREATE INDEX IF NOT EXISTS idx_station_stops_lon ON station_stops (lon)",
    "CREATE INDEX IF NOT EXISTS idx_station_stops_ref ON station_stops (ref)",
    # general node coordinate index (used to snap a track's isolated stop node to
    # the nearest walkable node); large -- ~73M rows -- but built once.
    "CREATE INDEX IF NOT EXISTS idx_osm_nodes_lat ON osm_nodes (lat)",
    "CREATE INDEX IF NOT EXISTS idx_osm_nodes_lon ON osm_nodes (lon)",
)


def main() -> int:
    rebuild = "--rebuild" in sys.argv
    conn = connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(STATION_STOPS_DDL)
        conn.commit()
        if rebuild:
            cur.execute("TRUNCATE station_stops")
            conn.commit()
        cur.execute("SELECT count(*) AS n FROM station_stops")
        if cur.fetchone()["n"] and not rebuild:
            print("station_stops already populated (use --rebuild to redo)", flush=True)
        else:
            print("Populating station_stops ...", flush=True)
            t0 = time.monotonic()
            cur.execute(STATION_STOPS_INSERT)
            conn.commit()
            cur.execute("SELECT count(*) AS n FROM station_stops")
            print(f"  {cur.fetchone()['n']:,} rows in {time.monotonic() - t0:.1f}s", flush=True)

        print("Building indexes (osm_nodes coordinate index is large, ~minutes) ...", flush=True)
        for stmt in INDEXES:
            t0 = time.monotonic()
            cur.execute(stmt)
            conn.commit()
            print(f"  {stmt.split(' ON ')[0].split('EXISTS ')[-1]} in {time.monotonic() - t0:.1f}s", flush=True)
        cur.execute("ANALYZE station_stops")
        cur.execute("ANALYZE osm_nodes")
        conn.commit()
        print("Done.", flush=True)
        return 0
    except KeyboardInterrupt:
        conn.rollback()
        print("\nInterrupted -- station_stops (if committed) and any finished index are kept; re-run.", flush=True)
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
