"""
Tests for the enrichment pipeline (api/pipeline.py).

  * Pure: the journey-level verdict rollup.
  * Offline: enrich() over REAL captured MOTIS fixtures with the transfer
    assessment stubbed (shape + verdict wiring), and enrich() over a
    platform-less FR/IT/ES fixture with the REAL assessor and no DB (proving
    graceful no_platform_data degradation).
  * DB-gated: enrich() over a DACH fixture against the real transfr_eu DB,
    proving the whole chain runs on real journey data.
"""

import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from api import journeys  # noqa: E402
import api.pipeline as P  # noqa: E402
import api.transfers as T  # noqa: E402
from api.pipeline import enrich, plan_journeys, rollup_verdict  # noqa: E402
from api.transfers import (  # noqa: E402
    FEASIBLE, INFEASIBLE, TIGHT, UNKNOWN, NO_PLATFORM_DATA,
    TransferAssessment, WalkResolution, platform_display,
)

FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "journeys")

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)

_ALLOWED_VERDICTS = {FEASIBLE, TIGHT, INFEASIBLE, UNKNOWN}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self._payload = payload

    def get(self, *a, **k):
        return _Resp(self._payload)


def _search_result(slug):
    """Run the real journeys.search_journeys over a captured fixture (net stubbed)."""
    with open(os.path.join(FIX_DIR, f"{slug}.json"), encoding="utf-8") as f:
        fx = json.load(f)
    original = journeys._get_session
    journeys._get_session = lambda: _Session(fx["response"])
    try:
        return journeys.search_journeys(
            fx["meta"]["origin_query"], fx["meta"]["destination_query"],
            datetime(2026, 7, 13, 9, 0),
        )
    finally:
        journeys._get_session = original


# ---------------------------------------------------------------------------
# rollup_verdict
# ---------------------------------------------------------------------------

def test_rollup_verdict():
    assert rollup_verdict([]) == FEASIBLE               # direct journey
    assert rollup_verdict([FEASIBLE, FEASIBLE]) == FEASIBLE
    assert rollup_verdict([FEASIBLE, TIGHT]) == TIGHT
    assert rollup_verdict([TIGHT, UNKNOWN]) == UNKNOWN   # unknown worse than tight
    assert rollup_verdict([UNKNOWN, INFEASIBLE]) == INFEASIBLE  # infeasible worst


# ---------------------------------------------------------------------------
# enrich() with the assessor stubbed
# ---------------------------------------------------------------------------

def test_enrich_shape_and_verdict_wiring(monkeypatch):
    monkeypatch.setattr(P, "assess_transfer", lambda *a, **k: TransferAssessment(
        verdict=FEASIBLE, walk_time_s=90.0, walk_distance_m=110.0, layover_s=300.0,
        relation_id=42, station_name="Somewhere", arrival_platform="3", departure_platform="7",
    ))
    resp = enrich(conn=None, search_result=_search_result("de_at_munchen_wien"))
    assert resp.origin.name and resp.destination.name
    assert resp.journeys
    for j in resp.journeys:
        assert j.verdict in _ALLOWED_VERDICTS
        assert len(j.transfers) == max(0, len([l for l in j.legs if l.mode != "walking"]) - 1)
        assert j.num_changes == len(j.transfers)
        if j.transfers:
            assert j.verdict == FEASIBLE          # every stubbed transfer is feasible
            for t in j.transfers:
                assert t.walk_time_s == 90.0 and t.verdict == FEASIBLE
        else:
            assert j.verdict == FEASIBLE          # direct -> feasible


def test_enrich_threads_avoid_elevators_to_every_assessment(monkeypatch):
    """#35: the "no elevators" routing profile must reach EVERY transfer's
    assessment, so a lift-free search's verdicts (not just its drawn geometry)
    are routed without lifts. Also guards enrich()'s positional hand-off to
    `_assess`: swapping the flag with the resolve cache there would otherwise
    fail silently, since both are truthy."""
    seen = []

    def spy(*a, **k):
        seen.append(k.get("avoid_elevators"))
        return TransferAssessment(verdict=FEASIBLE, walk_time_s=90.0, layover_s=300.0)

    monkeypatch.setattr(P, "assess_transfer", spy)
    enrich(conn=None, search_result=_search_result("de_at_munchen_wien"), avoid_elevators=True)
    assert seen and set(seen) == {True}
    seen.clear()
    enrich(conn=None, search_result=_search_result("de_at_munchen_wien"))
    assert seen and set(seen) == {False}      # default: lifts allowed, search unchanged


