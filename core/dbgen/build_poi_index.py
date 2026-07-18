#!/usr/bin/env python3
"""
Load the facility POI layer into the `pois` Postgres table from a POI-tag-filtered
pbf, so the API can answer /facilities and /facility-map with a fast indexed bbox
SELECT instead of forking `osmium extract` against the full planet on every request
(which timed out for every station whose bbox wasn't already cached).

    core/dbgen/extract_pois.sh                                    # build the pbf (once, ~15 min)
    .venv/bin/python core/dbgen/build_poi_index.py core/data/europe-pois.pbf
    .venv/bin/python core/dbgen/build_poi_index.py core/data/europe-pois.pbf --rebuild

One row per facility POI (amenity / shop / tourism / office / leisure), as a point:
a POI node uses its own coordinate; a POI-tagged way (an area) uses its centroid.
Street-furniture amenities (benches, parking, vending machines, ...) are dropped --
the SAME `_keep_poi` filter and `POI_CATEGORIES` scope viz_export's details layer
used, imported here so the two can't drift. Buildings are not loaded: the facility
layer is points.

Interrupt-safe (this repo's long-process convention): commits in batches, and a
Ctrl-C leaves a consistent partial table you can finish by re-running with the same
pbf -- `ON CONFLICT (id) DO NOTHING` skips whatever already loaded. `id` is the OSM
id sign-encoded (node > 0, way < 0) so nodes and ways never collide on the key.
"""

import argparse
import sys
import time

import osmium
import psycopg2.extras

# Reorg bootstrap: this script lives in core/dbgen/ but imports the engine by bare
# name (db/viz_export). Put core/ and its submodule dirs on sys.path so it runs
# both directly and as `python -m core.dbgen.build_poi_index`.
import os as _os
_C = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _p in (_C, _os.path.join(_C, "pathfinding"), _os.path.join(_C, "dbgen"), _os.path.join(_C, "viz")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db import connect
# Reuse viz_export's exact POI scope + street-furniture filter so the DB layer and
# the (CLI-only) osmium details layer stay identical in what counts as a facility.
from viz_export import POI_CATEGORIES, _keep_poi

BATCH = 50_000

_DDL = """
CREATE TABLE IF NOT EXISTS pois (
    id       BIGINT PRIMARY KEY,
    category TEXT NOT NULL,
    subtype  TEXT,
    name     TEXT,
    level    TEXT,
    lat      DOUBLE PRECISION NOT NULL,
    lon      DOUBLE PRECISION NOT NULL
)
"""
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_pois_lat ON pois (lat)",
    "CREATE INDEX IF NOT EXISTS idx_pois_lon ON pois (lon)",
    "CREATE INDEX IF NOT EXISTS idx_pois_category ON pois (category)",
)
_INSERT = (
    "INSERT INTO pois (id, category, subtype, name, level, lat, lon) "
    "VALUES %s ON CONFLICT (id) DO NOTHING"
)


def _category(tags):
    """The first POI key present on an element (amenity/shop/...), or None."""
    return next((c for c in POI_CATEGORIES if c in tags), None)


class Loader(osmium.SimpleHandler):
    """Streams POI nodes + POI-area centroids into `pois`, committing per batch."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.cur = conn.cursor()
        self.buf = []
        self.n = 0
        self.t0 = time.monotonic()

    def _add(self, oid, cat, tags, lat, lon):
        self.buf.append((oid, cat, tags.get(cat), tags.get("name"), tags.get("level"), lat, lon))
        if len(self.buf) >= BATCH:
            self.flush()

    def flush(self):
        if not self.buf:
            return
        psycopg2.extras.execute_values(self.cur, _INSERT, self.buf, page_size=2000)
        self.conn.commit()   # checkpoint every batch -- interrupt-safe
        self.n += len(self.buf)
        self.buf = []
        rate = self.n / max(time.monotonic() - self.t0, 1e-6)
        print(f"  {self.n:,} POIs loaded ({rate:.0f}/s)", flush=True)

    def node(self, n):
        cat = _category(n.tags)
        if cat and _keep_poi(cat, n.tags.get(cat)) and n.location.valid():
            self._add(n.id, cat, n.tags, n.location.lat, n.location.lon)

    def way(self, w):
        if "building" in w.tags:      # the facility layer is points, not buildings
            return
        cat = _category(w.tags)
        if not (cat and _keep_poi(cat, w.tags.get(cat))):
            return
        pts = [(nd.location.lat, nd.location.lon) for nd in w.nodes if nd.location.valid()]
        if not pts:
            return
        clat = sum(p[0] for p in pts) / len(pts)
        clon = sum(p[1] for p in pts) / len(pts)
        self._add(-w.id, cat, w.tags, clat, clon)   # -id: way areas never collide with nodes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pbf_path", help="POI-tag-filtered pbf (see core/dbgen/extract_pois.sh)")
    ap.add_argument("--rebuild", action="store_true", help="TRUNCATE pois before loading")
    args = ap.parse_args()

    conn = connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        for stmt in _INDEXES:
            cur.execute(stmt)
        conn.commit()
        if args.rebuild:
            print("Rebuilding: TRUNCATE pois ...", flush=True)
            cur.execute("TRUNCATE pois")
            conn.commit()

        loader = Loader(conn)
        # locations=True so a POI-area way's member nodes carry coordinates (needed
        # for the centroid). pyosmium builds a node-location index over the file;
        # the POI pbf is small enough for the default index.
        loader.apply_file(args.pbf_path, locations=True)
        loader.flush()

        cur.execute("ANALYZE pois")
        conn.commit()
        cur.execute("SELECT count(*) AS n FROM pois")
        print(f"Done. pois now has {cur.fetchone()['n']:,} rows "
              f"({time.monotonic() - loader.t0:.0f}s).", flush=True)
        return 0
    except KeyboardInterrupt:
        conn.rollback()
        print("\nInterrupted -- committed batches are saved; re-run to resume.", flush=True)
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
