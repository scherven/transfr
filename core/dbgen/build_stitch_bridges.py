#!/usr/bin/env python3
"""
Build `synthetic_bridges`: one-edge stitches that join a pedestrian connector
node lying INSIDE a platform polygon to that platform, for the case OSM mapped
the two overlapping but sharing no node id. Measured on real data: ~5% of ref'd
platform areas are this "stitchable" class (Colmar A->E is the canonical one --
its underpass footways/stairs/elevator END inside the platform polygon but share
no node, so the graph search can't step from one onto the other). See
core/PLATFORM-RESOLUTION.md and the disconnect diagnosis.

Why this can't invent a wrong transfer (the guardrails):
  * POINT-IN-POLYGON. node_a is geometrically inside the platform's own
    footprint, so there is no track between it and the platform -- you are
    standing on the platform. (An epsilon-*outside* snap could span a
    platform+track gap; we deliberately do NOT do that. This is why the 14.4 m
    "across a live track" gap at Seoul is never bridged.)
  * PEDESTRIAN CONNECTOR ONLY. node_a must belong to a footway / steps /
    corridor / pedestrian / elevator / conveying way -- never a platform or a
    rail way. So this never bridges platform<->platform (the across-the-tracks
    danger) and never bridges onto track.
  * LEVEL-COMPATIBLE. the platform's level set must intersect the connector's,
    so an underpass passing BENEATH a platform (level -1 under a level-0
    platform) is not bridged to it -- you still reach the platform via its
    stairs, which ARE level-compatible where they surface.

The bridge is OPT-IN at query time (SearchContext(use_stitch_bridges=True) /
find_shortest_path(use_stitch_bridges=True)); nothing routes differently until
asked, so existing behavior is byte-for-byte unchanged.

    PGDATABASE=transfr_kr python core/build_stitch_bridges.py            # all platforms
    PGDATABASE=transfr_kr python core/build_stitch_bridges.py --rebuild  # TRUNCATE first
    python core/build_stitch_bridges.py --bbox 48.070,7.343,48.076,7.350 # scope to a bbox

Resumable/interrupt-safe (repo convention): commits in batches, ON CONFLICT DO
NOTHING, so a Ctrl-C keeps every committed batch and re-running finishes the rest.
"""
import argparse
import sys
import time
from typing import List, Optional, Tuple

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
from graph import haversine_meters, parse_levels

# A pedestrian connector -- the walking network that SHOULD meet the platform.
# Never a platform (would allow platform<->platform) or a rail way.
CONNECTOR_SQL = (
    "(tags->>'highway' IN ('footway','steps','corridor','pedestrian','elevator') "
    "OR tags->>'railway' = 'elevator' OR tags ? 'conveying')"
)
PLATFORM_AREA_SQL = "(tags->>'railway' = 'platform' OR tags->>'public_transport' = 'platform')"

DDL = """
CREATE TABLE IF NOT EXISTS synthetic_bridges (
    node_a       BIGINT NOT NULL,   -- pedestrian connector node, INSIDE the platform
    node_b       BIGINT NOT NULL,   -- nearest node of the platform polygon
    dist_m       DOUBLE PRECISION NOT NULL,
    platform_way BIGINT NOT NULL,
    PRIMARY KEY (node_a, node_b)
)
"""
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_synthetic_bridges_a ON synthetic_bridges (node_a)",
    "CREATE INDEX IF NOT EXISTS idx_synthetic_bridges_b ON synthetic_bridges (node_b)",
)

INSERT = """
INSERT INTO synthetic_bridges (node_a, node_b, dist_m, platform_way) VALUES %s
ON CONFLICT (node_a, node_b) DO NOTHING
"""

BATCH = 500


def point_in_poly(lat: float, lon: float, poly: List[Tuple[float, float]]) -> bool:
    """Standard ray-cast point-in-polygon (lat as y, lon as x). `poly` is the
    ring of (lat, lon) vertices. Pure -- unit-tested."""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        (lai, loi), (laj, loj) = poly[i], poly[j]
        if (loi > lon) != (loj > lon) and lat < (laj - lai) * (lon - loi) / (loj - loi) + lai:
            inside = not inside
        j = i
    return inside


def levels_compatible(platform_level_raw: Optional[str], connector_level_raw: Optional[str]) -> bool:
    """True if the platform and connector share at least one level. Untagged ==
    ground (level 0), so two untagged features are compatible. A multi-level
    connector (stairs `-1;0`) is compatible with either level it reaches."""
    return bool(set(parse_levels(platform_level_raw)) & set(parse_levels(connector_level_raw)))


