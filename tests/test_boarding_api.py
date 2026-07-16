"""
Tests for api/boarding.py -- turning a resolved step-off node into where-to-stand
guidance.

  * Offline: the pure geometry (offset along an edge, orientation toward the
    departure end, significance banding, end-to-end guidance from a synthetic
    platform edge) with no DB.
  * DB-gated (TRANSFR_DB=1): the real chain -- Berlin Hbf produces a fraction on
    a genuine 430 m platform, and same-island platforms 1->2 still get non-trivial
    guidance because the cross-over point is at one end.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "pathfinding"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "boarding"))

from seat import PlatformGeometry  # noqa: E402

from api import boarding  # noqa: E402
from api.boarding import (  # noqa: E402
    NO_FORMATION_FEED, PLATFORM_GEOMETRY_UNAVAILABLE,
    SIG_HIGH, SIG_LOW, SIG_SOME,
    classify_significance, compute_boarding, guidance_from_edge,
    offset_along_edge, stepoff_node_of,
)

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)

# WALKING_SPEED_MS, mirrored so the expected seconds are explicit here.
from graph import WALKING_SPEED_MS  # noqa: E402


# ---------------------------------------------------------------------------
# Pure geometry
# ---------------------------------------------------------------------------

def _straight(offsets_m):
    """A straight, due-east platform edge whose haversine offsets reproduce
    `offsets_m` exactly (see PlatformGeometry.straight_line)."""
    return PlatformGeometry.straight_line(48.0, 9.0, offsets_m)


def test_offset_along_edge_reproduces_metre_offsets():
    geom = _straight([0.0, 50.0, 120.0, 300.0])
    for node, want in zip(geom.nodes, [0.0, 50.0, 120.0, 300.0]):
        off, length = offset_along_edge(geom.nodes, geom.coords, node)
        assert off == pytest.approx(want, abs=1e-3)
        assert length == pytest.approx(300.0, abs=1e-3)


def test_offset_along_edge_rejects_off_edge_and_degenerate():
    geom = _straight([0.0, 100.0])
    assert offset_along_edge(geom.nodes, geom.coords, 99999) is None      # not on edge
    assert offset_along_edge([geom.nodes[0]], geom.coords, geom.nodes[0]) is None  # single node


def test_classify_significance_bands():
    assert classify_significance(200.0) == SIG_HIGH
    assert classify_significance(40.0) == SIG_HIGH     # boundary inclusive
    assert classify_significance(39.9) == SIG_SOME
    assert classify_significance(12.0) == SIG_SOME
    assert classify_significance(5.0) == SIG_LOW


def test_guidance_orients_fraction_toward_departure():
    # 300 m platform; step off 60 m from node[0]. Departure anchor sits far beyond
    # node[0]'s end, so that end is "toward the connection" and the fraction must
    # flip: oriented offset = 300 - 60 = 240, fraction 0.8.
    geom = _straight([0.0, 60.0, 300.0])
    stepoff = geom.nodes[1]                       # the 60 m node
    # Anchor well to the west of node[0] (offsets increase due east).
    dep_anchor = (48.0, 8.9)
    g = guidance_from_edge("7", "8", geom.nodes, geom.coords, stepoff, dep_anchor)
    assert g is not None
    assert g.platform_length_m == pytest.approx(300.0, abs=0.1)
    assert g.stepoff_offset_m == pytest.approx(240.0, abs=0.1)
    assert g.stepoff_fraction == pytest.approx(0.8, abs=0.01)
    # Worst end is now 240 m away -> its walk penalty.
    assert g.time_saved_s == pytest.approx(240.0 / WALKING_SPEED_MS, abs=0.5)
    assert g.significance == SIG_HIGH
    # Position is known; the coach still needs a live formation feed.
    assert g.coach is None and g.reason == NO_FORMATION_FEED


def test_guidance_without_anchor_keeps_raw_offset():
    geom = _straight([0.0, 60.0, 300.0])
    g = guidance_from_edge("7", "8", geom.nodes, geom.coords, geom.nodes[1], None)
    assert g.stepoff_offset_m == pytest.approx(60.0, abs=0.1)
    # Worst end is the far one, 240 m away.
    assert g.time_saved_s == pytest.approx(240.0 / WALKING_SPEED_MS, abs=0.5)


def test_stepoff_node_of_reads_first_path_node():
    assert stepoff_node_of({"path": {"found": True, "node_ids": [42, 43, 44]}}) == 42
    assert stepoff_node_of({"path": {"found": False, "node_ids": [42]}}) is None
    assert stepoff_node_of({"path": {"found": True, "node_ids": []}}) is None
    assert stepoff_node_of({}) is None


def test_compute_boarding_none_node_is_coarse():
    # No step-off node -> position-less guidance, no DB touched (conn unused).
    g = compute_boarding(conn=None, relation_id=1, arr_ref="1", dep_ref="2", stepoff_node=None)
    assert not g.has_position
    assert g.reason == PLATFORM_GEOMETRY_UNAVAILABLE
    assert g.coach is None


# ---------------------------------------------------------------------------
# DB-gated: the real geometry
# ---------------------------------------------------------------------------

@DB
def test_real_berlin_boarding_has_position_and_no_coach():
    import db
    from api.walks import build_walk
    from api import schemas

    conn = db.connect(connect_timeout=5)

    r = build_walk(conn, schemas.WalkKey(relation_id=5688517, from_platform="1", to_platform="16"))
    assert r.ok and r.boarding is not None
    b = r.boarding
    # A real Berlin mainline platform is ~430 m; the step-off sits partway along.
    assert b.platform_length_m > 300
    assert 0.0 < b.stepoff_fraction < 1.0
    assert b.time_saved_s > 40 and b.significance == SIG_HIGH
    # Formation is geo-blocked from a generic host -> position, but no coach.
    assert b.coach is None and b.reason == NO_FORMATION_FEED


@DB
def test_real_same_island_still_guides_to_the_crossover_end():
    import db
    from api.walks import build_walk
    from api import schemas

    conn = db.connect(connect_timeout=5)
    r = build_walk(conn, schemas.WalkKey(relation_id=5688517, from_platform="1", to_platform="2"))
    assert r.ok and r.boarding is not None
    # Platforms 1 & 2 share an island, but the cross-over is at one end, so the
    # optimal step-off is toward that end (a high fraction), not "anywhere".
    assert r.boarding.stepoff_fraction > 0.5
