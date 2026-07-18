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
    NO_PLATFORM_DATA, STATION_UNRESOLVED, CROSS_STATION, IMPLAUSIBLE_WALK,
    assess_transfer, classify, layover_seconds, walk_is_implausible,
    LiveTransfer, reassess,
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


def test_classify_buffer_shifts_the_boundary():
    """#36: the boarding buffer is what the client's setting feeds. The SAME walk
    and layover must classify differently as the buffer changes -- otherwise the
    setting would be inert. Walk 120s into a 200s layover: 80s of slack clears a
    30s buffer (feasible) but not a 90s one (tight)."""
    assert classify(120, 200, buffer_s=30) == FEASIBLE
    assert classify(120, 200, buffer_s=90) == TIGHT
    # A zero buffer only ever draws the infeasible line (layover < walk).
    assert classify(120, 120, buffer_s=0) == FEASIBLE
    assert classify(120, 119, buffer_s=0) == INFEASIBLE


@pytest.mark.parametrize("walk_m, gap_m, implausible", [
    (144.0, 0.0, False),      # Munchen Ost 3->5, the longest real transfer measured
    (650.0, 0.0, False),      # a long-but-real transfer stays under the 800 m cap
    (2032.0, 167.0, True),    # the Koblenz 9->C bug: 'C' resolved to a bus stop 500 m away
    (2032.0, 0.0, True),      # same walk, gap unknown -> still caught by the absolute cap
    (1200.0, 400.0, False),   # far-apart platforms (gap 400) allow a proportionally long walk
    (2100.0, 400.0, True),    # ... but not an arbitrarily long one (> 4*400 + 250)
    (None, 100.0, False),     # no walk -> nothing to reject
])
def test_walk_is_implausible(walk_m, gap_m, implausible):
    assert walk_is_implausible(walk_m, gap_m) is implausible


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


def _kw_layover(layover_s, **over):
    """_base_kwargs with the arr/dep times set to yield exactly `layover_s`."""
    arr_time, dep_time = _times(layover_s)
    return _base_kwargs(arr_time=arr_time, dep_time=dep_time, **over)


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


def test_implausible_walk_rejected_not_reported(monkeypatch):
    """A 'found' walk far longer than the platforms are apart (the Koblenz 9->C
    shape: ref 'C' resolves to a bus stop ~500 m away, routed as a 2 km walk) is
    a mis-resolution -- surfaced as `unknown`, never as a real walk."""
    same = [StationMatch(3267269, "Koblenz Hauptbahnhof", 50.35, 7.59, 5.0)]
    _patch(
        monkeypatch,
        lambda *a, **k: same,
        lambda *a, **k: {"found": True, "walking_time_seconds": 1501.4, "walking_distance_meters": 2032.0},
    )
    # arr/dep coords ~170 m apart -> a 2 km "transfer" cannot be real
    a = assess_transfer(_FakeConn(), **_base_kwargs(
        arr_lat=50.350906, arr_lon=7.588375, dep_lat=50.349762, dep_lon=7.589902))
    assert a.verdict == UNKNOWN and a.reason == IMPLAUSIBLE_WALK
    assert a.walk_time_s is None and a.walk_distance_m is None


def test_implausible_walk_does_not_block_a_later_real_candidate(monkeypatch):
    """One candidate relation resolving a bogus far-away platform must not hide a
    later candidate that routes a real, plausible transfer."""
    cands = [StationMatch(1, "bogus wing", 50.1, 8.6, 5.0),
             StationMatch(2, "real wing", 50.1, 8.6, 40.0)]

    def finder(conn, relation_id, a, b, **k):
        if relation_id == 1:
            return {"found": True, "walking_time_seconds": 1400.0, "walking_distance_meters": 1900.0}
        return {"found": True, "walking_time_seconds": 90.0, "walking_distance_meters": 120.0}

    _patch(monkeypatch, lambda *a, **k: cands, finder)
    a = assess_transfer(_FakeConn(), **_base_kwargs(
        arr_time="2026-07-13T07:00:00Z", dep_time="2026-07-13T07:10:00Z"))
    assert a.verdict == FEASIBLE
    assert a.relation_id == 2 and a.walk_distance_m == 120.0


# ---------------------------------------------------------------------------
# resolve_walk split + memoization (the clock-independent half is cacheable)
# ---------------------------------------------------------------------------

