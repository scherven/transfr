"""
Tests for the station-centroid index (core/build_station_index.py) that backs
the API's coordinate-based station bridge.

  * Pure tests exercise the centroid math offline -- always run.
  * A DB-gated test (TRANSFR_DB=1) checks the built station_points table against
    a known station, so the real build is one env var away but CI stays offline.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from build_station_index import centroid  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB with station_points built; set TRANSFR_DB=1",
)


# ---------------------------------------------------------------------------
# Pure centroid math
# ---------------------------------------------------------------------------

def test_centroid_empty_is_none():
    assert centroid([]) is None


def test_centroid_single_point_is_itself():
    assert centroid([(50.1070, 8.6634)]) == (50.1070, 8.6634)


def test_centroid_is_the_arithmetic_mean():
    assert centroid([(0.0, 0.0), (2.0, 4.0)]) == (1.0, 2.0)
    lat, lon = centroid([(50.0, 8.0), (50.2, 8.4), (50.1, 8.2)])
    assert lat == pytest.approx(50.1)
    assert lon == pytest.approx(8.2)


def test_centroid_does_not_mutate_input():
    pts = [(1.0, 1.0), (3.0, 3.0)]
    centroid(pts)
    assert pts == [(1.0, 1.0), (3.0, 3.0)]


# ---------------------------------------------------------------------------
# DB-gated: the real built table
# ---------------------------------------------------------------------------

@DB
def test_station_points_is_populated_and_locates_a_known_station():
    import db
    from build_station_index import _fetch_coords  # noqa: F401  (import smoke)

    conn = db.connect(connect_timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT count(*) AS n FROM station_points")
    n = cur.fetchone()["n"]
    assert n > 100_000, f"station_points looks unbuilt ({n} rows) -- run core/build_station_index.py"

    # Frankfurt (Main) Hauptbahnhof stop_area (233704); centroid must sit on the
    # station, i.e. within ~1.5 km of its real coordinate (50.1071, 8.6634).
    cur.execute("SELECT lat, lon FROM station_points WHERE relation_id = 233704")
    row = cur.fetchone()
    assert row is not None, "Frankfurt Hbf (233704) missing from station_points"
    from graph import haversine_meters
    d = haversine_meters(row["lat"], row["lon"], 50.1071, 8.6634)
    assert d < 1500, f"Frankfurt Hbf centroid is {d:.0f} m off"
