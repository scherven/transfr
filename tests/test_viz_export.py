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
