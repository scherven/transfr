"""
Tests for synthetic stitch bridges (core/build_stitch_bridges.py +
SearchContext(use_stitch_bridges=True)).

A stitch joins a pedestrian connector node that lies INSIDE a platform polygon
to that platform, for the ~5% of platform areas OSM mapped overlapping but
sharing no node. Two layers:

  * Pure: the point-in-polygon and level-compatibility guardrails that decide
    whether a stitch is even allowed (no DB).
  * DB-gated (TRANSFR_DB=1): Colmar A->E -- disconnected by default, routes once
    stitching is enabled -- and the proof that an already-connected transfer
    (A->B) is byte-for-byte unchanged by turning stitching on.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from build_stitch_bridges import levels_compatible, point_in_poly  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs transfr_eu with synthetic_bridges built (core/build_stitch_bridges.py); set TRANSFR_DB=1",
)

# a unit square in (lat, lon), plus an L-shaped concave polygon
_SQUARE = [(0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0)]
_L = [(0.0, 0.0), (0.0, 3.0), (1.0, 3.0), (1.0, 1.0), (3.0, 1.0), (3.0, 0.0)]


# ---------------------------------------------------------------------------
# Pure: point-in-polygon
# ---------------------------------------------------------------------------

def test_point_in_poly_inside_and_outside():
    assert point_in_poly(1.0, 1.0, _SQUARE) is True
    assert point_in_poly(3.0, 3.0, _SQUARE) is False
    assert point_in_poly(1.0, 3.0, _SQUARE) is False  # outside to the side


def test_point_in_poly_concave_notch():
    # inside the foot of the L
    assert point_in_poly(0.5, 0.5, _L) is True
    assert point_in_poly(2.5, 0.5, _L) is True
    # in the notch that was cut out -> outside
    assert point_in_poly(2.5, 2.5, _L) is False


# ---------------------------------------------------------------------------
# Pure: level compatibility guardrail
# ---------------------------------------------------------------------------

def test_levels_untagged_are_ground_and_compatible():
    assert levels_compatible(None, None) is True
    assert levels_compatible("0", None) is True


def test_levels_disjoint_are_incompatible():
    # a level-0 platform must NOT stitch to a footway passing under it at -1
    assert levels_compatible("0", "-1") is False
    assert levels_compatible("1", None) is False  # None == ground 0


def test_multilevel_connector_compatible_where_it_reaches():
    # stairs level=-1;0 reach the level-0 platform
    assert levels_compatible("0", "-1;0") is True
    assert levels_compatible("-1", "-1;0") is True
    assert levels_compatible("1", "-1;0") is False


# ---------------------------------------------------------------------------
# DB-gated: real stitched station (Colmar) + the no-regression proof
# ---------------------------------------------------------------------------

_COLMAR = 6365739
_COLMAR_PLATFORM_DE_WAY = 53506915


def _require_colmar_bridges(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('synthetic_bridges') AS t")
        if cur.fetchone()["t"] is None:
            pytest.skip("synthetic_bridges table absent; run core/build_stitch_bridges.py")
        cur.execute("SELECT count(*) AS n FROM synthetic_bridges WHERE platform_way = %s",
                    (_COLMAR_PLATFORM_DE_WAY,))
        if cur.fetchone()["n"] == 0:
            pytest.skip("no Colmar bridges; run "
                        "core/build_stitch_bridges.py --bbox 48.070,7.343,48.077,7.350")


@DB
def test_colmar_ae_disconnected_until_stitched():
    import db
    import ground_truth as gt

    conn = db.connect(connect_timeout=5)
    try:
        _require_colmar_bridges(conn)
        base = gt.find_shortest_path(conn, _COLMAR, "A", "E", algorithm="astar")
        assert not base.get("found")
        assert base.get("reason") == "disconnected"

        stitched = gt.find_shortest_path(conn, _COLMAR, "A", "E", algorithm="astar",
                                         use_stitch_bridges=True)
        assert stitched.get("found"), f"stitch did not connect A->E: {stitched.get('reason')}"
        assert stitched["walking_time_seconds"] > 0
        # a real cross-platform walk via the underpass, not a hike or a wall-jump
        assert 50 < stitched["walking_distance_meters"] < 300, stitched["walking_distance_meters"]
    finally:
        conn.close()


@DB
def test_stitch_leaves_connected_transfer_byte_for_byte():
    """A->B at Colmar already routes (34.8 s). Enabling stitching only ADDS
    edges, so an already-optimal path must be completely unchanged."""
    import db
    import ground_truth as gt

    conn = db.connect(connect_timeout=5)
    try:
        base = gt.find_shortest_path(conn, _COLMAR, "A", "B", algorithm="astar")
        stitched = gt.find_shortest_path(conn, _COLMAR, "A", "B", algorithm="astar",
                                         use_stitch_bridges=True)
        assert base.get("found") and stitched.get("found")
        assert base["walking_time_seconds"] == stitched["walking_time_seconds"]
        assert base["walking_distance_meters"] == stitched["walking_distance_meters"]
        assert base["node_path"] == stitched["node_path"]
    finally:
        conn.close()
