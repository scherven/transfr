"""
Unit tests for the broadened platform matcher (core/search_context).

core/ used to resolve a platform only from railway=platform_edge ways -- the
rarest tagging. find_platform_edges now falls back to railway=platform /
public_transport=platform AREAS (10x more common, and walkable) by ref or
local_ref, but only after the precise platform_edge lookups miss, so stations
mapped with platform_edge are unaffected. These tests pin that precedence.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from search_context import find_platform_edges  # noqa: E402


def _ways(*specs):
    """specs: (way_id, tags) -> a minimal Ways dict."""
    return {wid: {"nodes": [wid * 10, wid * 10 + 1], "tags": tags} for wid, tags in specs}


def test_platform_edge_ref_takes_priority_over_area():
    ways = _ways(
        (1, {"railway": "platform_edge", "ref": "5"}),
        (2, {"railway": "platform", "ref": "5"}),
    )
    assert [w for w, _ in find_platform_edges(ways, "5")] == [1]  # edge wins; area not included


def test_composite_track_ref_edge_beats_area():
    ways = _ways(
        (1, {"railway": "platform_edge", "railway:track_ref": "412/422"}),  # matches logical "12"
        (2, {"railway": "platform", "ref": "12"}),
    )
    assert [w for w, _ in find_platform_edges(ways, "12")] == [1]


def test_falls_back_to_railway_platform_area_by_ref():
    ways = _ways((2, {"railway": "platform", "ref": "5"}))
    assert [w for w, _ in find_platform_edges(ways, "5")] == [2]


def test_falls_back_to_pt_platform_area_by_local_ref():
    ways = _ways((3, {"public_transport": "platform", "local_ref": "5"}))
    assert [w for w, _ in find_platform_edges(ways, "5")] == [3]


def test_returns_all_matching_area_ways():
    ways = _ways(
        (2, {"railway": "platform", "ref": "5"}),
        (3, {"public_transport": "platform", "ref": "5"}),
    )
    assert sorted(w for w, _ in find_platform_edges(ways, "5")) == [2, 3]


def test_no_match_returns_empty():
    assert find_platform_edges(_ways((1, {"railway": "platform", "ref": "9"})), "5") == []
    # a bare stop_position/station tagging is not a walkable platform area here
    assert find_platform_edges(_ways((1, {"public_transport": "stop_position", "ref": "5"})), "5") == []