def test_enrich_platformless_network_degrades_gracefully_without_db():
    """FR/IT/ES fixtures carry no platforms; the real assessor must short-circuit
    to no_platform_data BEFORE any DB call, so enrich works with conn=None."""
    resp = enrich(conn=None, search_result=_search_result("es_barcelona_madrid"))
    assert resp.journeys
    saw_transfer = False
    for j in resp.journeys:
        for t in j.transfers:
            saw_transfer = True
            assert t.verdict == UNKNOWN
            assert t.reason == NO_PLATFORM_DATA
    assert saw_transfer, "expected at least one (platformless) interchange in this corpus"


def test_plan_journeys_wires_search_and_enrich(monkeypatch):
    monkeypatch.setattr(P, "search", lambda *a, **k: _search_result("de_frankfurt_koln"))
    monkeypatch.setattr(P, "assess_transfer", lambda *a, **k: TransferAssessment(
        verdict=TIGHT, walk_time_s=200.0, layover_s=240.0, relation_id=1, station_name="X",
        arrival_platform="a", departure_platform="b",
    ))
    resp = plan_journeys(None, "Frankfurt", "Köln", datetime(2026, 7, 13, 9, 0))
    assert resp.journeys
    assert any(j.transfers for j in resp.journeys)  # the 1-transfer itinerary


# ---------------------------------------------------------------------------
# Memoization: one change of train shared by several journeys is resolved once
# ---------------------------------------------------------------------------

def _station(lat, lon, name):
    return {"id": name, "name": name, "latitude": lat, "longitude": lon}


def _transit_leg(origin, destination, dep_plat, arr_plat, dep_time, arr_time):
    return {
        "mode": "train", "train_name": "ICE",
        "origin": origin, "destination": destination,
        "departure": dep_time, "arrival": arr_time,
        "departure_platform": dep_plat, "arrival_platform": arr_plat,
    }


def _journey_with_shared_change(dep_time, mid_arr, mid_dep):
    """Two-leg itinerary changing at the SAME station (Mannheim, p3->p5); only the
    layover (mid_arr/mid_dep) varies between journeys."""
    a = _station(49.4794, 8.4692, "Frankfurt")
    mid = _station(49.4795, 8.4699, "Mannheim")
    b = _station(48.7838, 9.1829, "Stuttgart")
    return {
        "id": f"j@{dep_time}", "date": dep_time, "duration_s": 3600, "num_changes": 1,
        "legs": [
            _transit_leg(a, mid, "7", "3", dep_time, mid_arr),
            _transit_leg(mid, b, "5", "11", mid_dep, "2026-07-13T10:00:00Z"),
        ],
    }


def test_enrich_memoizes_shared_change_across_journeys(monkeypatch):
    """The interchange (same station + platforms) recurs in both journeys; the
    walk is clock-independent, so resolve_walk must run ONCE and be reused, while
    each journey still classifies against its own layover."""
    calls = []

    def fake_resolve_walk(conn, **kw):
        calls.append((kw["arr_platform"], kw["dep_platform"]))
        return WalkResolution(walk_time_s=200.0, walk_distance_m=260.0, reason=None,
                              relation_id=1, station_name="Mannheim",
                              arrival_platform="3", departure_platform="5")

    monkeypatch.setattr(T, "resolve_walk", fake_resolve_walk)
    search_result = {
        "origin": _station(49.4794, 8.4692, "Frankfurt"),
        "destination": _station(48.7838, 9.1829, "Stuttgart"),
        "departure_time": "2026-07-13T09:00:00Z",
        "journeys": [
            # layover 600s -> feasible (200s walk + 60s buffer clears)
            _journey_with_shared_change("2026-07-13T09:00:00Z", "2026-07-13T09:20:00Z", "2026-07-13T09:30:00Z"),
            # layover 150s -> infeasible (under the 200s walk)
            _journey_with_shared_change("2026-07-13T09:05:00Z", "2026-07-13T09:25:30Z", "2026-07-13T09:28:00Z"),
        ],
    }
    resp = enrich(conn=None, search_result=search_result)
    assert [j.verdict for j in resp.journeys] == [FEASIBLE, INFEASIBLE]
    assert all(j.transfers[0].walk_time_s == 200.0 for j in resp.journeys)
    assert calls == [("3", "5")], "the shared change must be pathfound exactly once"


