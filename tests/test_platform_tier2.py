"""
Tests for Tier-2 platform resolution (core/search_context): resolving a track
number that OSM records only on a stop_position node, by snapping to the nearest
walkable way and routing from there. See core/PLATFORM-RESOLUTION.md.

  * A pure test pins the source/target anchoring rule (Tier 2 anchors to one node
    so an island platform doesn't collapse to zero distance).
  * DB-gated tests (TRANSFR_DB=1) prove real stations that were previously
    platform_not_found now route, with sensible distances, and that platform_edge
    stations are unchanged.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from search_context import SearchContext, _ANCHOR_KEY  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs transfr_eu with station_stops + osm_nodes coord index; set TRANSFR_DB=1",
)


# ---------------------------------------------------------------------------
# Pure: anchor-node source/target selection
# ---------------------------------------------------------------------------

def test_anchor_nodes_prefers_the_snap_anchor():
    ctx = SearchContext.__new__(SearchContext)  # bypass __init__; only coord_cache is used
    ctx.coord_cache = {10: (0, 0), 11: (0, 0), 12: (0, 0)}

    # Tier 2: a whole way is returned for geometry, but only the anchor is a source
    tier2 = [(999, [10, 11, 12], {"railway": "platform", _ANCHOR_KEY: 11})]
    assert ctx._anchor_nodes(tier2) == {11}

    # Tier 1 / platform_edge: no anchor -> every cached platform node is a source
    tier1 = [(1, [10, 11], {"railway": "platform_edge", "ref": "5"})]
    assert ctx._anchor_nodes(tier1) == {10, 11}

    # an anchor without a cached coord can't be used
    assert ctx._anchor_nodes([(9, [1, 2], {_ANCHOR_KEY: 99})]) == set()


# ---------------------------------------------------------------------------
# DB-gated: real Tier-2 stations
# ---------------------------------------------------------------------------

# (name, lat, lon, arr_track, dep_track) -- stations whose numeric tracks live
# only on stop_position nodes (platform ways use letters/none), previously
# platform_not_found. Expected distance range keeps the assertion meaningful
# without pinning an exact metre.
_TIER2 = [
    ("Basel SBB", 47.5474, 7.5895, "7", "8", 5, 120),
    ("Aarau", 47.3911, 8.0516, "4", "5", 5, 120),
    ("Aachen Hbf", 50.7678, 6.0914, "6", "9", 5, 250),
]


def _route(conn, lat, lon, a, b):
    import ground_truth as gt
    from api.bridge import resolve_station_candidates
    with conn.cursor() as cur:
        cands = resolve_station_candidates(cur, [(lat, lon)], 600, 5)
    last = {}
    for c in cands:
        last = gt.find_shortest_path(conn, c.relation_id, a, b, algorithm="astar")
        if last.get("found"):
            return last
    return last


@DB
@pytest.mark.parametrize("name,lat,lon,a,b,lo,hi", _TIER2, ids=[t[0] for t in _TIER2])
def test_tier2_station_resolves_with_sensible_distance(name, lat, lon, a, b, lo, hi):
    import db

    conn = db.connect(connect_timeout=5)
    r = _route(conn, lat, lon, a, b)
    assert r.get("found"), f"{name} {a}->{b} did not resolve: {r.get('reason')}"
    assert r["walking_time_seconds"] > 0
    assert lo <= r["walking_distance_meters"] <= hi, (
        f"{name} {a}->{b} = {r['walking_distance_meters']} m, expected {lo}-{hi} m"
    )


@DB
def test_station_stops_table_is_populated():
    import db

    conn = db.connect(connect_timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT count(*) AS n FROM station_stops")
    assert cur.fetchone()["n"] > 50_000, "station_stops looks unbuilt -- run core/build_platform_index.py"


@DB
@pytest.mark.parametrize("rel,a,b,expected", [
    (6365739, "A", "B", 34.8),   # Colmar -- letter platform_edges
    (5347313, "1", "3", 72.2),   # Strasbourg -- numeric platform_edges
])
def test_platform_edge_stations_unchanged_by_tier2(rel, a, b, expected):
    """Tier 2 is a last-resort fallback; platform_edge stations must resolve
    exactly as before (byte-for-byte walking time)."""
    import db
    import ground_truth as gt

    conn = db.connect(connect_timeout=5)
    r = gt.find_shortest_path(conn, rel, a, b, algorithm="astar")
    assert r.get("found")
    assert abs(r["walking_time_seconds"] - expected) < 0.5
