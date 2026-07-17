"""
Tests for the single-station connectivity classifier behind /station-health.

  * Offline: `_sample`'s stride cap and `build_station_health`'s bucketing, with
    resolve_station / list_platform_refs / find_shortest_path stubbed -- a fixed
    3-platform topology exercises connected / stitchable / island, the plain-pass-
    first optimisation, the percentages, and the stitchable-first examples; a
    30-ref station exercises the combinatorics cap. Plus a FastAPI TestClient
    route-shape test (routing / validation / shape), the build stubbed.
  * DB-gated (TRANSFR_DB=1): the real chain against transfr_eu -- Berlin Hbf is
    mostly-connected with a sane platform count.
"""

import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
import api.station_health as sh  # noqa: E402
from api import schemas  # noqa: E402
from api.bridge import StationMatch  # noqa: E402
from api.station_health import ISLAND, STITCHABLE, build_station_health  # noqa: E402
from api.transfers import STATION_UNRESOLVED  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)


# ---------------------------------------------------------------------------
# _sample: the combinatorics cap
# ---------------------------------------------------------------------------

def test_sample_leaves_small_lists_untouched():
    refs = [str(i) for i in range(1, 11)]  # 10 refs, well under the cap
    out, sampled = sh._sample(refs, sh.MAX_PLATFORMS)
    assert out == refs and sampled is False


def test_sample_strides_evenly_over_large_lists():
    refs = [str(i) for i in range(30)]  # 30 > 24
    out, sampled = sh._sample(refs, sh.MAX_PLATFORMS)
    assert sampled is True
    assert len(out) == sh.MAX_PLATFORMS
    assert len(set(out)) == sh.MAX_PLATFORMS      # no duplicates
    assert out[0] == "0"                          # spans from the first
    assert out == sorted(out, key=lambda s: int(s))  # order preserved (strided)


# ---------------------------------------------------------------------------
# build_station_health: bucketing, with the DB primitives stubbed
# ---------------------------------------------------------------------------

class _FakeConn:
    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def cursor(self):
        return self._Cur()


def _stub_station(monkeypatch, refs, rules):
    """Resolve to a fixed station with `refs`, and route find_shortest_path by a
    {frozenset(pair): (plain_found, stitch_found)} rule table. Returns a list that
    records every find_shortest_path call so the plain-first optimisation is
    checkable."""
    monkeypatch.setattr(sh, "resolve_station",
                        lambda cur, lat, lon: StationMatch(42, "Teststation", lat, lon, 3.0))
    monkeypatch.setattr(sh, "list_platform_refs", lambda cur, rel: list(refs))

    calls = []

    def _fsp(conn, relation_id, a, b, algorithm="astar", use_stitch_bridges=False, **kw):
        calls.append((a, b, use_stitch_bridges))
        plain_ok, stitch_ok = rules[frozenset((a, b))]
        found = stitch_ok if use_stitch_bridges else plain_ok
        return {"found": True} if found else {"found": False, "reason": "disconnected"}

    monkeypatch.setattr(sh, "find_shortest_path", _fsp)
    return calls


def test_build_classifies_each_bucket(monkeypatch):
    # 1-2 connected; 1-3 stitchable (plain fails, stitch succeeds); 2-3 island.
    rules = {
        frozenset(("1", "2")): (True, True),
        frozenset(("1", "3")): (False, True),
        frozenset(("2", "3")): (False, False),
    }
    _stub_station(monkeypatch, ["1", "2", "3"], rules)

    r = build_station_health(_FakeConn(), 52.5, 13.4)
    assert r.found is True and r.station == "Teststation" and r.relation_id == 42
    assert r.platform_count == 3
    assert (r.connected, r.stitchable, r.island) == (1, 1, 1)
    # Three equal buckets -> ~33.3% each; they sum to ~100.
    assert r.connected_pct == pytest.approx(33.3)
    assert round(r.connected_pct + r.stitchable_pct + r.island_pct) == 100
    assert r.sampled is False


def test_build_skips_stitch_pass_for_connected_pairs(monkeypatch):
    rules = {
        frozenset(("1", "2")): (True, True),
        frozenset(("1", "3")): (False, True),
        frozenset(("2", "3")): (False, False),
    }
    calls = _stub_station(monkeypatch, ["1", "2", "3"], rules)
    build_station_health(_FakeConn(), 52.5, 13.4)

    # A connected pair costs exactly one pathfind (no stitch retry); a failing pair
    # costs two. So: 1 (connected) + 2 (stitchable) + 2 (island) = 5 calls, and the
    # only pair that ever runs a stitch pass is a plain-failed one.
    assert len(calls) == 5
    stitched_pairs = {frozenset((a, b)) for a, b, stitch in calls if stitch}
    assert frozenset(("1", "2")) not in stitched_pairs
    assert stitched_pairs == {frozenset(("1", "3")), frozenset(("2", "3"))}


def test_build_examples_prefer_stitchable_and_cap(monkeypatch):
    # 5 platforms: make several islands plus one stitchable, and check the one
    # stitchable pair leads the examples (recoverable first) and the list is capped.
    refs = ["1", "2", "3", "4", "5"]
    rules = {}
    for a, b in itertools.combinations(refs, 2):
        rules[frozenset((a, b))] = (False, False)      # island by default
    rules[frozenset(("1", "2"))] = (True, True)        # one connected
    rules[frozenset(("4", "5"))] = (False, True)       # one stitchable
    _stub_station(monkeypatch, refs, rules)

    r = build_station_health(_FakeConn(), 0.0, 0.0)
    assert r.connected == 1 and r.stitchable == 1
    assert r.island == len(list(itertools.combinations(refs, 2))) - 2
    assert len(r.examples) <= sh.MAX_EXAMPLES
    assert r.examples[0].kind == STITCHABLE           # recoverable pair surfaced first
    assert {e.kind for e in r.examples} <= {STITCHABLE, ISLAND}  # never a connected pair


