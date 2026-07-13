"""
Tests for the transfer feasibility assessment (api/transfers.py).

  * Pure tests pin the verdict boundary and the layover math -- offline.
  * Mocked tests drive every branch of assess_transfer with the bridge and
    core/ stubbed, so each degradation path is covered without a DB.
  * DB-gated tests (TRANSFR_DB=1) run the full chain (resolve -> core/ ->
    classify) against a station we've verified end to end (Colmar).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import api.transfers as T  # noqa: E402
from api.bridge import StationMatch  # noqa: E402
from api.transfers import (  # noqa: E402
    FEASIBLE, INFEASIBLE, TIGHT, UNKNOWN,
    NO_PLATFORM_DATA, STATION_UNRESOLVED, CROSS_STATION,
    assess_transfer, classify, layover_seconds,
)

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB with station_points built; set TRANSFR_DB=1",
)


class _FakeConn:
    """cursor() context manager whose cursor is never really used (resolve_station
    is stubbed in these tests)."""

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def cursor(self):
        return self._Cur()


def _times(layover_s):
    return "2026-07-13T07:00:00Z", f"2026-07-13T07:{int(layover_s // 60):02d}:{int(layover_s % 60):02d}Z"


# ---------------------------------------------------------------------------
# Pure: layover + classify
# ---------------------------------------------------------------------------

def test_layover_seconds():
    assert layover_seconds("2026-07-13T07:00:00Z", "2026-07-13T07:05:00Z") == 300
    assert layover_seconds(None, "2026-07-13T07:05:00Z") is None
    assert layover_seconds("bad", "worse") is None


@pytest.mark.parametrize("walk, layover, expected", [
    (120, 300, FEASIBLE),      # plenty of time
    (250, 300, TIGHT),         # 300 < 250 + 60 buffer
    (300, 300, TIGHT),         # exactly enough to walk, none to spare
    (400, 300, INFEASIBLE),    # can't make it
    (None, 300, UNKNOWN),      # walk unknown
    (120, None, UNKNOWN),      # layover unknown
])
def test_classify(walk, layover, expected):
    assert classify(walk, layover, buffer_s=60) == expected


# ---------------------------------------------------------------------------
# assess_transfer branches (bridge + core/ mocked)
# ---------------------------------------------------------------------------

def _patch(monkeypatch, candidates, finder):
    monkeypatch.setattr(T, "resolve_station_candidates", candidates)
    monkeypatch.setattr(T, "find_shortest_path", finder)


def _base_kwargs(**over):
    kw = dict(
        arr_lat=50.1, arr_lon=8.6, arr_platform="7", arr_time="2026-07-13T07:00:00Z",
        dep_lat=50.1, dep_lon=8.6, dep_platform="9", dep_time="2026-07-13T07:05:00Z",
    )
    kw.update(over)
    return kw


def test_no_platform_data_short_circuits(monkeypatch):
    calls = []
    _patch(monkeypatch,
           lambda *a, **k: calls.append("resolve") or [],
           lambda *a, **k: calls.append("find"))
    a = assess_transfer(_FakeConn(), **_base_kwargs(arr_platform=None))
    assert a.verdict == UNKNOWN and a.reason == NO_PLATFORM_DATA
    assert calls == [], "must not touch the DB when a platform is missing"


def test_station_unresolved(monkeypatch):
    _patch(monkeypatch, lambda *a, **k: [], lambda *a, **k: pytest.fail("should not route"))
    a = assess_transfer(_FakeConn(), **_base_kwargs())
    assert a.verdict == UNKNOWN and a.reason == STATION_UNRESOLVED


def test_cross_station_when_stops_are_far_apart(monkeypatch):
    _patch(monkeypatch,
           lambda *a, **k: [StationMatch(111, "A-Bahnhof", 50.10, 8.60, 5.0)],
           lambda *a, **k: pytest.fail("must not route between two distinct stations"))
    # arrival and departure ~5 km apart -> not one station
    a = assess_transfer(_FakeConn(), **_base_kwargs(
        arr_lat=50.10, arr_lon=8.60, dep_lat=50.14, dep_lon=8.66))
    assert a.verdict == UNKNOWN and a.reason == CROSS_STATION
    assert a.relation_id == 111


@pytest.mark.parametrize("walk, layover_s, expected", [
    (120.0, 300, FEASIBLE),
    (280.0, 300, TIGHT),
    (500.0, 300, INFEASIBLE),
])
def test_found_path_classified_against_layover(monkeypatch, walk, layover_s, expected):
    same = [StationMatch(6365739, "Colmar", 48.07, 7.35, 5.0)]
    _patch(
        monkeypatch,
        lambda *a, **k: same,
        lambda *a, **k: {"found": True, "walking_time_seconds": walk, "walking_distance_meters": walk * 1.3},
    )
    arr_time, dep_time = _times(layover_s)
    a = assess_transfer(_FakeConn(), **_base_kwargs(arr_time=arr_time, dep_time=dep_time))
    assert a.verdict == expected
    assert a.walk_time_s == walk
    assert a.relation_id == 6365739 and a.station_name == "Colmar"


def test_routes_across_candidate_relations_until_one_contains_both(monkeypatch):
    """The split-station fix: the first candidate relation lacks the platforms,
    a second relation for the same physical station has them."""
    cands = [StationMatch(1, "wing A", 50.1, 8.6, 5.0), StationMatch(2, "wing B", 50.1, 8.6, 40.0)]

    def finder(conn, relation_id, a, b, **k):
        if relation_id == 2:
            return {"found": True, "walking_time_seconds": 90.0, "walking_distance_meters": 120.0}
        return {"found": False, "reason": "platform_not_found"}

    _patch(monkeypatch, lambda *a, **k: cands, finder)
    a = assess_transfer(_FakeConn(), **_base_kwargs(
        arr_time="2026-07-13T07:00:00Z", dep_time="2026-07-13T07:10:00Z"))
    assert a.verdict == FEASIBLE
    assert a.relation_id == 2 and a.station_name == "wing B"


def test_all_candidates_fail_surfaces_reason(monkeypatch):
    cands = [StationMatch(1, "S", 50.1, 8.6, 5.0), StationMatch(2, "S2", 50.1, 8.6, 40.0)]
    _patch(monkeypatch, lambda *a, **k: cands,
           lambda *a, **k: {"found": False, "reason": "platform_not_found"})
    a = assess_transfer(_FakeConn(), **_base_kwargs())
    assert a.verdict == UNKNOWN and a.reason == "platform_not_found"


# ---------------------------------------------------------------------------
# DB-gated end-to-end (real resolve -> core/ -> classify)
# ---------------------------------------------------------------------------

def _centroid(cur, relation_id):
    cur.execute("SELECT lat, lon FROM station_points WHERE relation_id = %s", (relation_id,))
    row = cur.fetchone()
    return (row["lat"], row["lon"]) if row else None


@DB
def test_end_to_end_feasible_and_infeasible_at_colmar():
    import db

    conn = db.connect(connect_timeout=5)
    colmar = _centroid(conn.cursor(), 6365739)  # Colmar, refs A/B, ~35s walk (verified)
    assert colmar, "Colmar (6365739) not in station_points"
    lat, lon = colmar

    common = dict(arr_lat=lat, arr_lon=lon, arr_platform="A",
                  dep_lat=lat, dep_lon=lon, dep_platform="B")

    feasible = assess_transfer(conn, arr_time="2026-07-13T07:00:00Z",
                               dep_time="2026-07-13T07:30:00Z", **common)
    assert feasible.relation_id == 6365739
    assert feasible.verdict == FEASIBLE
    assert feasible.walk_time_s and feasible.walk_time_s > 0

    infeasible = assess_transfer(conn, arr_time="2026-07-13T07:00:00Z",
                                 dep_time="2026-07-13T07:00:10Z", **common)  # 10 s layover
    assert infeasible.verdict == INFEASIBLE


@DB
def test_end_to_end_cross_station_detected():
    import db

    conn = db.connect(connect_timeout=5)
    cur = conn.cursor()
    colmar = _centroid(cur, 6365739)
    berlin = _centroid(cur, 5688517)  # a genuinely different station
    assert colmar and berlin
    a = assess_transfer(
        conn,
        arr_lat=colmar[0], arr_lon=colmar[1], arr_platform="A", arr_time="2026-07-13T07:00:00Z",
        dep_lat=berlin[0], dep_lon=berlin[1], dep_platform="1", dep_time="2026-07-13T09:00:00Z",
    )
    assert a.verdict == UNKNOWN and a.reason == CROSS_STATION


@DB
def test_end_to_end_split_relation_station_resolves_via_candidates():
    """Stuttgart Hbf p5->p12 was a false 'cross_station' when each platform was
    resolved to a single (different) relation; trying the candidate set finds the
    relation whose geometry holds both platforms and returns a real walk."""
    import db

    conn = db.connect(connect_timeout=5)
    lat, lon = 48.7843, 9.1819  # Stuttgart Hbf
    a = assess_transfer(
        conn,
        arr_lat=lat, arr_lon=lon, arr_platform="5", arr_time="2026-07-13T07:00:00Z",
        dep_lat=lat, dep_lon=lon, dep_platform="12", dep_time="2026-07-13T07:30:00Z",
    )
    assert a.verdict == FEASIBLE, f"got {a.verdict}/{a.reason}"
    assert a.walk_time_s and a.walk_time_s > 0


@DB
def test_end_to_end_area_tagged_platforms_resolve():
    """München Ost maps its platforms as public_transport=platform areas, not
    railway=platform_edge; the broadened matcher (Tier 1) now routes between
    them where core/ previously returned platform_not_found."""
    import db

    conn = db.connect(connect_timeout=5)
    lat, lon = 48.1280, 11.6039  # München Ost
    a = assess_transfer(
        conn,
        arr_lat=lat, arr_lon=lon, arr_platform="3", arr_time="2026-07-13T07:00:00Z",
        dep_lat=lat, dep_lon=lon, dep_platform="5", dep_time="2026-07-13T07:30:00Z",
    )
    assert a.verdict == FEASIBLE, f"got {a.verdict}/{a.reason}"
    assert a.walk_time_s and a.walk_time_s > 0
