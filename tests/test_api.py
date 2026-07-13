"""
Tests for the FastAPI HTTP layer (api/main.py), via TestClient.

Offline and deterministic: the DB dependency is overridden with a fake
connection, and the service layer (plan_journeys / resolve_station /
find_shortest_path) is stubbed per test, so these exercise the HTTP contract --
routing, query validation, status codes, response shape -- not the DB.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
from api import schemas  # noqa: E402
from api.bridge import StationMatch  # noqa: E402
from api.transfers import FEASIBLE, STATION_UNRESOLVED, TIGHT  # noqa: E402


class _FakeConn:
    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def cursor(self):
        return self._Cur()


@pytest.fixture
def client():
    def _fake_conn():
        yield _FakeConn()

    main.app.dependency_overrides[main.get_conn] = _fake_conn
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /health and /stations (no DB, no network)
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_stations_autocomplete(client):
    r = client.get("/stations", params={"q": "Frankf"})
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert names and any("Frankfurt" in n for n in names)


def test_stations_query_too_short_is_422(client):
    assert client.get("/stations", params={"q": "F"}).status_code == 422


# ---------------------------------------------------------------------------
# /journeys
# ---------------------------------------------------------------------------

def _canned_journeys():
    return schemas.JourneysResponse(
        origin=schemas.Place(name="A"),
        destination=schemas.Place(name="B"),
        departure_time="2026-07-13T09:00:00+02:00",
        journeys=[schemas.Journey(
            id="j1", num_changes=1, verdict=TIGHT,
            legs=[schemas.Leg(mode="highspeed_rail",
                              origin=schemas.Place(name="A"), destination=schemas.Place(name="B"))],
            transfers=[schemas.Transfer(verdict=TIGHT, at_station="X", walk_time_s=200.0, layover_s=240.0)],
        )],
    )


def test_journeys_happy_path(client, monkeypatch):
    monkeypatch.setattr(main, "plan_journeys", lambda *a, **k: _canned_journeys())
    r = client.get("/journeys", params={"from": "A", "to": "B"})
    assert r.status_code == 200
    body = r.json()
    assert body["origin"]["name"] == "A"
    assert body["journeys"][0]["verdict"] == TIGHT
    assert body["journeys"][0]["transfers"][0]["at_station"] == "X"


def test_journeys_passes_through_query_params(client, monkeypatch):
    captured = {}

    def _fake_plan(conn, origin, destination, when, max_journeys=5, **kw):
        captured.update(origin=origin, destination=destination, max_journeys=max_journeys)
        return _canned_journeys()

    monkeypatch.setattr(main, "plan_journeys", _fake_plan)
    r = client.get("/journeys", params={"from": "Frankfurt", "to": "Köln", "max": 3})
    assert r.status_code == 200
    assert captured == {"origin": "Frankfurt", "destination": "Köln", "max_journeys": 3}


def test_journeys_missing_destination_is_422(client):
    assert client.get("/journeys", params={"from": "A"}).status_code == 422


def test_journeys_unresolvable_station_is_404(client, monkeypatch):
    def _raise(*a, **k):
        raise ValueError("No station found for: 'Nowhere'")

    monkeypatch.setattr(main, "plan_journeys", _raise)
    r = client.get("/journeys", params={"from": "Nowhere", "to": "B"})
    assert r.status_code == 404
    assert "No station" in r.json()["detail"]


def test_journeys_bad_time_is_400(client):
    # bad time is rejected before any provider call
    r = client.get("/journeys", params={"from": "A", "to": "B", "time": "not-a-time"})
    assert r.status_code == 400


def test_journeys_max_out_of_range_is_422(client):
    assert client.get("/journeys", params={"from": "A", "to": "B", "max": 0}).status_code == 422


# ---------------------------------------------------------------------------
# /transfer (debug endpoint)
# ---------------------------------------------------------------------------

def test_transfer_found(client, monkeypatch):
    monkeypatch.setattr(main, "resolve_station",
                        lambda *a, **k: StationMatch(6365739, "Colmar", 48.07, 7.35, 4.0))
    monkeypatch.setattr(main, "find_shortest_path",
                        lambda *a, **k: {"found": True, "walking_time_seconds": 120.0, "walking_distance_meters": 150.0})
    r = client.get("/transfer", params={"lat": 48.07, "lon": 7.35, "from_platform": "A", "to_platform": "B"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert body["walk_time_s"] == 120.0
    assert body["station"] == "Colmar" and body["relation_id"] == 6365739


def test_transfer_station_unresolved(client, monkeypatch):
    monkeypatch.setattr(main, "resolve_station", lambda *a, **k: None)
    r = client.get("/transfer", params={"lat": 0.0, "lon": 0.0, "from_platform": "1", "to_platform": "2"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False and body["reason"] == STATION_UNRESOLVED


def test_transfer_not_found_surfaces_reason(client, monkeypatch):
    monkeypatch.setattr(main, "resolve_station",
                        lambda *a, **k: StationMatch(1, "S", 50.0, 8.0, 3.0))
    monkeypatch.setattr(main, "find_shortest_path",
                        lambda *a, **k: {"found": False, "reason": "platform_not_found"})
    r = client.get("/transfer", params={"lat": 50.0, "lon": 8.0, "from_platform": "1", "to_platform": "99"})
    assert r.status_code == 200
    assert r.json()["reason"] == "platform_not_found"
