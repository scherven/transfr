"""
Tests for api/walks.py -- turning a transfer's (relation_id, from, to) into the
viz_export walk geometry.

  * Offline: build_walk's error handling, with `export` stubbed to succeed,
    raise SystemExit (the "no coordinates" data-gap case), or raise an arbitrary
    error (one bad key must not kill a batch).
  * DB-gated (TRANSFR_DB=1): the real chain against transfr_eu -- Berlin Hbf
    1->16 produces a found path, and the default walk time equals what the
    verdict path (find_shortest_path, same settings) would report.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import api.walks as walks  # noqa: E402
from api import config, schemas  # noqa: E402
from api.walks import NO_GEOMETRY, WALK_BUILD_FAILED, build_walk, build_walks  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)


def _key(relation_id=5688517, from_p="1", to_p="16", step_free=False, poi=None):
    return schemas.WalkKey(relation_id=relation_id, from_platform=from_p,
                           to_platform=to_p, step_free=step_free, poi=poi)


# ---------------------------------------------------------------------------
# Offline: error handling around the export() call
# ---------------------------------------------------------------------------

def test_build_walk_success_passes_export_through(monkeypatch):
    doc = {"meta": {"station_name": "S"}, "path": {"found": True, "walking_time_seconds": 100.0}}
    monkeypatch.setattr(walks, "export", lambda *a, **k: doc)
    r = build_walk(conn=None, key=_key())
    assert r.ok is True and r.reason is None
    assert r.export == doc
    assert (r.relation_id, r.from_platform, r.to_platform) == (5688517, "1", "16")


def test_build_walk_forwards_settings_matching_the_verdict(monkeypatch):
    captured = {}

    def _fake_export(conn, relation_id, ref_1, ref_2, **kw):
        captured.update(relation_id=relation_id, ref_1=ref_1, ref_2=ref_2, **kw)
        return {"path": {"found": True}}

    monkeypatch.setattr(walks, "export", _fake_export)
    build_walk(conn=None, key=_key(from_p="4", to_p="5", step_free=True))
    # Same algorithm/stitch as assess_transfer; details off; step_free -> avoid_elevators.
    assert captured["ref_1"] == "4" and captured["ref_2"] == "5"
    assert captured["algorithm"] == "astar"
    assert captured["stitch"] == config.STITCH_BRIDGES
    assert captured["details"] is False
    assert captured["avoid_elevators"] is True


def test_build_walk_without_poi_attaches_nothing(monkeypatch):
    captured = {}
    monkeypatch.setattr(walks, "export",
                        lambda *a, **k: captured.update(k) or {"path": {"found": True}})
    build_walk(conn=None, key=_key())
    assert captured["attach_pois"] is None       # a plain transfer walk carries no POI


def test_build_walk_forwards_the_chosen_facility_as_a_focus_poi(monkeypatch):
    captured = {}
    monkeypatch.setattr(walks, "export",
                        lambda *a, **k: captured.update(k) or {"path": {"found": True}})
    poi = schemas.WalkPOI(lat=52.5, lon=13.37, name="WC", category="amenity",
                          subtype="toilets", level="0")
    build_walk(conn=None, key=_key(poi=poi))
    # The 'walk to nearest' facility rides in as attach_pois, shaped for
    # viz_export.detail_entry (level -> level_raw), so it's projected + flagged focus.
    assert captured["attach_pois"] == [{
        "lat": 52.5, "lon": 13.37, "name": "WC",
        "category": "amenity", "subtype": "toilets", "level_raw": "0",
    }]


def test_build_walk_system_exit_becomes_no_geometry(monkeypatch):
    def _boom(*a, **k):
        raise SystemExit("no coordinates resolved -- is relation/ref correct?")

    monkeypatch.setattr(walks, "export", _boom)
    r = build_walk(conn=None, key=_key(relation_id=999999999))
    assert r.ok is False and r.reason == NO_GEOMETRY and r.export is None


def test_build_walk_arbitrary_error_becomes_build_failed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(walks, "export", _boom)
    r = build_walk(conn=None, key=_key())
    assert r.ok is False and r.reason == WALK_BUILD_FAILED


def test_build_walks_isolates_failures(monkeypatch):
    def _selective(conn, relation_id, *a, **k):
        if relation_id < 0:
            raise RuntimeError("bad")
        return {"path": {"found": True}}

    monkeypatch.setattr(walks, "export", _selective)
    resp = build_walks(conn=None, keys=[_key(relation_id=1), _key(relation_id=-1), _key(relation_id=2)])
    oks = [w.ok for w in resp.walks]
    assert oks == [True, False, True]
    assert resp.walks[1].reason == WALK_BUILD_FAILED


# ---------------------------------------------------------------------------
# DB-gated: the real geometry, and consistency with the verdict's walk time
# ---------------------------------------------------------------------------

@DB
def test_build_walk_real_berlin_matches_verdict_walk():
    import db
    from ground_truth import find_shortest_path

    conn = db.connect(connect_timeout=5)

    r = build_walk(conn, _key(relation_id=5688517, from_p="1", to_p="16"))
    assert r.ok is True
    assert r.export["path"]["found"] is True
    assert r.export["meta"]["station_name"] == "Berlin Hauptbahnhof"

    # The default walk must equal the verdict path: assess_transfer uses
    # find_shortest_path(algorithm="astar", stitch per config.STITCH_BRIDGES) and
    # build_walk feeds export() the same, so the two times can't disagree in the UI.
    gt = find_shortest_path(conn, 5688517, "1", "16", algorithm="astar",
                            use_stitch_bridges=config.STITCH_BRIDGES)
    assert r.export["path"]["walking_time_seconds"] == gt["walking_time_seconds"]

    # The step-free variant is a *different* route (routes around the elevator),
    # so it is allowed to differ -- just assert it still resolves.
    sf = build_walk(conn, _key(relation_id=5688517, from_p="1", to_p="16", step_free=True))
    assert sf.ok is True and sf.export["path"]["found"] is True


@DB
def test_build_walk_attaches_a_focus_poi_to_the_real_export():
    """The 'walk to nearest' door: a chosen facility, projected into the real
    Berlin export's details layer as the focus -- no planet extract needed. Its
    coordinate must land near the walk's geometry (a station-footprint POI), and
    it's the only detail on an otherwise details-free transfer walk."""
    import db

    conn = db.connect(connect_timeout=5)

    # A point inside Berlin Hbf (near the origin the export centres on).
    poi = schemas.WalkPOI(lat=52.5250, lon=13.3690, name="WC", category="amenity",
                          subtype="toilets", level="0")
    r = build_walk(conn, _key(relation_id=5688517, from_p="1", to_p="16", poi=poi))
    assert r.ok is True
    details = r.export["details"]
    assert len(details) == 1                       # exactly the one chosen facility
    d = details[0]
    assert d["focus"] is True and d["kind"] == "poi"
    assert d["subtype"] == "toilets" and d["name"] == "WC"
    assert "xyz" in d and len(d["xyz"]) == 3
    assert r.export["meta"]["has_details"] is True and r.export["meta"]["n_details"] == 1
    # The POI sits within the drawn scene's footprint, not off in null space.
    bbox = r.export["meta"]["bbox"]
    x, y, _ = d["xyz"]
    margin = 60.0
    assert bbox["min_x"] - margin <= x <= bbox["max_x"] + margin
    assert bbox["min_y"] - margin <= y <= bbox["max_y"] + margin