def test_build_samples_pathologically_large_station(monkeypatch):
    refs = [str(i) for i in range(30)]                 # 30 > MAX_PLATFORMS (24)
    # Everything connects, so the sweep is cheap and the counts are pure pair math.
    rules = {frozenset((a, b)): (True, True)
             for a, b in itertools.combinations(refs, 2)}
    _stub_station(monkeypatch, refs, rules)

    r = build_station_health(_FakeConn(), 0.0, 0.0)
    assert r.platform_count == 30                       # true total, not the sample
    assert r.sampled is True
    n = sh.MAX_PLATFORMS
    assert r.connected == n * (n - 1) // 2              # pairs over the 24 sampled
    assert r.stitchable == 0 and r.island == 0
    assert r.connected_pct == 100.0


def test_build_unresolved_station(monkeypatch):
    monkeypatch.setattr(sh, "resolve_station", lambda cur, lat, lon: None)
    r = build_station_health(_FakeConn(), 0.0, 0.0)
    assert r.found is False and r.reason == STATION_UNRESOLVED
    assert r.platform_count == 0 and r.examples == []


def test_build_single_platform_has_no_pairs(monkeypatch):
    monkeypatch.setattr(sh, "resolve_station",
                        lambda cur, lat, lon: StationMatch(7, "Halt", lat, lon, 1.0))
    monkeypatch.setattr(sh, "list_platform_refs", lambda cur, rel: ["1"])
    # No pair to pathfind; a call here would be a bug.
    monkeypatch.setattr(sh, "find_shortest_path",
                        lambda *a, **k: pytest.fail("no pathfind for a lone platform"))
    r = build_station_health(_FakeConn(), 0.0, 0.0)
    assert r.found is True and r.platform_count == 1
    assert (r.connected, r.stitchable, r.island) == (0, 0, 0)
    assert r.connected_pct == 0.0 and r.examples == []


# ---------------------------------------------------------------------------
# FastAPI TestClient: routing / validation / response shape (build stubbed)
# ---------------------------------------------------------------------------

class _FakeConnMain(_FakeConn):
    pass


@pytest.fixture
def client():
    def _fake_conn():
        yield _FakeConnMain()

    main.app.dependency_overrides[main.get_conn] = _fake_conn
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def _canned_health(**over):
    base = dict(
        lat=52.5251, lon=13.3694, relation_id=5688520, station="Berlin, S Hauptbahnhof",
        found=True, platform_count=14, connected=89, stitchable=1, island=1,
        connected_pct=97.8, stitchable_pct=1.1, island_pct=1.1, sampled=False,
        examples=[schemas.StationHealthPair(from_platform="9", to_platform="12", kind="island")],
    )
    base.update(over)
    return schemas.StationHealthResponse(**base)


def test_station_health_route_shape(client, monkeypatch):
    captured = {}

    def _fake_build(conn, lat, lon):
        captured.update(lat=lat, lon=lon)
        return _canned_health()

    monkeypatch.setattr(main, "build_station_health", _fake_build)
    r = client.get("/station-health", params={"lat": 52.5251, "lon": 13.3694})
    assert r.status_code == 200
    body = r.json()
    assert captured == {"lat": 52.5251, "lon": 13.3694}
    assert body["found"] is True
    assert body["relation_id"] == 5688520 and body["station"] == "Berlin, S Hauptbahnhof"
    assert body["platform_count"] == 14
    assert (body["connected"], body["stitchable"], body["island"]) == (89, 1, 1)
    # snake_case survives the wire and the example pair carries its kind.
    assert body["connected_pct"] == 97.8
    assert body["examples"][0] == {"from_platform": "9", "to_platform": "12", "kind": "island"}


def test_station_health_unresolved_shape(client, monkeypatch):
    monkeypatch.setattr(
        main, "build_station_health",
        lambda conn, lat, lon: schemas.StationHealthResponse(
            lat=lat, lon=lon, found=False, reason=STATION_UNRESOLVED),
    )
    r = client.get("/station-health", params={"lat": 0.0, "lon": 0.0})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False and body["reason"] == STATION_UNRESOLVED
    assert body["platform_count"] == 0 and body["examples"] == []


def test_station_health_missing_lon_is_422(client):
    assert client.get("/station-health", params={"lat": 52.5}).status_code == 422


# ---------------------------------------------------------------------------
# DB-gated: the real classifier against transfr_eu
# ---------------------------------------------------------------------------

@DB
def test_real_berlin_hbf_mostly_connected():
    import db

    conn = db.connect(connect_timeout=5)
    r = build_station_health(conn, 52.5251, 13.3694)

    assert r.found is True
    assert "Berlin" in (r.station or "")
    # A real Berlin Hbf relation carries a sane platform count.
    assert 8 <= r.platform_count <= 24
    assert r.sampled is False                     # under the sampling cap
    # Every pair is accounted for, and the station is overwhelmingly connected.
    total = r.connected + r.stitchable + r.island
    assert total == r.platform_count * (r.platform_count - 1) // 2
    assert r.connected >= r.stitchable + r.island
    assert r.connected_pct >= 80.0
