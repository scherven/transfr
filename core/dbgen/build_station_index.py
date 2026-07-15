#!/usr/bin/env python3
"""
Build (or extend) the station_points centroid table used by the API's
coordinate-based station bridge (api/bridge.py).

One row per stop_area / stop_area_group relation: the mean lat/lon of its
member geometry. This is what lets the API map a MOTIS journey stop -- which
carries lat/lon but a name that does NOT reliably match OSM's -- to the OSM
relation id core/'s find_shortest_path() needs.

    .venv/bin/python core/build_station_index.py            # fill in what's missing
    .venv/bin/python core/build_station_index.py --rebuild  # TRUNCATE first

Resumable + interrupt-safe (per this repo's long-process convention): commits
in batches, skips relations already present, and a Ctrl-C leaves a consistent,
partial table you can finish by re-running -- no progress is lost.
"""

import sys
import time
from typing import Dict, List, Optional, Set, Tuple

from psycopg2.extras import execute_values

# Reorg bootstrap: this script lives in core/dbgen/ but imports the engine by
# bare name (db/graph/...). Put core/ and its submodule dirs on sys.path so it
# runs both directly and as `python -m core.dbgen.<name>`.
import os as _os
_C = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _p in (_C, _os.path.join(_C, "pathfinding"), _os.path.join(_C, "dbgen"), _os.path.join(_C, "viz")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db import connect
from graph import resolve_relation_ways_and_nodes

BATCH = 1000

_DDL = """
CREATE TABLE IF NOT EXISTS station_points (
    relation_id BIGINT PRIMARY KEY,
    name        TEXT,
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    country     TEXT,
    n_members   INT NOT NULL
)
"""
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_station_points_lat ON station_points (lat)",
    "CREATE INDEX IF NOT EXISTS idx_station_points_lon ON station_points (lon)",
)

Point = Tuple[float, float]


def centroid(points: List[Point]) -> Optional[Point]:
    """Mean (lat, lon) of a set of points, or None if empty. Pure -- unit
    tested in tests/test_station_index.py. A station footprint is small enough
    that a plain arithmetic mean is a fine 'where is this station' anchor for
    nearest-neighbour resolution (we are not doing geodesy, just picking the
    closest station to a query coordinate)."""
    if not points:
        return None
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def _fetch_coords(cur, node_ids: Set[int]) -> Dict[int, Point]:
    if not node_ids:
        return {}
    cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (list(node_ids),))
    return {r["id"]: (r["lat"], r["lon"]) for r in cur.fetchall()}


def _fetch_way_nodes(cur, way_ids: Set[int]) -> Dict[int, List[int]]:
    if not way_ids:
        return {}
    cur.execute("SELECT id, nodes FROM osm_ways WHERE id = ANY(%s)", (list(way_ids),))
    return {r["id"]: r["nodes"] for r in cur.fetchall()}


def centroids_for_batch(cur, rels: List[Tuple[int, Optional[str], Optional[str]]]):
    """Compute (relation_id, name, lat, lon, country, n_members) rows for a
    batch of (relation_id, name, country) inputs.

    Prefers a relation's direct node members (stop_position / station nodes --
    the actual station points), and only expands its member ways' geometry for
    relations that have no usable direct-node coordinates. That keeps the common
    case to two bulk queries per batch instead of dragging every platform way's
    node list through for all ~333k relations.
    """
    per_rel: Dict[int, dict] = {}
    all_direct_nodes: Set[int] = set()
    for rid, name, country in rels:
        way_ids, node_ids = resolve_relation_ways_and_nodes(cur, rid)
        per_rel[rid] = {"name": name, "country": country, "ways": set(way_ids), "dnodes": set(node_ids)}
        all_direct_nodes |= set(node_ids)

    coords = _fetch_coords(cur, all_direct_nodes)

    points: Dict[int, List[Point]] = {}
    need_ways: Dict[int, Set[int]] = {}
    all_way_ids: Set[int] = set()
    for rid, d in per_rel.items():
        pts = [coords[n] for n in d["dnodes"] if n in coords]
        if pts:
            points[rid] = pts
        elif d["ways"]:
            need_ways[rid] = d["ways"]
            all_way_ids |= d["ways"]

    if all_way_ids:
        way_nodes = _fetch_way_nodes(cur, all_way_ids)
        leftover_nodes: Set[int] = set()
        for ways in need_ways.values():
            for w in ways:
                leftover_nodes.update(way_nodes.get(w, ()))
        coords2 = _fetch_coords(cur, leftover_nodes)
        for rid, ways in need_ways.items():
            pts = [coords2[n] for w in ways for n in way_nodes.get(w, ()) if n in coords2]
            if pts:
                points[rid] = pts

    rows = []
    for rid, pts in points.items():
        c = centroid(pts)
        if c is None:
            continue
        d = per_rel[rid]
        rows.append((rid, d["name"], c[0], c[1], d["country"], len(pts)))
    return rows


def main() -> int:
    rebuild = "--rebuild" in sys.argv
    conn = connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        for stmt in _INDEXES:
            cur.execute(stmt)
        conn.commit()
        if rebuild:
            print("Rebuilding: TRUNCATE station_points ...", flush=True)
            cur.execute("TRUNCATE station_points")
            conn.commit()

        cur.execute("SELECT relation_id FROM station_points")
        done = {r["relation_id"] for r in cur.fetchall()}
        cur.execute(
            "SELECT id, tags->>'name' AS name, tags->>'addr:country' AS country "
            "FROM osm_relations "
            "WHERE tags->>'public_transport' IN ('stop_area', 'stop_area_group') "
            "ORDER BY id"
        )
        todo = [(r["id"], r["name"], r["country"]) for r in cur.fetchall() if r["id"] not in done]
        total = len(todo)
        print(f"{len(done):,} already present; {total:,} to process.", flush=True)

        work = conn.cursor()  # separate cursor: resolve_relation_ways_and_nodes iterates its own results
        t0 = time.monotonic()
        inserted = 0
        for i in range(0, total, BATCH):
            batch = todo[i:i + BATCH]
            rows = centroids_for_batch(work, batch)
            if rows:
                execute_values(
                    work,
                    "INSERT INTO station_points (relation_id, name, lat, lon, country, n_members) "
                    "VALUES %s ON CONFLICT (relation_id) DO NOTHING",
                    rows,
                )
            conn.commit()  # checkpoint every batch -- interrupt-safe
            inserted += len(rows)
            processed = i + len(batch)
            rate = processed / max(time.monotonic() - t0, 1e-6)
            print(f"  {processed:,}/{total:,} processed, {inserted:,} inserted ({rate:.0f} rel/s)", flush=True)

        cur.execute("ANALYZE station_points")
        conn.commit()
        cur.execute("SELECT count(*) AS n FROM station_points")
        print(f"Done. station_points now has {cur.fetchone()['n']:,} rows.", flush=True)
        return 0
    except KeyboardInterrupt:
        conn.rollback()
        print("\nInterrupted -- committed batches are saved; re-run to resume.", flush=True)
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