def test_resolve_walk_is_clock_independent(monkeypatch):
    """resolve_walk carries no layover/verdict -- only the station, refs and the
    walk (or a reason). It takes no *_time args, so it can't depend on them."""
    same = [StationMatch(6365739, "Colmar", 48.07, 7.35, 5.0)]
    _patch(monkeypatch, lambda *a, **k: same,
           lambda *a, **k: {"found": True, "walking_time_seconds": 90.0, "walking_distance_meters": 120.0})
    r = T.resolve_walk(_FakeConn(), arr_lat=50.1, arr_lon=8.6, arr_platform="7",
                       dep_lat=50.1, dep_lon=8.6, dep_platform="9")
    assert r.walk_time_s == 90.0 and r.walk_distance_m == 120.0 and r.reason is None
    assert r.relation_id == 6365739 and r.arrival_platform == "7" and r.departure_platform == "9"


def test_cache_reuses_walk_but_reclassifies_per_layover(monkeypatch):
    """Same change of train, two itineraries with different layovers: the walk is
    pathfound ONCE (cache hit), yet each call's verdict reflects its own layover."""
    finds = []
    _patch(monkeypatch, lambda *a, **k: [StationMatch(1, "X", 50.1, 8.6, 5.0)],
           lambda *a, **k: finds.append(1) or {"found": True, "walking_time_seconds": 200.0, "walking_distance_meters": 260.0})
    cache = {}
    # Layover 600s: 200s walk + 60s buffer comfortably clears -> feasible.
    a1 = assess_transfer(_FakeConn(), resolve_cache=cache, **_kw_layover(600))
    # Layover 210s: barely over the walk, inside the buffer -> tight.
    a2 = assess_transfer(_FakeConn(), resolve_cache=cache, **_kw_layover(210))
    # Layover 150s: under the walk -> infeasible.
    a3 = assess_transfer(_FakeConn(), resolve_cache=cache, **_kw_layover(150))
    assert (a1.verdict, a2.verdict, a3.verdict) == (FEASIBLE, TIGHT, INFEASIBLE)
    assert all(a.walk_time_s == 200.0 for a in (a1, a2, a3))
    assert len(finds) == 1, "the walk must be resolved once and reused from cache"
    assert len(cache) == 1


def test_cache_distinguishes_different_transfers(monkeypatch):
    """Different platforms => different cache key => a fresh resolve, not a stale hit."""
    _patch(monkeypatch, lambda *a, **k: [StationMatch(1, "X", 50.1, 8.6, 5.0)],
           lambda conn, rel, aref, bref, **k: {"found": True,
               "walking_time_seconds": 100.0 if bref == "9" else 40.0,
               "walking_distance_meters": 130.0 if bref == "9" else 50.0})
    cache = {}
    a = assess_transfer(_FakeConn(), resolve_cache=cache, **_kw_layover(600))
    b = assess_transfer(_FakeConn(), resolve_cache=cache, **_kw_layover(600, dep_platform="4"))
    assert a.walk_time_s == 100.0 and b.walk_time_s == 40.0
    assert len(cache) == 2


def test_cache_result_matches_uncached(monkeypatch):
    """Passing a cache never changes the assessment vs not passing one."""
    _patch(monkeypatch, lambda *a, **k: [StationMatch(7, "Y", 50.1, 8.6, 5.0)],
           lambda *a, **k: {"found": True, "walking_time_seconds": 75.0, "walking_distance_meters": 95.0})
    kw = _kw_layover(300)
    uncached = assess_transfer(_FakeConn(), **kw)
    cached = assess_transfer(_FakeConn(), resolve_cache={}, **kw)
    assert uncached == cached


# ---------------------------------------------------------------------------
# avoid_elevators: the "no elevators" JOURNEY routing profile (#35)
#
# core/ has always supported avoid_elevators (its --no-elevators profile: a lift
# is not traversable, route over stairs/escalators/ramps instead), and the /walk
# endpoint threads it for the drawn geometry -- but the verdict path
# (assess_transfer -> resolve_walk -> core/) did not, so a "no elevators"
# preference never changed the walkability verdict, only the picture. These lock
# the flag reaching core/ from the verdict path.
# ---------------------------------------------------------------------------