# ---------------------------------------------------------------------------
# Planned platform threading through enrich (scheduledTrack -> legs + transfers)
# ---------------------------------------------------------------------------

def _leg_planned(origin, destination, dep_plat, arr_plat, dep_time, arr_time,
                 planned_dep=None, planned_arr=None):
    return {
        "mode": "train", "train_name": "ICE",
        "origin": origin, "destination": destination,
        "departure": dep_time, "arrival": arr_time,
        "departure_platform": dep_plat, "arrival_platform": arr_plat,
        "planned_departure_platform": planned_dep, "planned_arrival_platform": planned_arr,
    }


def test_enrich_threads_planned_platforms_onto_legs_and_transfers(monkeypatch):
    """The scheduled platform (MOTIS scheduledTrack) must reach BOTH the legs and
    the transfers on the wire, so the client can render the planned/live/changed
    signal. Uses a stubbed walk (no DB): only the planned threading is under test."""
    def fake_resolve_walk(conn, *, arr_platform, dep_platform, **kw):
        from api.bridge import map_track_to_ref
        return WalkResolution(walk_time_s=120.0, walk_distance_m=150.0, reason=None,
                              relation_id=1, station_name="Mannheim Hbf",
                              arrival_platform=map_track_to_ref(arr_platform),
                              departure_platform=map_track_to_ref(dep_platform))
    monkeypatch.setattr(T, "resolve_walk", fake_resolve_walk)

    fra = _station(50.107, 8.663, "Frankfurt Hbf")
    man = _station(49.4795, 8.4699, "Mannheim Hbf")
    stu = _station(48.7838, 9.1829, "Stuttgart Hbf")
    search_result = {
        "origin": fra, "destination": stu, "departure_time": "2026-07-13T09:00:00Z",
        "journeys": [{
            "id": "j1", "date": "2026-07-13T09:00:00Z", "duration_s": 3600, "num_changes": 1,
            "legs": [
                # FRA dep: live == planned (a schedule guess). MA arr: live == planned.
                _leg_planned(fra, man, "7", "4", "2026-07-13T09:00:00Z", "2026-07-13T09:40:00Z",
                             planned_dep="7", planned_arr="4"),
                # MA dep: live 5 != planned 8 (a platform CHANGE). STR arr: no planned (confirmed live).
                _leg_planned(man, stu, "5", "16", "2026-07-13T09:50:00Z", "2026-07-13T10:30:00Z",
                             planned_dep="8", planned_arr=None),
            ],
        }],
    }
    resp = enrich(conn=None, search_result=search_result)
    j = resp.journeys[0]

    # Legs carry the scheduled platform straight through from the feed dict.
    assert j.legs[0].departure_platform == "7" and j.legs[0].planned_departure_platform == "7"
    assert j.legs[1].departure_platform == "5" and j.legs[1].planned_departure_platform == "8"
    assert j.legs[1].arrival_platform == "16" and j.legs[1].planned_arrival_platform is None

    # The Mannheim transfer: arrival is a guess (live==planned), departure a change.
    t = j.transfers[0]
    assert t.arrival_platform == "4" and t.planned_arrival_platform == "4"
    assert t.departure_platform == "5" and t.planned_departure_platform == "8"
    arr = platform_display(t.arrival_platform, t.planned_arrival_platform)
    dep = platform_display(t.departure_platform, t.planned_departure_platform)
    assert arr.state == "planned" and dep.state == "changed" and dep.changed_from == "8"


def test_pending_transfer_carries_planned_platform_without_db():
    """assess=false must still surface the scheduled platform (mapped, no DB), so the
    fast itinerary list can show the planned/changed signal before verdicts stream."""
    fra = _station(50.107, 8.663, "Frankfurt Hbf")
    man = _station(49.4795, 8.4699, "Mannheim Hbf")
    stu = _station(48.7838, 9.1829, "Stuttgart Hbf")
    search_result = {
        "origin": fra, "destination": stu, "departure_time": "2026-07-13T09:00:00Z",
        "journeys": [{
            "id": "j1", "date": "2026-07-13T09:00:00Z", "duration_s": 3600, "num_changes": 1,
            "legs": [
                _leg_planned(fra, man, "7", "Gl 4", "2026-07-13T09:00:00Z", "2026-07-13T09:40:00Z",
                             planned_dep="7", planned_arr="Gl 4"),
                _leg_planned(man, stu, "5", "16", "2026-07-13T09:50:00Z", "2026-07-13T10:30:00Z",
                             planned_dep="8", planned_arr=None),
            ],
        }],
    }
    resp = enrich(conn=None, search_result=search_result, assess=False)
    t = resp.journeys[0].transfers[0]
    assert t.verdict == "pending"
    # planned mapped the same way as live ("Gl 4" -> "4"); departure is a change.
    assert t.planned_arrival_platform == "4" and t.arrival_platform == "4"
    assert t.planned_departure_platform == "8" and t.departure_platform == "5"


