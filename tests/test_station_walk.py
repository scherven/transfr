"""
Tests for the 'full station walk' tool -- from one source platform, the walk to
every other platform at a station (api/station_walk.py + GET /station-walk).

  * Offline: build_station_walk's shaping with the primitives stubbed --
    skip-the-source, honest per-row degradation, platform-ref ordering, and the
    station-unresolved top-level failure. No DB.
  * Route-shape (TestClient): the HTTP contract -- routing, query validation,
    response shape -- with the builder stubbed (mirrors tests/test_api.py's
    /walk tests).
  * DB-gated (TRANSFR_DB=1): the real chain against transfr_eu -- the Berlin Hbf
    coordinate reaches several platforms with sane times, in platform-ref order.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
import api.station_walk as station_walk  # noqa: E402
from api import schemas  # noqa: E402
from api.bridge import StationMatch  # noqa: E402
from api.station_walk import build_station_walk  # noqa: E402
from api.transfers import STATION_UNRESOLVED  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)

# The prototype's canonical station.
BERLIN_HBF = (52.5251, 13.3694)


class _FakeConn:
    """A conn whose cursor is a no-op context manager -- enough for the offline
    builder tests, where resolve_station / list_platform_refs / find_shortest_path
    are all stubbed and never touch it."""

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def cursor(self):
        return self._Cur()


# ---------------------------------------------------------------------------
# Offline: build_station_walk shaping (primitives stubbed)
# ---------------------------------------------------------------------------

class _NoOverlay:
    """Neutralises the harvested platform overlay.

    build_station_walk folds overlay tracks in alongside the OSM refs (so the tool
    covers the platforms OSM doesn't label). These offline tests use REAL station
    coordinates, so without this they'd pull in that station's real overlay and stop
    isolating the ref-list -> rows shaping they exist to check. The union itself is
    covered by test_overlay_tracks_are_offered_alongside_osm_refs."""

    @staticmethod
    def platform_markers(lat, lon, *a, **k):
        return None

    @staticmethod
    def track_coord(lat, lon, track, *a, **k):
        return None


def _stub_station(monkeypatch, relation_id=5688517, name="Berlin Hauptbahnhof"):
    monkeypatch.setattr(station_walk, "resolve_station",
                        lambda cur, lat, lon: StationMatch(relation_id, name, lat, lon, 4.0))
    monkeypatch.setattr(station_walk, "platform_labels", _NoOverlay)


def test_unresolved_station_is_top_level_not_found(monkeypatch):
    monkeypatch.setattr(station_walk, "resolve_station", lambda cur, lat, lon: None)
    resp = build_station_walk(_FakeConn(), 0.0, 0.0, "1")
    assert resp.found is False
    assert resp.reason == STATION_UNRESOLVED
    assert resp.results == []
    assert resp.from_platform == "1"


def test_skips_the_source_platform(monkeypatch):
    _stub_station(monkeypatch)
    monkeypatch.setattr(station_walk, "list_platform_refs", lambda cur, rel: ["1", "2", "3"])
    monkeypatch.setattr(
        station_walk, "find_shortest_path",
        lambda *a, **k: {"found": True, "walking_time_seconds": 30.0, "walking_distance_meters": 20.0},
    )
    resp = build_station_walk(_FakeConn(), *BERLIN_HBF, "1")
    assert resp.found is True
    assert resp.relation_id == 5688517 and resp.station == "Berlin Hauptbahnhof"
    # Row for every ref EXCEPT the source itself.
    assert [r.to_platform for r in resp.results] == ["2", "3"]


def test_overlay_tracks_are_offered_alongside_osm_refs(monkeypatch):
    """The tool must cover the same platform set the station map shows. OSM labels
    only a handful at a big station (Zürich HB: 5 of ~25), so overlay-only tracks
    are folded in -- deduped against the OSM refs and anchored by their overlay
    coordinate so they can actually route."""
    _stub_station(monkeypatch)
    monkeypatch.setattr(station_walk, "list_platform_refs", lambda cur, rel: ["1", "2"])

    class _Overlay:
        @staticmethod
        def platform_markers(lat, lon, *a, **k):
            return ("Berlin Hauptbahnhof", [{"track": "2"}, {"track": "8"}, {"track": "10"}])

        @staticmethod
        def track_coord(lat, lon, track, *a, **k):
            return (52.5, 13.4) if track in ("8", "10") else None

    monkeypatch.setattr(station_walk, "platform_labels", _Overlay)
    seen = {}

    def _fake_path(conn, rel, src, dst, **kw):
        seen[dst] = kw.get("to_coord")
        return {"found": True, "walking_time_seconds": 30.0, "walking_distance_meters": 20.0}

    monkeypatch.setattr(station_walk, "find_shortest_path", _fake_path)
    resp = build_station_walk(_FakeConn(), *BERLIN_HBF, "1")

    # OSM ref "2" appears once (deduped); overlay-only "8"/"10" are now offered.
    assert [r.to_platform for r in resp.results] == ["2", "8", "10"]
    # ...and the overlay-only ones carry their coordinate as the routing anchor.
    assert seen["8"] == (52.5, 13.4) and seen["10"] == (52.5, 13.4)
    assert seen["2"] is None


def test_unreachable_platform_is_a_found_false_row_with_reason(monkeypatch):
    _stub_station(monkeypatch)
    monkeypatch.setattr(station_walk, "list_platform_refs", lambda cur, rel: ["1", "2", "99"])

    def _fake_path(conn, rel, src, dst, **kw):
        if dst == "99":
            return {"found": False, "reason": "platform_not_found"}
        return {"found": True, "walking_time_seconds": 45.0, "walking_distance_meters": 33.0}

    monkeypatch.setattr(station_walk, "find_shortest_path", _fake_path)
    resp = build_station_walk(_FakeConn(), *BERLIN_HBF, "1")
    by_ref = {r.to_platform: r for r in resp.results}
    assert by_ref["2"].found is True and by_ref["2"].walk_time_s == 45.0
    # An unreachable pair degrades to a row, not an error.
    assert by_ref["99"].found is False
    assert by_ref["99"].reason == "platform_not_found"
    assert by_ref["99"].walk_time_s is None


def test_rows_sorted_by_platform_ref_numeric_then_alphabetic(monkeypatch):
    _stub_station(monkeypatch)
    # Refs arrive deliberately out of order. Rows come back in platform-ref
    # order: numeric first, ascending ("2" before "10", NOT lexicographically),
    # then alphabetic ("A", "D06"). "10" is unreachable and its distance would
    # be smallest, proving a row sorts by its ref -- not its verdict or distance.
    monkeypatch.setattr(station_walk, "list_platform_refs",
                        lambda cur, rel: ["10", "2", "1", "A", "D06"])
    dist = {"2": 120.0, "A": 15.0, "D06": 60.0}

    def _fake_path(conn, rel, src, dst, **kw):
        if dst in dist:
            return {"found": True, "walking_time_seconds": dist[dst],
                    "walking_distance_meters": dist[dst]}
        return {"found": False, "reason": "disconnected"}

    monkeypatch.setattr(station_walk, "find_shortest_path", _fake_path)
    resp = build_station_walk(_FakeConn(), *BERLIN_HBF, "1")
    order = [r.to_platform for r in resp.results]
    assert order == ["2", "10", "A", "D06"]
    # The unreachable "10" keeps its numeric-ref slot, not shoved to the end.
    assert resp.results[1].to_platform == "10" and resp.results[1].found is False


def test_step_free_threads_avoid_elevators(monkeypatch):
    _stub_station(monkeypatch)
    monkeypatch.setattr(station_walk, "list_platform_refs", lambda cur, rel: ["1", "2"])
    captured = {}

    def _fake_path(conn, rel, src, dst, **kw):
        captured.update(kw)
        return {"found": True, "walking_time_seconds": 30.0, "walking_distance_meters": 20.0}

    monkeypatch.setattr(station_walk, "find_shortest_path", _fake_path)
    build_station_walk(_FakeConn(), *BERLIN_HBF, "1", step_free=True)
    assert captured["avoid_elevators"] is True
    assert captured["algorithm"] == "astar"


# ---------------------------------------------------------------------------
# Route-shape (TestClient): the HTTP contract, builder stubbed
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    def _fake_conn():
        yield _FakeConn()

    main.app.dependency_overrides[main.get_conn] = _fake_conn
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def _canned_response(found=True):
    if not found:
        return schemas.StationWalkResponse(
            lat=0.0, lon=0.0, from_platform="1", found=False, reason=STATION_UNRESOLVED)
    return schemas.StationWalkResponse(
        lat=52.5251, lon=13.3694, relation_id=5688517, station="Berlin Hauptbahnhof",
        from_platform="1", step_free=False, found=True,
        results=[
            schemas.StationWalkRow(to_platform="2", found=True, walk_time_s=30.0, walk_distance_m=20.0),
            schemas.StationWalkRow(to_platform="16", found=True, walk_time_s=122.0, walk_distance_m=107.0),
            schemas.StationWalkRow(to_platform="99", found=False, reason="platform_not_found"),
        ],
    )


def test_station_walk_route_returns_rows(client, monkeypatch):
    monkeypatch.setattr(main, "build_station_walk", lambda *a, **k: _canned_response())
    r = client.get("/station-walk", params={"lat": 52.5251, "lon": 13.3694, "from_platform": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert body["station"] == "Berlin Hauptbahnhof" and body["relation_id"] == 5688517
    assert body["from_platform"] == "1"
    refs = [row["to_platform"] for row in body["results"]]
    assert refs == ["2", "16", "99"]
    # snake_case -> the wire keeps snake_case; the Swift decoder camelCases it.
    assert body["results"][0]["walk_time_s"] == 30.0
    assert body["results"][-1]["found"] is False and body["results"][-1]["reason"] == "platform_not_found"


def test_station_walk_route_forwards_params(client, monkeypatch):
    captured = {}

    def _fake_build(conn, lat, lon, from_platform, step_free):
        captured.update(lat=lat, lon=lon, from_platform=from_platform, step_free=step_free)
        return _canned_response()

    monkeypatch.setattr(main, "build_station_walk", _fake_build)
    r = client.get("/station-walk", params={"lat": 52.5, "lon": 13.4,
                                            "from_platform": "5", "step_free": "true"})
    assert r.status_code == 200
    assert captured == {"lat": 52.5, "lon": 13.4, "from_platform": "5", "step_free": True}


def test_station_walk_unresolved_shape(client, monkeypatch):
    monkeypatch.setattr(main, "build_station_walk", lambda *a, **k: _canned_response(found=False))
    r = client.get("/station-walk", params={"lat": 0.0, "lon": 0.0, "from_platform": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False and body["reason"] == STATION_UNRESOLVED
    assert body["results"] == [] and body["relation_id"] is None


def test_station_walk_missing_from_platform_is_422(client):
    assert client.get("/station-walk", params={"lat": 52.5, "lon": 13.4}).status_code == 422


def test_station_walk_missing_lat_is_422(client):
    assert client.get("/station-walk", params={"lon": 13.4, "from_platform": "1"}).status_code == 422


# ---------------------------------------------------------------------------
# DB-gated: the real chain against transfr_eu
# ---------------------------------------------------------------------------

@DB
def test_real_berlin_hbf_reaches_several_platforms():
    import db
    from api.bridge import resolve_station as _resolve
    from search_context import list_platform_refs as _refs

    conn = db.connect(connect_timeout=5)
    lat, lon = BERLIN_HBF
    # Discover a real source platform at whichever relation the coordinate resolves
    # to (the same one /station-walk will), so the source is guaranteed to exist.
    with conn.cursor() as cur:
        match = _resolve(cur, lat, lon)
        assert match is not None, "Berlin Hbf coordinate should resolve to a station"
        refs = _refs(cur, match.relation_id)
    assert refs, "resolved Berlin station should list platforms"
    source = refs[0]

    resp = build_station_walk(conn, lat, lon, source)
    assert resp.found is True
    assert resp.relation_id == match.relation_id
    assert resp.station and "Berlin" in resp.station
    assert resp.from_platform == source
    # The source is never a row against itself.
    assert all(r.to_platform != source for r in resp.results)

    reachable = [r for r in resp.results if r.found]
    assert len(reachable) >= 3, f"expected several reachable platforms, got {len(reachable)}"
    for r in reachable:
        assert r.walk_time_s and r.walk_time_s > 0
        assert r.walk_distance_m and r.walk_distance_m > 0
        assert r.reason is None

    # Platform-ref order: rows come back naturally sorted by ref (numeric before
    # alphabetic), the same order list_platform_refs returns them in.
    from search_context import _natural_key
    out_refs = [r.to_platform for r in resp.results]
    assert out_refs == sorted(out_refs, key=_natural_key)