def test_avoid_elevators_is_forwarded_to_core_from_the_verdict_path(monkeypatch):
    """assess_transfer must pass avoid_elevators through to find_shortest_path so
    the verdict is routed without lifts. Fails before #35: the verdict pathfind
    ran with lifts regardless of the preference."""
    seen = {}

    def finder(conn, relation_id, a, b, **k):
        seen["avoid_elevators"] = k.get("avoid_elevators")
        return {"found": True, "walking_time_seconds": 90.0, "walking_distance_meters": 120.0}

    _patch(monkeypatch, lambda *a, **k: [StationMatch(1, "S", 50.1, 8.6, 5.0)], finder)
    assess_transfer(_FakeConn(), avoid_elevators=True, **_base_kwargs())
    assert seen["avoid_elevators"] is True
    seen.clear()
    assess_transfer(_FakeConn(), **_base_kwargs())  # default: elevators allowed
    assert seen["avoid_elevators"] is False


def test_cache_distinguishes_no_elevators_from_default(monkeypatch):
    """The lift-free route is a different walk, so it must not collide in the
    resolve cache with the with-lifts walk for the same platforms -- each profile
    pathfinds once and keeps its own time."""
    finds = []

    def finder(conn, rel, a, b, **k):
        avoid = k.get("avoid_elevators")
        finds.append(avoid)
        return {"found": True, "walking_time_seconds": 200.0 if avoid else 100.0,
                "walking_distance_meters": 260.0}

    _patch(monkeypatch, lambda *a, **k: [StationMatch(1, "X", 50.1, 8.6, 5.0)], finder)
    cache = {}
    default = assess_transfer(_FakeConn(), resolve_cache=cache, **_kw_layover(600))
    no_lifts = assess_transfer(_FakeConn(), resolve_cache=cache, avoid_elevators=True,
                               **_kw_layover(600))
    assert default.walk_time_s == 100.0 and no_lifts.walk_time_s == 200.0
    assert finds == [False, True], "each profile pathfinds once; no cross-profile cache hit"
    assert len(cache) == 2


# ---------------------------------------------------------------------------
# Live re-assessment under delay (pure -- cached walk, no DB/core/)
# ---------------------------------------------------------------------------

def _live(**over):
    kw = dict(relation_id=1, arr_ref="8", dep_ref="5", walk_time_s=71.0,
              scheduled_layover_s=420.0, motis_assumed_s=240.0, buffer_s=60.0)
    kw.update(over)
    return LiveTransfer(**kw)


def test_reassess_on_time_matches_classify():
    v = reassess(_live())
    assert v.verdict == FEASIBLE
    assert v.effective_layover_s == 420.0
    assert v.margin_s == 420.0 - 71.0
    assert v.absorb_s == 420.0 - 71.0 - 60.0
    assert v.rescued is False  # 420s layover, MOTIS is happy too


@pytest.mark.parametrize("inb, outb, verdict, rescued", [
    (0,   0,   FEASIBLE,   False),   # scheduled: plenty of time
    (200, 0,   FEASIBLE,   True),    # eff 220s; MOTIS(<240) drops, real 71s walk makes it
    (340, 0,   TIGHT,      True),    # eff 80s; still >= 71s walk, MOTIS long gone
    (360, 0,   INFEASIBLE, False),   # eff 60s < 71s walk: genuinely missed
    (320, 120, FEASIBLE,   True),    # outbound 2min late gives time back; eff 220s < 240s MOTIS
    (300, 120, FEASIBLE,   False),   # eff exactly 240s == MOTIS min: boundary, both keep it
])
def test_reassess_delay_sweep(inb, outb, verdict, rescued):
    v = reassess(_live(), inbound_delay_s=inb, outbound_delay_s=outb)
    assert v.verdict == verdict
    assert v.rescued is rescued
    assert v.effective_layover_s == 420.0 - inb + outb


def test_reassess_absorb_is_the_delay_budget():
    # margin over the raw walk, and how much MORE inbound delay you can still take
    v = reassess(_live(), inbound_delay_s=200)
    assert v.margin_s == 220.0 - 71.0        # 149s of physical slack left
    assert v.absorb_s == 220.0 - 71.0 - 60.0  # 89s more delay and still "feasible"


def test_reassess_unknown_when_walk_never_resolved():
    v = reassess(_live(walk_time_s=None))
    assert v.verdict == UNKNOWN
    assert v.margin_s is None and v.absorb_s is None


def test_reassess_no_rescue_flag_without_motis_baseline():
    # can't claim a rescue if we don't know MOTIS's bar
    v = reassess(_live(motis_assumed_s=None), inbound_delay_s=200)
    assert v.verdict == FEASIBLE and v.rescued is False