# ---------------------------------------------------------------------------
# Progressive split: assess=false is pending + dbless; streamed == bundled
# ---------------------------------------------------------------------------

def test_enrich_assess_false_is_pending_without_db():
    """The fast path: assess=false returns every transfer `pending` with no walk
    and no DB touched (conn=None), so the itinerary list renders instantly."""
    resp = enrich(conn=None, search_result=_search_result("de_munchen_hamburg"), assess=False)
    assert resp.journeys
    saw = False
    for j in resp.journeys:
        for t in j.transfers:
            saw = True
            assert t.verdict == "pending"
            assert t.walk_time_s is None and t.walk_distance_m is None and t.relation_id is None
            # still carries what the client needs to render + then stream:
            assert t.layover_s is not None
        if j.transfers:
            assert j.verdict == "pending"
    assert saw, "expected at least one interchange in this DACH corpus"


def _interchange_reqs(journey_raw):
    from api.transitous import interchanges as _ich
    from api import schemas
    reqs = []
    for arrive, depart in _ich(journey_raw):
        arr, dep = arrive.get("destination") or {}, depart.get("origin") or {}
        reqs.append(schemas.AssessInterchange(
            at_station=arr.get("name"),
            arr_lat=arr.get("latitude"), arr_lon=arr.get("longitude"),
            arr_platform=arrive.get("arrival_platform"), arr_time=arrive.get("arrival"),
            dep_lat=dep.get("latitude"), dep_lon=dep.get("longitude"),
            dep_platform=depart.get("departure_platform"), dep_time=depart.get("departure"),
            planned_arr_platform=arrive.get("planned_arrival_platform"),
            planned_dep_platform=depart.get("planned_departure_platform"),
        ))
    return reqs


@DB
def test_streamed_assess_matches_bundled_enrich():
    """The progressive path must give the SAME verdicts as the bundled one:
    assess_interchanges over the client-built requests reproduces what
    enrich(assess=True) would have returned for each journey."""
    import db
    from api import schemas as S
    from api.pipeline import assess_interchanges

    conn = db.connect(connect_timeout=5)
    raw = _search_result("de_munchen_hamburg")
    bundled = enrich(conn, raw, assess=True)
    for jb in bundled.journeys:
        raw_j = next(x for x in raw["journeys"] if x["id"] == jb.id)
        streamed = assess_interchanges(conn, _interchange_reqs(raw_j)).transfers
        assert len(streamed) == len(jb.transfers)
        for a, b in zip(jb.transfers, streamed):
            assert (a.verdict, a.walk_time_s, a.walk_distance_m, a.relation_id, a.reason,
                    a.arrival_platform, a.departure_platform, a.layover_s,
                    a.planned_arrival_platform, a.planned_departure_platform) == \
                   (b.verdict, b.walk_time_s, b.walk_distance_m, b.relation_id, b.reason,
                    b.arrival_platform, b.departure_platform, b.layover_s,
                    b.planned_arrival_platform, b.planned_departure_platform)


# ---------------------------------------------------------------------------
# DB-gated: real enrichment over a DACH fixture
# ---------------------------------------------------------------------------

@DB
def test_enrich_real_dach_fixture_end_to_end():
    import db

    conn = db.connect(connect_timeout=5)
    resp = enrich(conn, _search_result("de_munchen_hamburg"))
    assert resp.journeys
    for j in resp.journeys:
        assert j.verdict in _ALLOWED_VERDICTS
        for t in j.transfers:
            assert t.verdict in _ALLOWED_VERDICTS
    # At least one interchange in this DACH journey must have resolved to a
    # station and been handed to core/ -- proving the bridge fires in-pipeline.
    assert any(
        t.relation_id is not None
        for j in resp.journeys for t in j.transfers
    ), "expected at least one interchange to resolve to an OSM station"
