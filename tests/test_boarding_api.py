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

from seat import PlatformGeometry, TrainFormation  # noqa: E402
from formation_model import NormalizedFormation  # noqa: E402
from live_sources import FormationUnavailable, parse_wagenreihung  # noqa: E402

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
# Coach naming: formation feed -> coach at the step-off point (offline)
# ---------------------------------------------------------------------------

def _wagenreihung_fixture():
    """A small but schema-faithful DB Wagenreihung payload (field names per
    live_sources.parse_wagenreihung): two sectors A/B with metre spans, one train
    portion of three passenger coaches with explicit metres, plus a power car that
    must be dropped."""
    return {
        "data": {
            "istformation": {
                "fahrtnummer": "573",
                "halt": {"bahnhofsname": "Musterstadt Hbf", "gleisbezeichnung": "7"},
                "allSektor": [
                    {"sektorbezeichnung": "A", "positionamgleis": {"startmeter": "0", "endemeter": "100"}},
                    {"sektorbezeichnung": "B", "positionamgleis": {"startmeter": "100", "endemeter": "200"}},
                ],
                "allFahrzeuggruppe": [{
                    "fahrzeuggruppebezeichnung": "ICE573",
                    "allFahrzeug": [
                        {"wagenordnungsnummer": "1", "kategorie": "REISEZUGWAGENERSTEKLASSE",
                         "fahrzeugsektor": "A", "positionamhalt": {"startmeter": "0", "endemeter": "26.4"}},
                        {"wagenordnungsnummer": "2", "kategorie": "REISEZUGWAGENZWEITEKLASSE",
                         "fahrzeugsektor": "A", "positionamhalt": {"startmeter": "26.4", "endemeter": "52.8"}},
                        {"wagenordnungsnummer": "3", "kategorie": "REISEZUGWAGENZWEITEKLASSE",
                         "fahrzeugsektor": "B", "positionamhalt": {"startmeter": "52.8", "endemeter": "79.2"}},
                        # A power car carries no reservable seats -> dropped by the parser.
                        {"wagenordnungsnummer": "0", "kategorie": "TRIEBKOPF",
                         "positionamhalt": {"startmeter": "79.2", "endemeter": "99.0"}},
                    ],
                }],
            }
        }
    }


def test_wagenreihung_parses_to_coach_at_offset():
    # The whole coach-naming chain end to end, no DB:
    #   payload -> parse_wagenreihung -> NormalizedFormation -> to_train_formation
    #           -> coach_at_offset(step-off metres) -> the coach to board.
    formation = parse_wagenreihung(_wagenreihung_fixture())
    assert isinstance(formation, NormalizedFormation)
    assert formation.source == "db-wagenreihung"
    assert formation.station == "Musterstadt Hbf" and formation.track == "7"
    assert formation.coach_ids() == ["1", "2", "3"]      # power car dropped
    assert formation.has_metres()

    tf = formation.to_train_formation(platform_length_m=200.0)
    assert tf.coach_span_m["3"] == pytest.approx((52.8, 79.2))
    # A step-off 60 m along the platform lands in coach 3 (52.8..79.2).
    assert tf.coach_at_offset(60.0) == "3"
    assert tf.coach_at_offset(40.0) == "2"
    assert tf.coach_at_offset(10.0) == "1"


# --- compute_boarding with an injected formation (SearchContext monkeypatched) --

class _FakeCtx:
    """Stands in for a resolved SearchContext: one straight arrival-platform edge,
    its node coordinates, and (deliberately) no departure targets so the fraction
    keeps the raw A-end offset -- exactly the frame coach spans live in."""

    def __init__(self, coords, edges, targets):
        self.error = None
        self.coord_cache = coords
        self.edges_1 = edges          # [(way_id, nodes, tags), ...]
        self.targets = targets


class _FakeCursorCM:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursorCM()


def _patch_edge(monkeypatch):
    """Wire compute_boarding onto a synthetic 100 m platform edge (nodes at
    0/25/50/75/100 m) and return (conn, stepoff_node) with the step-off at 50 m."""
    geom = _straight([0.0, 25.0, 50.0, 75.0, 100.0])
    ctx = _FakeCtx(dict(geom.coords), [(1, list(geom.nodes), {})], set())
    monkeypatch.setattr(boarding, "SearchContext", lambda cur, rid, a, d: ctx)
    return _FakeConn(), geom.nodes[2]                 # nodes[2] is the 50 m node


def test_compute_boarding_fills_coach_when_formation_injected(monkeypatch):
    conn, stepoff = _patch_edge(monkeypatch)
    formation = parse_wagenreihung(_wagenreihung_fixture())     # coaches 1..3 over 0..79.2 m
    g = compute_boarding(conn, relation_id=1, arr_ref="7", dep_ref="8",
                         stepoff_node=stepoff, formation=formation)
    assert g.has_position and g.platform_length_m == pytest.approx(100.0, abs=0.1)
    # Step-off 50 m -> coach 2 (26.4..52.8); the coach gap reason is cleared.
    assert g.coach == "2"
    assert g.formation_source == "db-wagenreihung"
    assert g.reason is None


def test_compute_boarding_accepts_a_formation_provider(monkeypatch):
    conn, stepoff = _patch_edge(monkeypatch)
    formation = parse_wagenreihung(_wagenreihung_fixture())
    calls = []

    def provider():
        calls.append(1)
        return formation

    g = compute_boarding(conn, relation_id=1, arr_ref="7", dep_ref="8",
                         stepoff_node=stepoff, formation_provider=provider)
    assert g.coach == "2" and g.formation_source == "db-wagenreihung"
    assert calls == [1]                              # provider invoked exactly once, lazily


def test_compute_boarding_no_formation_stays_position_only(monkeypatch):
    conn, stepoff = _patch_edge(monkeypatch)
    g = compute_boarding(conn, relation_id=1, arr_ref="7", dep_ref="8", stepoff_node=stepoff)
    assert g.has_position                            # the position half still works
    assert g.coach is None and g.formation_source is None
    assert g.reason == NO_FORMATION_FEED


def test_compute_boarding_swallows_unavailable_formation(monkeypatch):
    # A geo-blocked feed raises FormationUnavailable; boarding must degrade to
    # position-only, never propagate the failure.
    conn, stepoff = _patch_edge(monkeypatch)

    def blocked():
        raise FormationUnavailable("geo-blocked from this host")

    g = compute_boarding(conn, relation_id=1, arr_ref="7", dep_ref="8",
                         stepoff_node=stepoff, formation_provider=blocked)
    assert g.has_position
    assert g.coach is None and g.reason == NO_FORMATION_FEED


def test_compute_boarding_accepts_a_prebuilt_train_formation(monkeypatch):
    # A TrainFormation can be injected directly (bypassing the normalized model);
    # coach_at_offset drives the same result.
    conn, stepoff = _patch_edge(monkeypatch)
    tf = TrainFormation.uniform("local", num_coaches=4, coach_length_m=26.4)
    g = compute_boarding(conn, relation_id=1, arr_ref="7", dep_ref="8",
                         stepoff_node=stepoff, formation=tf)
    assert g.coach == "2" and g.reason is None       # 50 m -> coach 2 (26.4..52.8)


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
