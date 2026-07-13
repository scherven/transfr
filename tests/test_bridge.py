"""
Tests for the coordinate station bridge (api/bridge.py).

  * Unit tests drive resolve_station with a fake cursor -- deterministic, offline.
  * DB-gated tests (TRANSFR_DB=1) resolve the REAL stop coordinates in the
    captured journey fixtures against the built station_points index, proving the
    MOTIS->OSM join works on live data.
"""

import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from api.bridge import (  # noqa: E402
    DEFAULT_MAX_DISTANCE_M, map_track_to_ref, resolve_station, resolve_station_candidates,
)

FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "journeys")

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB with station_points built; set TRANSFR_DB=1",
)


class _FakeCursor:
    """Minimal RealDictCursor stand-in: records params, replays preset rows."""

    def __init__(self, rows):
        self._rows = rows
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_params = params

    def fetchall(self):
        return self._rows


def _row(rid, lat, lon, name="S"):
    return {"relation_id": rid, "name": name, "lat": lat, "lon": lon}


# ---------------------------------------------------------------------------
# map_track_to_ref
# ---------------------------------------------------------------------------

def test_map_track_to_ref():
    assert map_track_to_ref(None) is None
    assert map_track_to_ref("") is None
    assert map_track_to_ref("  ") is None
    assert map_track_to_ref("7") == "7"
    assert map_track_to_ref("  7 ") == "7"
    assert map_track_to_ref("3a") == "3a"   # sub-platform preserved
    assert map_track_to_ref("A") == "A"     # lettered platform preserved


def test_map_track_to_ref_strips_label_prefixes():
    # Feeds that prepend a label word -> the bare ref OSM actually tags.
    assert map_track_to_ref("Gl 1") == "1"       # Swiss "Gleis"
    assert map_track_to_ref("Gleis 5") == "5"
    assert map_track_to_ref("Regio 3") == "3"    # Frankfurt airport regional
    assert map_track_to_ref("Voie 2") == "2"     # French
    assert map_track_to_ref("Binario 4") == "4"  # Italian
    assert map_track_to_ref("Spoor 8a") == "8a"  # Dutch, keeps sub-letter


def test_resolve_candidates_sorted_within_radius_and_limited():
    rows = [
        _row(1, 50.1071, 8.6634, "on top"),   # ~0 m
        _row(2, 50.1081, 8.6640, "close"),    # ~120 m
        _row(9, 50.2000, 8.9000, "far"),      # >10 km
    ]
    cands = resolve_station_candidates(_FakeCursor(rows), [(50.1071, 8.6634)], radius_m=1000.0)
    assert [c.relation_id for c in cands] == [1, 2]   # nearest first, far one dropped
    assert all(c.distance_m <= 1000.0 for c in cands)


def test_resolve_candidates_uses_min_distance_over_points():
    rows = [_row(1, 50.10, 8.60, "near p1"), _row(2, 50.20, 8.80, "near p2")]
    cands = resolve_station_candidates(_FakeCursor(rows), [(50.10, 8.60), (50.20, 8.80)], radius_m=500.0)
    assert {c.relation_id for c in cands} == {1, 2}   # each sits on one of the points
    assert all(c.distance_m < 50 for c in cands)


def test_resolve_candidates_respects_limit_and_empty():
    rows = [_row(i, 50.1071 + i * 1e-5, 8.6634, f"s{i}") for i in range(10)]
    assert len(resolve_station_candidates(_FakeCursor(rows), [(50.1071, 8.6634)], radius_m=1000.0, limit=3)) == 3
    assert resolve_station_candidates(_FakeCursor([]), [], radius_m=1000.0) == []


# ---------------------------------------------------------------------------
# resolve_station (fake cursor)
# ---------------------------------------------------------------------------

def test_resolve_picks_the_nearest_candidate():
    # query point near Frankfurt Hbf; two candidates, one clearly closer
    near = _row(1, 50.1071, 8.6634, "Frankfurt Hbf")
    far = _row(2, 50.1200, 8.7000, "Somewhere else")
    match = resolve_station(_FakeCursor([far, near]), 50.1071, 8.6634)
    assert match is not None
    assert match.relation_id == 1
    assert match.distance_m < 5  # essentially on top of the query point


def test_resolve_returns_none_when_all_candidates_beyond_max():
    # candidate ~3 km away, but max_distance is 1000 m
    far = _row(9, 50.135, 8.663)
    assert resolve_station(_FakeCursor([far]), 50.107, 8.663, max_distance_m=1000.0) is None


def test_resolve_returns_none_on_empty():
    assert resolve_station(_FakeCursor([]), 50.0, 8.0) is None


def test_resolve_bbox_brackets_the_query_point():
    cur = _FakeCursor([])
    resolve_station(cur, 50.0, 8.0, max_distance_m=1200.0)
    min_lat, max_lat, min_lon, max_lon = cur.last_params
    assert min_lat < 50.0 < max_lat
    assert min_lon < 8.0 < max_lon
    # ~1.2 km in latitude is ~0.0108 deg; box half-height must be in that ballpark
    assert 0.005 < (max_lat - 50.0) < 0.02


# ---------------------------------------------------------------------------
# DB-gated: resolve real MOTIS stop coordinates from the journey fixtures
# ---------------------------------------------------------------------------

def _fixture_transit_stops():
    """(slug, name, lat, lon) for every transit leg endpoint that has coords."""
    out = []
    for path in sorted(glob.glob(os.path.join(FIX_DIR, "*.json"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for itin in data["response"].get("itineraries", []):
            for leg in itin.get("legs", []):
                if leg.get("mode") in ("WALK", "BIKE", "CAR"):
                    continue
                for end in (leg.get("from"), leg.get("to")):
                    if end and end.get("lat") is not None and end.get("lon") is not None:
                        out.append((slug, end.get("name"), end["lat"], end["lon"]))
    return out


@DB
def test_real_fixture_stops_resolve_to_nearby_stations():
    """Every transit stop in the corpus should resolve to a station centroid
    within DEFAULT_MAX_DISTANCE_M -- the join the whole pipeline depends on."""
    import db

    conn = db.connect(connect_timeout=5)
    cur = conn.cursor()
    stops = _fixture_transit_stops()
    assert stops, "no transit stops found in fixtures"

    resolved = 0
    for slug, name, lat, lon in stops:
        match = resolve_station(cur, lat, lon)
        if match is not None:
            assert match.distance_m <= DEFAULT_MAX_DISTANCE_M
            resolved += 1
    # These are real mainline stations; the vast majority must resolve.
    rate = resolved / len(stops)
    assert rate > 0.9, f"only {resolved}/{len(stops)} ({rate:.0%}) stops resolved to a station"


@DB
def test_frankfurt_coordinate_resolves_to_a_frankfurt_station():
    import db

    conn = db.connect(connect_timeout=5)
    match = resolve_station(conn.cursor(), 50.1071, 8.6634)
    assert match is not None
    assert match.distance_m < 800
    assert "Frankfurt" in (match.name or ""), f"got {match.name!r}"
