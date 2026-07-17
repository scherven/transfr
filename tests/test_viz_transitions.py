"""Regression tests for phantom vertical-transition over-detection in the 3D
viz export (core/viz/viz_export.py), against real transfr_eu data.

Two real transfers used to render as long strings of up/down "transitions" even
though the walk is essentially flat or has a single real level change per
elevator -- "looking like you take ~7 elevators":

  * Stuttgart Hbf 4->16 -- a flat walk along the level=1 "Neuer Querbahnsteig"
    concourse AREA (way 230821002). Empty-tagged 3-node ways trace stretches of
    that area's boundary; because way_for_hop picked whichever way the set
    yielded first, some hops took their height from a stub (no level -> ground)
    and the path yo-yoed L1<->L0, firing SEVEN phantom "vertical" transitions.
    The routing itself was always correct (86.1 s == 120.6 m / 1.4 m/s).

  * Karlsruhe Hbf 3->12 -- platform (L1) down into a room=elevator polygon
    (way 270880470, level=0;1), along the L0 underpass, up a building=elevator
    polygon to the far platform (L1). Two elevators = two real level changes,
    but interpolating the multi-level room polygon end-to-end around its
    perimeter fabricated SIX extra "other" transitions on top of them (NINE
    total).

The fix is entirely in the viz export (way_for_hop preference + flattening
multi-level areas); routing (walking_time/distance/node_path) is unchanged, so
this does not touch the ground-truth suite. See md/ISSUE-viz-phantom-transitions.md.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

import viz_export as vx  # noqa: E402
from db import connect  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    c = connect()
    yield c
    c.close()


def _export(conn, relation_id, ref1, ref2):
    data = vx.export(conn, relation_id, ref1, ref2)
    assert data["path"]["found"], f"{relation_id} {ref1}->{ref2}: expected a path, got {data['path'].get('reason')}"
    return data["path"]


def _summary(transitions):
    return [(t.get("kind"), t.get("node_id", t.get("way_id"))) for t in transitions]


def test_stuttgart_4_16_does_not_oscillate(conn):
    path = _export(conn, 215808, "4", "16")
    transitions = path["transitions"]
    # A flat level-1 concourse walk: there is NO real level change, so there must
    # be zero transitions (was 7 phantom "vertical" ones).
    assert transitions == [], (
        f"Stuttgart 4->16 is a flat walk but rendered {len(transitions)} "
        f"transitions: {_summary(transitions)}"
    )
    # Routing must be untouched: pure-horizontal walking time.
    assert path["walking_time_seconds"] == pytest.approx(path["walking_distance_meters"] / 1.4, abs=1.0)


def test_karlsruhe_3_12_shows_only_real_level_changes(conn):
    path = _export(conn, 140179, "3", "12")
    transitions = path["transitions"]
    # The real structure is two elevators (down to the underpass, up to the far
    # platform). At most those two may show; more means the room-polygon
    # interpolation is fabricating transitions again (was 9).
    assert len(transitions) <= 2, (
        f"Karlsruhe 3->12 should show at most its two real level changes but "
        f"rendered {len(transitions)}: {_summary(transitions)}"
    )
    # ...and it must still genuinely cross between levels (not be flattened away).
    assert transitions, "Karlsruhe 3->12 lost its real level change entirely"
    zs = [z for t in transitions for z in (t["from"][2], t["to"][2])]
    assert max(zs) - min(zs) > 0.01


def test_berlin_1_16_keeps_its_real_transitions(conn):
    # Guardrail: the fix must not flatten a genuinely multi-level transfer.
    # Berlin Hbf 1->16 legitimately changes level via a mapped escalator way and
    # a node-mapped elevator; both transitions must survive, with real kinds.
    path = _export(conn, 5688517, "1", "16")
    transitions = path["transitions"]
    assert len(transitions) >= 2, (
        f"Berlin 1->16 must keep its real escalator+elevator transitions, got {_summary(transitions)}"
    )
    kinds = {t["kind"] for t in transitions}
    assert kinds & {"escalator", "elevator", "stairs"}, f"expected a real mechanism kind, got {kinds}"