def test_reassess_platform_change_repathfinds_once(monkeypatch):
    calls = []

    def finder(conn, relation_id, a, b, **k):
        calls.append((relation_id, a, b))
        return {"found": True, "walking_time_seconds": 180.0, "walking_distance_meters": 250.0}

    monkeypatch.setattr(T, "find_shortest_path", finder)
    t = _live(scheduled_layover_s=300.0)
    v = reassess(t, dep_track_now="12", conn=object())
    assert v.replanned_walk is True
    assert calls == [(1, "8", "12")]        # re-routed to the new departure platform
    assert v.walk_time_s == 180.0 and t.dep_ref == "12"
    assert v.verdict == FEASIBLE            # 300s layover still clears the 180s walk


def test_reassess_same_platform_does_not_repathfind(monkeypatch):
    monkeypatch.setattr(T, "find_shortest_path", lambda *a, **k: pytest.fail("should not route"))
    v = reassess(_live(), arr_track_now="8", dep_track_now="5", conn=object())
    assert v.replanned_walk is False and v.walk_time_s == 71.0


def test_live_transfer_from_assessment_carries_avoid_elevators():
    """The plan-time -> live handoff must preserve the journey's routing profile so
    the live path re-routes under it, rather than silently regaining lifts."""
    a = T.TransferAssessment(verdict=FEASIBLE, walk_time_s=71.0, layover_s=420.0,
                             relation_id=1, arrival_platform="8", departure_platform="5")
    assert LiveTransfer.from_assessment(a, avoid_elevators=True).avoid_elevators is True
    assert LiveTransfer.from_assessment(a).avoid_elevators is False  # default


def test_reassess_platform_change_preserves_no_elevators_profile(monkeypatch):
    """A "no elevators" journey that gets re-tracked must re-route without lifts:
    the one live re-pathfind carries the transfer's avoid_elevators profile
    through."""
    seen = {}

    def finder(conn, relation_id, a, b, **k):
        seen["avoid_elevators"] = k.get("avoid_elevators")
        return {"found": True, "walking_time_seconds": 180.0, "walking_distance_meters": 250.0}

    monkeypatch.setattr(T, "find_shortest_path", finder)
    v = reassess(_live(scheduled_layover_s=300.0, avoid_elevators=True),
                 dep_track_now="12", conn=object())
    assert v.replanned_walk is True and seen["avoid_elevators"] is True


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


@DB
def test_end_to_end_koblenz_lettered_bus_platform_is_unknown_not_bogus_walk():
    """Regression: Koblenz Hbf rail track 9 -> "track C". 'C' is a *bus* bay on
    the forecourt, not a rail platform (rail tracks there are numbered). core/
    used to resolve 'C' to a same-lettered bus stop ~500 m away and route a
    2032 m / 1501 s "platform transfer" -- reported with reason=None as if real.

    Two independent fixes must keep this an honest `unknown`: rail-only
    station_stops (so 'C' finds no rail stop -> platform_not_found) and the
    walk-plausibility guard (so any bogus far-away resolution is rejected ->
    implausible_walk). Either way it must never come back as a valid ~1500 s
    walk. The two platform coordinates are the real MOTIS stops, ~170 m apart."""
    import db

    conn = db.connect(connect_timeout=5)
    a = assess_transfer(
        conn,
        arr_lat=50.350906, arr_lon=7.588375, arr_platform="9", arr_time="2026-07-14T08:00:00Z",
        dep_lat=50.349762, dep_lon=7.589902, dep_platform="C", dep_time="2026-07-14T08:10:00Z",
    )
    assert a.station_name and "Koblenz" in a.station_name, a.station_name
    assert a.verdict == UNKNOWN, f"expected unknown, got {a.verdict} ({a.walk_distance_m} m)"
    # Any honest not-found reason is fine (the bug report lists several); the one
    # that must never happen is a real-looking walk. Which fires depends on which
    # of the two fixes catches it first: rail-only station_stops -> platform_not_found;
    # the plausibility guard -> implausible_walk; a candidate whose search runs
    # long -> exceeded_plausibility_bound / disconnected.
    assert a.reason in (
        IMPLAUSIBLE_WALK, "platform_not_found", "exceeded_plausibility_bound", "disconnected",
    ), a.reason
    # The whole point: no spuriously-large "valid" walk leaks out.
    assert a.walk_time_s is None and a.walk_distance_m is None
