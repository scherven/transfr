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
