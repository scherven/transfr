#!/usr/bin/env python3
"""
Load a (pre-scoped) OSM pbf into the minimal schema in schema.sql.

Usage:
    python core/etl.py path/to/europe-railway-pedestrian.pbf

Progress handling
------------------
Rows are committed in batches (not all-at-once at the end), and every insert
is an upsert (ON CONFLICT DO UPDATE). That means:

  * A crash or Ctrl-C loses at most one in-flight batch, everything already
    committed stays committed.
  * On Ctrl-C we flush whatever is currently buffered before exiting, so the
    loss is bounded by "since the last flush", not "since the start".
  * Re-running the script from scratch is always safe: already-loaded rows
    are just re-upserted with identical data. We don't track a byte-offset
    checkpoint into the pbf because pyosmium's reader doesn't expose one;
    given the file is already Europe+tag scoped (a few GB at most, not the
    90GB planet file), a full re-scan is cheap enough that this is a
    reasonable place to stop rather than building chunked-file resumption.
"""

import argparse
import sys
import time

import osmium
import psycopg2.extras

# Reorg bootstrap: this script lives in core/dbgen/ but imports the engine by
# bare name (db/graph/...). Put core/ and its submodule dirs on sys.path so it
# runs both directly and as `python -m core.dbgen.<name>`.
import os as _os
_C = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _p in (_C, _os.path.join(_C, "pathfinding"), _os.path.join(_C, "dbgen"), _os.path.join(_C, "viz")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db import connect

NODE_BATCH = 50_000
WAY_BATCH = 20_000
REL_BATCH = 5_000

NODE_SQL = """
    INSERT INTO osm_nodes (id, lat, lon, tags) VALUES %s
    ON CONFLICT (id) DO UPDATE SET lat = EXCLUDED.lat, lon = EXCLUDED.lon, tags = EXCLUDED.tags
"""
WAY_SQL = """
    INSERT INTO osm_ways (id, nodes, tags) VALUES %s
    ON CONFLICT (id) DO UPDATE SET nodes = EXCLUDED.nodes, tags = EXCLUDED.tags
"""
REL_SQL = """
    INSERT INTO osm_relations (id, tags) VALUES %s
    ON CONFLICT (id) DO UPDATE SET tags = EXCLUDED.tags
"""
REL_MEMBER_SQL = """
    INSERT INTO osm_relation_members (relation_id, sequence, member_type, member_ref, member_role)
    VALUES %s
    ON CONFLICT (relation_id, sequence) DO UPDATE SET
        member_type = EXCLUDED.member_type,
        member_ref = EXCLUDED.member_ref,
        member_role = EXCLUDED.member_role
"""


def _tag_dict(tags) -> dict:
    return {t.k: t.v for t in tags}


class Loader(osmium.SimpleHandler):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.node_buf = []
        self.way_buf = []
        self.rel_buf = []
        self.rel_member_buf = []
        self.n_nodes = 0
        self.n_ways = 0
        self.n_rels = 0
        self.start = time.monotonic()

    def _progress(self, label: str) -> None:
        elapsed = time.monotonic() - self.start
        print(
            f"[{elapsed:8.1f}s] {label:9s} nodes={self.n_nodes:,} ways={self.n_ways:,} relations={self.n_rels:,}",
            flush=True,
        )

    def node(self, n):
        self.node_buf.append((n.id, n.location.lat, n.location.lon, psycopg2.extras.Json(_tag_dict(n.tags))))
        self.n_nodes += 1
        if len(self.node_buf) >= NODE_BATCH:
            self.flush_nodes()

    def way(self, w):
        node_ids = [nd.ref for nd in w.nodes]
        self.way_buf.append((w.id, node_ids, psycopg2.extras.Json(_tag_dict(w.tags))))
        self.n_ways += 1
        if len(self.way_buf) >= WAY_BATCH:
            self.flush_ways()

    def relation(self, r):
        self.rel_buf.append((r.id, psycopg2.extras.Json(_tag_dict(r.tags))))
        for seq, m in enumerate(r.members):
            self.rel_member_buf.append((r.id, seq, m.type.upper(), m.ref, m.role))
        self.n_rels += 1
        if len(self.rel_buf) >= REL_BATCH:
            self.flush_relations()

    def flush_nodes(self):
        if not self.node_buf:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, NODE_SQL, self.node_buf, page_size=2000)
        self.conn.commit()
        self.node_buf.clear()
        self._progress("nodes")

    def flush_ways(self):
        if not self.way_buf:
            return
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, WAY_SQL, self.way_buf, page_size=1000)
        self.conn.commit()
        self.way_buf.clear()
        self._progress("ways")

    def flush_relations(self):
        if not self.rel_buf and not self.rel_member_buf:
            return
        with self.conn.cursor() as cur:
            if self.rel_buf:
                psycopg2.extras.execute_values(cur, REL_SQL, self.rel_buf, page_size=500)
            if self.rel_member_buf:
                psycopg2.extras.execute_values(cur, REL_MEMBER_SQL, self.rel_member_buf, page_size=2000)
        self.conn.commit()
        self.rel_buf.clear()
        self.rel_member_buf.clear()
        self._progress("relations")

    def flush_all(self):
        self.flush_nodes()
        self.flush_ways()
        self.flush_relations()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf_path", help="Path to the scoped OSM pbf file")
    args = parser.parse_args()

    conn = connect()
    loader = Loader(conn)
    try:
        print(f"Loading {args.pbf_path} ...", flush=True)
        loader.apply_file(args.pbf_path)
        loader.flush_all()
        loader._progress("DONE")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted -- flushing buffered rows before exit ...", flush=True)
        loader.flush_all()
        print("Progress saved. Safe to re-run the same command to continue.", flush=True)
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
