"""
Tests for Tier-3 platform resolution (core/search_context) and the platform-label
recovery it enables (api/transfers + api/bridge).

Tier 3 is the coordinate fallback: when a feed labels a platform with a code OSM
carries nowhere (Köln Hbf reports its main-hall tracks as an "84-91" block for the
public Gleise 1-11), the ref resolves to no platform, but the journey stop's own
coordinate still sits on the real platform. We snap that coordinate to the nearest
pedestrian way and route from there, and -- for display only -- recover the real
platform number to show next to the feed's code.

  * A pure test pins the display-label guard (missing coordinate -> no label).
  * DB-gated tests (TRANSFR_DB=1) prove Köln's renumbered transfer, previously
    platform_not_found, now routes with a sensible distance; that the fallback is
    purely additive (a ref that already resolves is byte-for-byte unchanged); and
    that the recovered display label is the real Gleis, absent at normal stations.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import ground_truth as gt  # noqa: E402
from api.bridge import nearest_platform_label  # noqa: E402
from api.transfers import FEASIBLE, assess_transfer  # noqa: E402
from viz_export import export  # noqa: E402
from api import pipeline  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs transfr_eu with osm_nodes coord index; set TRANSFR_DB=1",
)

# Köln Hbf: the stop_area_group relation, and the real MOTIS quay coordinates for
# the DELFI tracks "84".."91" (which are the public Gleise 2..9). ~17 m separates
# the same-island 88/89 (=6/7); ~100 m separates the far 84/91 (=2/9).
KOLN_REL = 6875142
K89 = (50.943394, 6.9587010)   # feed "89" -> real platform 7
K88 = (50.943320, 6.958548)    # feed "88" -> real platform 6
K84 = (50.942574, 6.958647)    # feed "84" -> real platform 2
K91 = (50.943493, 6.958916)    # feed "91" -> real platform 9


# ---------------------------------------------------------------------------
# Pure: display-label guard
# ---------------------------------------------------------------------------

def test_nearest_platform_label_none_without_coordinate():
    # No coordinate -> no lookup, no label (cur is never touched).
    assert nearest_platform_label(None, None, None) is None


# ---------------------------------------------------------------------------
# DB-gated: Tier-3 routing
# ---------------------------------------------------------------------------

@DB
def test_koln_renumbered_transfer_is_platform_not_found_without_coords():
    """Baseline: the feed's "89"/"88" match no OSM platform at Köln, so the
    ref-only search (no coordinate) can't resolve them -- the gap Tier 3 fixes."""
    import db
    conn = db.connect(connect_timeout=5)
    r = gt.find_shortest_path(conn, KOLN_REL, "89", "88", algorithm="astar")
    assert not r.get("found")
    assert r.get("reason") == "platform_not_found"


@DB
@pytest.mark.parametrize("algorithm", ["astar", "dijkstra"])
def test_koln_renumbered_transfer_resolves_by_coordinate(algorithm):
    """With the stops' coordinates, Tier 3 snaps to the real platforms and routes.
    88/89 are the two tracks of one island (public 6/7), so it's a short
    cross-platform walk -- and the result flags that both ends came from the snap.
    Both algorithms agree (dijkstra is the ground-truth baseline)."""
    import db
    conn = db.connect(connect_timeout=5)
    r = gt.find_shortest_path(conn, KOLN_REL, "89", "88", algorithm=algorithm,
                              from_coord=K89, to_coord=K88)
    assert r.get("found"), f"did not resolve: {r.get('reason')}"
    assert r["source_by_coord"] and r["target_by_coord"]
    assert 0 < r["walking_distance_meters"] < 60  # same-island cross-platform


@DB
def test_tier3_scales_from_near_to_far():
    """The snap is not collapsing everything to one point: a same-island change
    (89->88) is much shorter than a cross-hall one (84->91, public 2->9)."""
    import db
    conn = db.connect(connect_timeout=5)
    near = gt.find_shortest_path(conn, KOLN_REL, "89", "88", algorithm="astar",
                                 from_coord=K89, to_coord=K88)
    far = gt.find_shortest_path(conn, KOLN_REL, "84", "91", algorithm="astar",
                                from_coord=K84, to_coord=K91)
    assert near.get("found") and far.get("found")
    assert far["walking_distance_meters"] > near["walking_distance_meters"] + 50


@DB
def test_tier3_is_additive_when_ref_already_resolves():
    """A platform_edge station (Strasbourg, numeric refs) resolves by ref, so
    passing coordinates must change nothing -- same walk time, same path, and the
    by-coord flags stay False."""
    import db
    conn = db.connect(connect_timeout=5)
    STRASBOURG_REL = 5347313
    base = gt.find_shortest_path(conn, STRASBOURG_REL, "1", "3", algorithm="astar")
    withc = gt.find_shortest_path(conn, STRASBOURG_REL, "1", "3", algorithm="astar",
                                  from_coord=(48.5850, 7.7350), to_coord=(48.5850, 7.7350))
    assert base.get("found") and withc.get("found")
    assert withc["walking_time_seconds"] == base["walking_time_seconds"]
    assert withc["node_path"] == base["node_path"]
    assert not withc["source_by_coord"] and not withc["target_by_coord"]