def bridges_for_platform(cur, way_id: int, nodes: List[int], tags: dict) -> List[Tuple[int, int, float, int]]:
    """Every (connector_node, nearest_platform_node, dist, platform_way) stitch
    for one platform-area polygon."""
    cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (nodes,))
    pc = {r["id"]: (r["lat"], r["lon"]) for r in cur.fetchall()}
    ring = [pc[n] for n in nodes if n in pc]
    if len(ring) < 3:
        return []
    pset = set(nodes)
    lats = [c[0] for c in ring]
    lons = [c[1] for c in ring]

    # Candidate nodes: inside the polygon bbox, not already part of the platform.
    cur.execute(
        "SELECT id, lat, lon FROM osm_nodes WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
        (min(lats), max(lats), min(lons), max(lons)),
    )
    cand = [(r["id"], r["lat"], r["lon"]) for r in cur.fetchall()
            if r["id"] not in pset and point_in_poly(r["lat"], r["lon"], ring)]
    if not cand:
        return []

    # Which candidates belong to a pedestrian connector way, and at which levels?
    cur.execute("SELECT node_id, way_ids FROM node_way_ids WHERE node_id = ANY(%s)", ([c[0] for c in cand],))
    nw = {r["node_id"]: r["way_ids"] for r in cur.fetchall()}
    all_way_ids = {w for ws in nw.values() for w in ws}
    connector_levels = {}  # way_id -> raw level string
    if all_way_ids:
        cur.execute(f"SELECT id, tags FROM osm_ways WHERE id = ANY(%s) AND {CONNECTOR_SQL}", (list(all_way_ids),))
        for r in cur.fetchall():
            connector_levels[r["id"]] = (r["tags"] or {}).get("level")

    out = []
    plat_level = tags.get("level")
    for nid, la, lo in cand:
        conn_ways = [w for w in nw.get(nid, ()) if w in connector_levels]
        if not conn_ways:
            continue  # not on the pedestrian network -> don't bridge
        if not any(levels_compatible(plat_level, connector_levels[w]) for w in conn_ways):
            continue  # passes over/under the platform, doesn't meet it
        # nearest platform-polygon node to anchor the stitch onto
        m, md = None, None
        for pn in nodes:
            if pn in pc:
                d = haversine_meters(la, lo, *pc[pn])
                if md is None or d < md:
                    m, md = pn, d
        if m is not None:
            out.append((nid, m, round(md, 2), way_id))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="TRUNCATE synthetic_bridges first")
    ap.add_argument("--bbox", default=None, help="scope to minlat,minlon,maxlat,maxlon")
    args = ap.parse_args()

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute(DDL)
        for stmt in INDEXES:
            cur.execute(stmt)
        conn.commit()
        if args.rebuild:
            cur.execute("TRUNCATE synthetic_bridges")
            conn.commit()

        where = PLATFORM_AREA_SQL + " AND (tags ? 'ref' OR tags ? 'local_ref')"
        params: tuple = ()
        if args.bbox:
            mnla, mnlo, mxla, mxlo = (float(x) for x in args.bbox.split(","))
            where += (" AND EXISTS (SELECT 1 FROM osm_nodes n WHERE n.id = ANY(osm_ways.nodes) "
                      "AND n.lat BETWEEN %s AND %s AND n.lon BETWEEN %s AND %s)")
            params = (mnla, mxla, mnlo, mxlo)
        cur.execute(f"SELECT id, nodes, tags FROM osm_ways WHERE {where} ORDER BY id", params)
        platforms = cur.fetchall()
        print(f"scanning {len(platforms):,} platform areas ...", flush=True)

        work = conn.cursor()
        buf: List[Tuple[int, int, float, int]] = []
        n_bridges = n_platforms_stitched = 0
        t0 = time.monotonic()
        for i, p in enumerate(platforms):
            rows = bridges_for_platform(work, p["id"], p["nodes"], p["tags"] or {})
            if rows:
                buf.extend(rows)
                n_bridges += len(rows)
                n_platforms_stitched += 1
            if len(buf) >= BATCH:
                execute_values(work, INSERT, buf)
                conn.commit()
                buf.clear()
            if (i + 1) % 1000 == 0:
                print(f"  {i+1:,}/{len(platforms):,}  ({n_bridges:,} bridges, "
                      f"{n_platforms_stitched:,} platforms) {time.monotonic()-t0:.0f}s", flush=True)
        if buf:
            execute_values(work, INSERT, buf)
            conn.commit()
        cur.execute("ANALYZE synthetic_bridges")
        conn.commit()
        cur.execute("SELECT count(*) AS n FROM synthetic_bridges")
        print(f"Done: {cur.fetchone()['n']:,} bridge rows total "
              f"({n_platforms_stitched:,} platforms stitched this run).", flush=True)
        return 0
    except KeyboardInterrupt:
        conn.rollback()
        print("\nInterrupted -- committed batches kept; re-run to finish.", flush=True)
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
