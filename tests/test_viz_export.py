"""Unit tests for the pure geometry/classification logic in core/viz_export.py.

These need no database -- they cover the parts most likely to be subtly wrong:
parsing the many real-world shapes of the OSM `level` tag, classifying way
kinds, and interpolating a connector's height between floors.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

import viz_export as vx


# --- parse_levels: every form that actually occurs in transfr_eu -------------

def test_parse_single_and_missing():
    assert vx.parse_levels("1") == [1.0]
    assert vx.parse_levels("0") == [0.0]
    assert vx.parse_levels(None) == [0.0]     # untagged outdoor ways -> ground
    assert vx.parse_levels("") == [0.0]


def test_parse_semicolon_lists():
    assert vx.parse_levels("-1;0") == [-1.0, 0.0]
    assert vx.parse_levels("0;1") == [0.0, 1.0]
    assert vx.parse_levels("-2;-1;0") == [-2.0, -1.0, 0.0]


def test_parse_dash_range_keeps_leading_sign():
    # "-3-0" is a range from -3 to 0, NOT the number -3 minus 0.
    assert vx.parse_levels("-3-0") == [-3.0, 0.0]
    # a plain negative single level must not be read as a range.
    assert vx.parse_levels("-2") == [-2.0]


def test_parse_half_levels_and_garbage():
    assert vx.parse_levels("0.5") == [0.5]
    assert vx.parse_levels("-0.5") == [-0.5]
    assert vx.parse_levels("mezzanine") == [0.0]  # unparseable -> ground, no crash


# --- way_kind / is_connector -------------------------------------------------

def test_way_kind_splits_vertical_circulation():
    assert vx.way_kind({"conveying": "yes", "highway": "steps"}, [0, 1]) == "escalator"
    assert vx.way_kind({"highway": "steps"}, [0, 1]) == "stairs"
    assert vx.way_kind({"highway": "elevator"}, [0, 1]) == "elevator"
    assert vx.way_kind({"railway": "platform"}, [0]) == "platform"


def test_way_kind_walkway_vs_ramp():
    assert vx.way_kind({"highway": "footway"}, [1]) == "walkway"
    assert vx.way_kind({"highway": "footway"}, [0, 1]) == "ramp"  # spans levels


def test_is_connector():
    assert vx.is_connector({"highway": "steps"}, [0, 1]) is True
    assert vx.is_connector({"highway": "footway"}, [-1, 0]) is True   # multi-level
    assert vx.is_connector({"highway": "footway"}, [0]) is False


def test_keep_poi_filters_street_furniture():
    # real places are kept...
    assert vx._keep_poi("shop", "bakery") is True
    assert vx._keep_poi("amenity", "restaurant") is True
    assert vx._keep_poi("tourism", "museum") is True
    # ...street furniture tagged amenity=* is dropped
    assert vx._keep_poi("amenity", "bench") is False
    assert vx._keep_poi("amenity", "waste_basket") is False
    assert vx._keep_poi("amenity", "vending_machine") is False


def test_platform_ref_prefers_ref_then_local_then_track():
    assert vx.platform_ref({"ref": "12"}) == "12"
    assert vx.platform_ref({"local_ref": "3a"}) == "3a"
    assert vx.platform_ref({"railway:track_ref": "7"}) == "7"
    assert vx.platform_ref({"ref": "1", "local_ref": "9"}) == "1"   # ref wins
    assert vx.platform_ref({}) is None                               # bare area


def test_is_non_rail_platform_flags_other_modes_not_rail():
    # A rail station map must not draw tram/bus platforms even though they share
    # the station footprint. Explicit non-rail modes are dropped by kind...
    assert vx._is_non_rail_platform({"railway": "platform", "tram": "yes"}) is True
    assert vx._is_non_rail_platform({"railway": "platform", "bus": "yes"}) is True
    assert vx._is_non_rail_platform({"railway": "platform", "trolleybus": "yes"}) is True
    assert vx._is_non_rail_platform({"highway": "platform"}) is True   # bus island
    # ...while a rail platform (tagged train=yes or nothing) is kept.
    assert vx._is_non_rail_platform({"railway": "platform", "train": "yes"}) is False
    assert vx._is_non_rail_platform({"railway": "platform"}) is False


def test_seg_dist2_point_to_segment():
    # point on the segment -> 0
    assert vx._seg_dist2(1, 0, 0, 0, 2, 0) == 0.0
    # perpendicular offset from the middle of the segment
    assert vx._seg_dist2(1, 3, 0, 0, 2, 0) == 9.0
    # beyond an endpoint -> distance to that endpoint, not the infinite line
    assert vx._seg_dist2(5, 0, 0, 0, 2, 0) == 9.0
    # degenerate segment (a == b) -> plain point distance
    assert vx._seg_dist2(3, 4, 0, 0, 0, 0) == 25.0


def test_node_kind_classifies_vertical_nodes():
    # an elevator mapped on a node (the Berlin OTIS case)
    assert vx.node_kind({"highway": "elevator", "level": "-2;-1;0;1;2"}) == "elevator"
    assert vx.node_kind({"railway": "elevator"}) == "elevator"
    assert vx.node_kind({"conveying": "yes"}) == "escalator"
    assert vx.node_kind({"highway": "steps"}) == "stairs"
    # a level change at a node with nothing saying how -> "vertical", never a crash
    assert vx.node_kind({}) == "vertical"
    assert vx.node_kind({"public_transport": "stop_position"}) == "vertical"


# --- way_node_heights: flat vs interpolated ----------------------------------

def test_flat_way_single_level():
    coords = {1: (48.0, 7.0), 2: (48.0, 7.001), 3: (48.0, 7.002)}
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    h = vx.way_node_heights([1, 2, 3], coords, [1.0], proj)
    assert h == {1: 4.0, 2: 4.0, 3: 4.0}    # 1 * 4 m


def test_connector_interpolates_endpoints():
    # a staircase from level 0 to level 1 over three evenly spaced nodes:
    # first node at z=0, last at z=4 (1 floor), middle in between.
    coords = {1: (48.0, 7.0), 2: (48.0, 7.001), 3: (48.0, 7.002)}
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    h = vx.way_node_heights([1, 2, 3], coords, [0.0, 1.0], proj)
    assert h[1] == 0.0
    assert h[3] == 4.0
    assert 0.0 < h[2] < 4.0


def test_missing_coords_are_skipped():
    coords = {1: (48.0, 7.0)}  # node 2 has no coordinate
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    h = vx.way_node_heights([1, 2], coords, [1.0], proj)
    assert set(h) == {1}


def test_node_levels_orient_a_reversed_level_list():
    # A connector tagged level='1;2' whose nodes actually run L2 -> L1 (as
    # Berlin Hbf escalator 269400497 is mapped). Without the per-node hint the
    # level-list order wins and the endpoints render flipped (the "N"); with the
    # endpoints' own level tags the slope follows the real floors.
    coords = {1: (48.0, 7.0), 2: (48.0, 7.002)}
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)

    flipped = vx.way_node_heights([1, 2], coords, [1.0, 2.0], proj)
    assert flipped[1] == proj.z(1.0) and flipped[2] == proj.z(2.0)

    oriented = vx.way_node_heights([1, 2], coords, [1.0, 2.0], proj, {1: 2.0, 2: 1.0})
    assert oriented[1] == proj.z(2.0)  # node 1's own tag (L2) wins
    assert oriented[2] == proj.z(1.0)  # node 2's own tag (L1) wins


# --- is_area_way / multi-level-area flattening (Karlsruhe room=elevator) ------

def test_is_area_way_detects_polygons():
    assert vx.is_area_way({"area": "yes"}, [1, 2, 3]) is True
    assert vx.is_area_way({"indoor": "room"}, [1, 2, 3]) is True
    assert vx.is_area_way({"building": "elevator"}, [1, 2, 3]) is True
    assert vx.is_area_way({"building:part": "elevator"}, [1, 2, 3]) is True
    # a closed node ring is an area even with no explicit area tag
    assert vx.is_area_way({}, [1, 2, 3, 1]) is True
    # a plain open linear way (a footway, a staircase) is NOT an area
    assert vx.is_area_way({"highway": "footway"}, [1, 2, 3]) is False
    assert vx.is_area_way({"highway": "steps"}, [1, 2]) is False


def test_multi_level_area_is_flattened_not_ramped():
    # A room=elevator polygon tagged level=0;1 (Karlsruhe Hbf way 270880470):
    # its boundary has no along-slope order, so it must render FLAT at its lowest
    # level, not interpolate a fake ramp around its perimeter that reads as a
    # burst of phantom transitions.
    coords = {1: (48.0, 7.0), 2: (48.0, 7.001), 3: (48.001, 7.001), 4: (48.001, 7.0)}
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    flat = vx.way_node_heights([1, 2, 3, 4], coords, [0.0, 1.0], proj, is_area=True)
    assert set(flat.values()) == {0.0}
    # the same node ring as a genuine linear connector still slopes end-to-end
    sloped = vx.way_node_heights([1, 2, 3, 4], coords, [0.0, 1.0], proj, is_area=False)
    assert min(sloped.values()) == 0.0 and max(sloped.values()) == 4.0


# --- way_for_hop preference (Stuttgart untagged-duplicate-way flip) -----------

def test_hop_way_rank_orders_level_then_path_then_bare():
    # explicit level beats a bare mapped path; a mapped path beats empty geometry
    assert vx._hop_way_rank({"level": "1", "highway": "footway"}) > vx._hop_way_rank({"highway": "footway"})
    assert vx._hop_way_rank({"highway": "footway"}) > vx._hop_way_rank({})
    # level="0" is an EXPLICIT ground tag, not an absent one -> still outranks bare
    assert vx._hop_way_rank({"level": "0"}) > vx._hop_way_rank({})


# --- detail_entry: projecting a POI/building into the details layer ----------

def test_detail_entry_projects_poi_at_origin():
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    feat = {"kind": "poi", "category": "amenity", "subtype": "toilets",
            "name": "WC", "level_raw": "0", "lat": 48.0, "lon": 7.0}
    e = vx.detail_entry(proj, feat, path_xy=[(0.0, 0.0)])
    assert e["kind"] == "poi" and e["category"] == "amenity" and e["subtype"] == "toilets"
    assert e["name"] == "WC"
    assert e["xyz"] == [0.0, 0.0, 0.0]        # the origin projects to the origin
    assert "focus" not in e                    # not flagged unless asked


def test_detail_entry_lifts_poi_to_its_level_and_flags_focus():
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    feat = {"kind": "poi", "category": "shop", "subtype": "coffee", "name": "Cafe",
            "level_raw": "1", "lat": 48.0, "lon": 7.0, "focus": True}
    e = vx.detail_entry(proj, feat, path_xy=[(0.0, 0.0)])
    assert e["xyz"][2] == 4.0                  # level 1 * 4 m floor height
    assert e["focus"] is True                  # the chosen facility is marked


def test_detail_entry_measures_distance_to_the_nearest_path_point():
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    # A POI 10 m east of origin (lon delta); the path passes right by the origin
    # and also far away -- the nearest point wins.
    dlon = 10.0 / proj.m_per_deg_lon
    feat = {"kind": "poi", "category": "amenity", "subtype": "atm",
            "name": None, "level_raw": None, "lat": 48.0, "lon": 7.0 + dlon}
    e = vx.detail_entry(proj, feat, path_xy=[(0.0, 0.0), (500.0, 500.0)])
    assert abs(e["dist"] - 10.0) < 0.1


def test_detail_entry_building_uses_outline_centroid():
    proj = vx.Projector(48.0, 7.0, floor_height_m=4.0)
    feat = {"kind": "building", "category": "building", "subtype": "train_station",
            "name": "Hbf", "level_raw": None,
            "outline": [(48.0, 7.0), (48.0, 7.001), (48.001, 7.001), (48.001, 7.0)]}
    e = vx.detail_entry(proj, feat, path_xy=[(0.0, 0.0)])
    assert "points" in e and "xyz" not in e     # a building carries an outline, not a point
    assert len(e["points"]) == 4


def test_way_for_hop_prefers_level_tagged_over_untagged_stub():
    # Two ways place nodes 10 and 11 adjacent: a level=1 concourse AREA and a
    # tag-less stub laid over the same pair (the Stuttgart Hbf pattern). The
    # tagged way must win in BOTH hop directions regardless of set iteration
    # order, so the hop's height is read off it, not off the stub whose missing
    # level would default the pair to the ground plane.
    way_cache = {
        100: {"nodes": [10, 11, 12], "tags": {"highway": "footway", "level": "1", "area": "yes"}},
        200: {"nodes": [11, 10], "tags": {}},
    }
    node_to_ways = {10: {100, 200}, 11: {100, 200}, 12: {100}}
    assert vx.way_for_hop(10, 11, way_cache, node_to_ways) == 100
    assert vx.way_for_hop(11, 10, way_cache, node_to_ways) == 100


# ---------------------------------------------------------------------------
# DB-gated (TRANSFR_DB=1): the station-map export against transfr_eu --
# GTFS-overlay track markers, the tram/bus declutter, and the degenerate
# same-island walk that used to crash. Zurich HB carries the full overlay
# (~25 tracks) and the '1;2' shared-island refs, so it exercises all three.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)

ZURICH_HB = 1532513   # stop_area relation; OSM refs only ~3 platforms, GTFS all ~25


def _platform_ways(data):
    return [w for w in data["ways"] if w["kind"] == "platform"]


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


@DB
def test_all_platforms_markers_outnumber_osm_refs():
    import db
    conn = db.connect(connect_timeout=5)
    data = vx.export(conn, ZURICH_HB, "3", "21", all_platforms=True)
    meta, markers = data["meta"], data["platform_markers"]

    assert meta["all_platforms"] is True
    assert meta["n_platform_markers"] == len(markers)
    # OSM tags only a few platform geometries with a ref; the GTFS overlay labels
    # every track, so the map carries many more track labels than OSM alone.
    osm_refs = {w.get("ref") for w in _platform_ways(data) if w.get("ref")}
    assert len(markers) > len(osm_refs)
    assert len(markers) >= 20          # Zurich HB has ~25 tracks
    # One label per track, each with a projected coordinate and a level slot.
    assert len({m["track"] for m in markers}) == len(markers)
    for m in markers:
        assert m["track"]
        assert all(isinstance(m[k], (int, float)) for k in ("x", "y", "z"))
        assert m["level"] is None or isinstance(m["level"], int)   # None -> ground


@DB
def test_no_platform_markers_when_all_platforms_false():
    import db
    conn = db.connect(connect_timeout=5)
    data = vx.export(conn, ZURICH_HB, "3", "21")     # all_platforms defaults False
    assert data["platform_markers"] == []
    assert data["meta"]["all_platforms"] is False
    assert data["meta"]["n_platform_markers"] == 0


@DB
def test_station_map_draws_no_tram_or_bus_platforms():
    """Declutter: the station map is bounded to rail platforms near the tracks, so
    no tram/bus platform surface is drawn even though the ~600 m footprint is full
    of them, and every drawn platform sits at the rail core (not 700 m out)."""
    import db
    conn = db.connect(connect_timeout=5)
    data = vx.export(conn, ZURICH_HB, "3", "21", all_platforms=True)
    plats = _platform_ways(data)
    assert plats, "the station map should draw at least the rail platforms it has"

    with conn.cursor() as cur:
        cur.execute("SELECT id, tags FROM osm_ways WHERE id = ANY(%s)", ([w["id"] for w in plats],))
        tags = {r["id"]: (r["tags"] or {}) for r in cur.fetchall()}
    # Neither gate leaks: no drawn platform is tagged for another mode...
    assert not [w["id"] for w in plats if vx._is_non_rail_platform(tags.get(w["id"], {}))]
    # ...and none is centred out with the far tram stops (Stauffacher ~870 m away).
    for w in plats:
        cx, cy = _centroid(w["points"])
        assert (cx * cx + cy * cy) ** 0.5 < 400.0


@DB
def test_same_island_platform_walk_is_degenerate_not_a_crash():
    """Zurich HB '1' and '2' resolve to the one shared '1;2' island polygon, so the
    search returns a found, zero-hop path with no drawn points. Building the
    endpoints from pts[0] used to IndexError; it must now flag the path degenerate
    and skip the endpoints instead."""
    import db
    conn = db.connect(connect_timeout=5)
    data = vx.export(conn, ZURICH_HB, "1", "2")      # must not raise
    path = data["path"]
    assert path["found"] is True
    assert path["points"] == []
    assert path.get("degenerate") is True
    assert "endpoints" not in path