# ---------------------------------------------------------------------------
# DB-gated: display-label recovery (api/transfers)
# ---------------------------------------------------------------------------

@DB
def test_koln_assess_recovers_real_platform_for_display():
    """End to end: the Köln 89->88 change is feasible, and both ends carry the
    real platform sign (7/6) alongside the feed's code (89/88) for the hint."""
    import db
    conn = db.connect(connect_timeout=5)
    a = assess_transfer(
        conn,
        arr_lat=K89[0], arr_lon=K89[1], arr_platform="89", arr_time="2026-07-17T10:00:00Z",
        dep_lat=K88[0], dep_lon=K88[1], dep_platform="88", dep_time="2026-07-17T10:15:00Z",
    )
    assert a.verdict == FEASIBLE, f"got {a.verdict}/{a.reason}"
    assert a.arrival_platform == "89" and a.arrival_platform_actual == "7"
    assert a.departure_platform == "88" and a.departure_platform_actual == "6"


@DB
def test_normal_station_has_no_display_hint():
    """Where the feed's label already is the real Gleis (München Ost 3->5, area
    platforms that resolve by ref), no coordinate fallback runs, so there is no
    recovered label and thus no spurious "operator lists it as N" hint."""
    import db
    conn = db.connect(connect_timeout=5)
    a = assess_transfer(
        conn,
        arr_lat=48.1280, arr_lon=11.6039, arr_platform="3", arr_time="2026-07-17T10:00:00Z",
        dep_lat=48.1280, dep_lon=11.6039, dep_platform="5", dep_time="2026-07-17T10:30:00Z",
    )
    assert a.verdict == FEASIBLE, f"got {a.verdict}/{a.reason}"
    assert a.arrival_platform_actual is None
    assert a.departure_platform_actual is None


# ---------------------------------------------------------------------------
# DB-gated: the drawable /walk geometry (viz_export) and leg propagation
# ---------------------------------------------------------------------------

@DB
def test_koln_walk_geometry_draws_only_with_coords():
    """viz_export.export must draw the same walk the verdict resolved: the feed's
    "89"/"88" alone yield no geometry (platform_not_found), but with the stops'
    coordinates the Tier-3 snap gives a real path -- keeping geometry == verdict."""
    import db
    conn = db.connect(connect_timeout=5)
    without = export(conn, KOLN_REL, "89", "88", algorithm="astar")
    assert not without["path"]["found"]
    withc = export(conn, KOLN_REL, "89", "88", algorithm="astar", from_coord=K89, to_coord=K88)
    assert withc["path"]["found"]
    assert len(withc["path"]["points"]) >= 2


@DB
def test_enrich_propagates_recovered_platform_to_transfer_and_legs():
    """End to end through the pipeline: a Hannover->Köln->Stuttgart itinerary whose
    Köln stops carry the feed's "89"/"88". The change and BOTH adjacent legs must
    carry the recovered real platform (7/6), and the transfer must carry the stop
    coordinates the client forwards to /walk."""
    import db

    def _leg(name, o, olat, olon, d, dlat, dlon, dep_t, arr_t, dep_p, arr_p):
        return {"mode": "HIGHSPEED_RAIL", "train_name": name,
                "origin": {"name": o, "latitude": olat, "longitude": olon},
                "destination": {"name": d, "latitude": dlat, "longitude": dlon},
                "departure": dep_t, "arrival": arr_t,
                "departure_platform": dep_p, "arrival_platform": arr_p}

    journey = {"id": "t", "legs": [
        _leg("ICE 654", "Hannover Hbf", 52.3766, 9.7411, "Köln Hbf", K89[0], K89[1],
             "2026-07-17T13:00:00Z", "2026-07-17T14:07:00Z", None, "89"),
        _leg("ICE 225", "Köln Hbf", K88[0], K88[1], "Stuttgart Hbf", 48.7841, 9.1816,
             "2026-07-17T14:19:00Z", "2026-07-17T16:30:00Z", "88", None),
    ]}
    conn = db.connect(connect_timeout=5)
    resp = pipeline.enrich(conn, {"journeys": [journey]}, assess=True)
    j = resp.journeys[0]
    t = j.transfers[0]
    assert t.arrival_platform == "89" and t.arrival_platform_actual == "7"
    assert t.departure_platform == "88" and t.departure_platform_actual == "6"
    assert t.arr_lat == K89[0] and t.dep_lon == K88[1]           # coords for the client WalkKey
    assert j.legs[0].arrival_platform_actual == "7"              # arriving leg
    assert j.legs[1].departure_platform_actual == "6"            # departing leg
