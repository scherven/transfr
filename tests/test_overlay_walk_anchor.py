"""
Tests for the overlay walk anchor: routing to a platform OSM does not label.

The station map draws a track marker for every platform in the harvested overlay
(Zürich HB: ~25 tracks), but OSM tags a `ref` on only a handful of them (Zürich
HB: 5). Before this seam, `/walk?from_platform=8&to_platform=10` resolved neither
ref and died as `platform_not_found` -- the map advertised platforms the router
rejected. Now the overlay coordinate (the marker's OWN position) is handed to
core/'s Tier-3 snap, so anything we can draw we can also route to.

DB-gated (TRANSFR_DB=1) where it needs transfr_eu; the pure-lookup tests need
only the harvested overlay file.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from api import platform_labels, schemas  # noqa: E402
from api.walks import build_walk  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)
OVERLAY = pytest.mark.skipif(
    not platform_labels.available(),
    reason="needs the harvested platform_labels.json overlay",
)

# Zürich HB: OSM labels only 1/2/3/21/22; the overlay carries 3..18, 31..34, 41/42...
ZURICH_RELATION = 1532513
ZURICH_LAT, ZURICH_LON = 47.378305, 8.538850


@OVERLAY
def test_track_coord_finds_a_track_osm_does_not_label():
    """The overlay has a real coordinate for track 8 -- the anchor that makes it
    routable even though OSM tags no platform with ref=8 here."""
    coord = platform_labels.track_coord(ZURICH_LAT, ZURICH_LON, "8")
    assert coord is not None
    lat, lon = coord
    assert 47.37 < lat < 47.39 and 8.53 < lon < 8.55


@OVERLAY
def test_track_coord_is_none_for_an_unknown_track():
    assert platform_labels.track_coord(ZURICH_LAT, ZURICH_LON, "no-such-track") is None


@OVERLAY
def test_track_coord_is_none_far_from_any_station():
    # Mid-Atlantic: no harvested station within the overlay's max distance.
    assert platform_labels.track_coord(0.0, -30.0, "8") is None


@DB
@OVERLAY
def test_walk_between_two_platforms_osm_does_not_label():
    """The regression this seam exists for: 8 -> 10 at Zürich HB. Both refs are
    absent from OSM, so this returned platform_not_found (a 200 with no geometry)
    before the overlay anchor."""
    import db  # noqa: E402  -- core/db, via the sys.path insert above

    conn = db.connect(connect_timeout=5)
    try:
        res = build_walk(conn, schemas.WalkKey(
            relation_id=ZURICH_RELATION, from_platform="8", to_platform="10"))
        assert res.ok, f"expected geometry, got reason={res.reason}"
        path = res.export["path"]
        assert path["found"] is True
        # Adjacent island platforms: a short, real cross-platform walk.
        assert 0 < path["walking_distance_meters"] < 200
    finally:
        conn.close()


@DB
def test_osm_labelled_platforms_are_unaffected():
    """A pair OSM DOES label must route exactly as before -- the anchor only
    matters on a ref miss, so this path is untouched."""
    import db  # noqa: E402

    conn = db.connect(connect_timeout=5)
    try:
        res = build_walk(conn, schemas.WalkKey(
            relation_id=ZURICH_RELATION, from_platform="3", to_platform="21"))
        assert res.ok
        assert res.export["path"]["found"] is True
    finally:
        conn.close()


@DB
@OVERLAY
def test_overlay_anchor_degrades_when_track_is_unknown():
    """A ref in neither OSM nor the overlay stays an honest not-found -- the anchor
    never invents a platform. `ok` is True (the export document still builds); the
    honesty lives in the path, which reports platform_not_found rather than
    snapping to whatever happens to be nearby."""
    import db  # noqa: E402

    conn = db.connect(connect_timeout=5)
    try:
        res = build_walk(conn, schemas.WalkKey(
            relation_id=ZURICH_RELATION, from_platform="no-such-track", to_platform="10"))
        assert res.ok is True
        path = res.export["path"]
        assert path["found"] is False
        assert path["reason"] == "platform_not_found"
    finally:
        conn.close()
