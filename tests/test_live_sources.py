"""
Tests for the live data-process pipeline (core/live_sources.py).

Two layers:
  * Pure parser tests run offline against captured real data and the real DB
    Wagenreihung wire schema -- deterministic, always run.
      - tests/fixtures/derf_frankfurt_departures.json : a REAL board captured
        live from dbf.finalrewind.org (eva 8000105) during development.
      - tests/fixtures/transitous_plan_fra_koeln.json  : a REAL MOTIS itinerary
        captured live from api.transitous.org (Frankfurt -> Köln), with real tracks.
      - tests/fixtures/wagenreihung_ice124.json        : the real DB Wagenreihung
        schema (field names confirmed against juliuste/db-wagenreihung's live
        client); values representative because the endpoint is geo-blocked.
  * Live tests actually hit the network and are skipped unless TRANSFR_LIVE=1,
    so CI stays deterministic but the real pull is one env var away.

The whole point: the data-process side is exercised end to end into the SAME
NormalizedFormation / PlatformGeometry the algorithm tests already trust -- no
algorithm code is touched or re-tested here.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from boarding import find_path_from_seat  # noqa: E402
from formation_model import PlatformSectorMap  # noqa: E402
from live_sources import (  # noqa: E402
    Departure,
    FormationUnavailable,
    fetch_db_formation,
    long_distance,
    parse_db_departures,
    parse_transitous_platforms,
    parse_wagenreihung,
    sector_map_from_wagenreihung,
    straight_geometry_for,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fix(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


LIVE = pytest.mark.skipif(
    os.environ.get("TRANSFR_LIVE") != "1",
    reason="live network test; set TRANSFR_LIVE=1 to run against real APIs",
)


# ---------------------------------------------------------------------------
# Departures (real captured board)
# ---------------------------------------------------------------------------

def test_parse_real_departure_board():
    deps = parse_db_departures(_fix("derf_frankfurt_departures.json"))
    assert deps and all(isinstance(d, Departure) for d in deps)
    ice = next(d for d in deps if d.train.startswith("ICE"))
    assert ice.train_number and ice.platform and ice.scheduled_departure
    assert ice.train_class == "F"                       # long-distance
    assert len(ice.route) >= 1                          # downstream stops present


def test_long_distance_filter_keeps_only_fern():
    deps = parse_db_departures(_fix("derf_frankfurt_departures.json"))
    ld = long_distance(deps)
    assert ld and all(d.train_class == "F" for d in ld)
    assert all(not d.train.startswith(("RE", "RB", "S")) for d in ld)


# ---------------------------------------------------------------------------
# Transitous platforms (real captured itinerary)
# ---------------------------------------------------------------------------

def test_parse_real_transitous_tracks():
    legs = parse_transitous_platforms(_fix("transitous_plan_fra_koeln.json"))
    assert legs, "expected at least one transit leg"
    assert any(l.from_track for l in legs), "expected a real platform/track on some leg"
    first = legs[0]
    assert first.from_name and first.mode                # real names + mode came through


# ---------------------------------------------------------------------------
# Wagenreihung (real schema) -> NormalizedFormation -> algorithm
# ---------------------------------------------------------------------------

def test_parse_wagenreihung_maps_the_real_schema():
    nf = parse_wagenreihung(_fix("wagenreihung_ice124.json"))
    assert nf.source == "db-wagenreihung" and nf.country == "DE"
    assert nf.station == "Frankfurt(Main)Hbf" and nf.track == "4"
    assert [p.coach for p in nf.placements] == ["11", "12", "13", "14", "15", "16", "17", "18"]  # power car skipped
    assert nf.has_metres() and nf.has_sectors()
    diner = next(p for p in nf.placements if p.coach == "13")
    assert diner.travel_class == "WR" and diner.sectors == ["B"]
    assert nf.placements[0].travel_class == "1"          # coach 11 first class
    assert nf.placements[0].start_m == pytest.approx(20.3, abs=0.1)  # right after the 20.3 m power car


def test_sector_map_from_real_schema_uses_operator_metres():
    smap = sector_map_from_wagenreihung(_fix("wagenreihung_ice124.json"))
    assert isinstance(smap, PlatformSectorMap)
    assert smap.offset_of(["A"]) == pytest.approx((0.0, 100.5))
    assert smap.offset_of(["D"]) == pytest.approx((301.5, 402.0))


def test_wagenreihung_feeds_the_algorithm_end_to_end():
    """The real formation, resolved to metres, routed by the untouched algorithm:
    a seat in coach 18 (sector D, far end) must be a longer transfer than coach 11."""
    nf = parse_wagenreihung(_fix("wagenreihung_ice124.json"))
    tf = nf.to_train_formation(402.0)                    # metres -> no sector map needed
    geom = straight_geometry_for(402.0)

    # one exit at the A-end: transfer distance == metres-from-A-end + a fixed rest
    coords = dict(geom.coords)
    exit_node = -1
    from graph import haversine_meters, WALKING_SPEED_MS  # noqa: E402
    base = geom.coords[geom.nodes[0]]
    coords[exit_node] = (base[0], base[1] - 0.001)       # ~70 m west of the A-end
    graph = {}
    ids = geom.nodes
    for a, b in zip(ids, ids[1:]):
        w = haversine_meters(*coords[a], *coords[b]) / WALKING_SPEED_MS
        graph.setdefault(a, []).append((b, w, None)); graph.setdefault(b, []).append((a, w, None))
    w = haversine_meters(*coords[ids[0]], *coords[exit_node]) / WALKING_SPEED_MS
    graph.setdefault(ids[0], []).append((exit_node, w, None)); graph.setdefault(exit_node, []).append((ids[0], w, None))

    near = find_path_from_seat(graph, coords, tf, geom, "11", 20, {exit_node})
    far = find_path_from_seat(graph, coords, tf, geom, "18", 20, {exit_node})
    assert near["found"] and far["found"]
    # coach 18 is 7 coaches (7 * 26.4 m) further down the platform than coach 11
    gap = far["walking_distance_meters"] - near["walking_distance_meters"]
    assert gap == pytest.approx(7 * 26.4, abs=1.0)


def test_bad_payload_raises_formation_unavailable():
    with pytest.raises(FormationUnavailable):
        parse_wagenreihung({"data": {}})                 # no istformation


def test_fetch_formation_wraps_network_failure(monkeypatch):
    """A blocked/unreachable host must surface as FormationUnavailable, not a raw
    requests traceback -- so callers can degrade to a platform-level answer."""
    import requests

    class _DeadSession:
        headers = {}
        def get(self, *a, **k):
            raise requests.ConnectionError("blocked")

    with pytest.raises(FormationUnavailable) as ei:
        fetch_db_formation("124", "202607121336", session=_DeadSession())
    assert "geo-restricted" in str(ei.value)


# ---------------------------------------------------------------------------
# Live (opt-in) -- real pulls
# ---------------------------------------------------------------------------

@LIVE
def test_live_departures():
    from live_sources import fetch_db_departures
    deps = fetch_db_departures("8000105")               # Frankfurt Hbf
    assert deps and any(d.train_class == "F" for d in deps)


@LIVE
def test_live_transitous_plan():
    import datetime
    from live_sources import fetch_transitous_plan
    when = (datetime.datetime.utcnow() + datetime.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = fetch_transitous_plan(50.1070, 8.6634, 50.9430, 6.9583, when)
    legs = parse_transitous_platforms(plan)
    assert legs and any(l.from_track for l in legs)


@LIVE
def test_live_formation_is_reachable_or_cleanly_unavailable():
    """Documents reality: from a DE egress this returns a real NormalizedFormation;
    from elsewhere it must raise FormationUnavailable, never hang or crash."""
    from live_sources import fetch_db_departures
    ld = long_distance(fetch_db_departures("8000105"))
    assert ld, "no long-distance departures right now to test with"
    d = ld[0]
    when = "2026" "0712" + (d.scheduled_departure or "0000").replace(":", "")
    try:
        nf = fetch_db_formation(d.train_number, when)
        assert nf.placements and nf.country == "DE"
    except FormationUnavailable:
        pass
