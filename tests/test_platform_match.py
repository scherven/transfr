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

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from search_context import SearchContext, _ref_tokens, find_platform_edges  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB (with the ref-token GIN indexes); set TRANSFR_DB=1",
)


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


# ---------------------------------------------------------------------------
# Compound refs: one platform serving several tracks is tagged ';'-joined
# ('3;4' = island platform, tracks 3 and 4). A bare track number must match
# either side. Common in KR/JP; ~4.9k platforms in EU too.
# ---------------------------------------------------------------------------

def test_ref_tokens_splits_compound():
    assert _ref_tokens("3;4") == ["3", "4"]
    assert _ref_tokens("10;11") == ["10", "11"]


def test_ref_tokens_trims_space_around_semicolon():
    assert _ref_tokens("3; 4") == ["3", "4"]
    assert _ref_tokens("3 ; 4") == ["3", "4"]


def test_ref_tokens_plain_ref_is_a_single_token():
    assert _ref_tokens("7") == ["7"]
    assert _ref_tokens("3a") == ["3a"]


def test_ref_tokens_label_with_embedded_space_is_not_split():
    # 'Steig 1' / 'Voie 2' are single labels, NOT track 1 / track 2. Splitting on
    # whitespace would make them false-match a bare number.
    assert _ref_tokens("Steig 1") == ["Steig 1"]
    assert _ref_tokens("Voie 2") == ["Voie 2"]


def test_ref_tokens_empty_or_none():
    assert _ref_tokens(None) == []
    assert _ref_tokens("") == []


def test_compound_area_ref_matches_either_track():
    ways = _ways((2, {"railway": "platform", "ref": "3;4"}))
    assert [w for w, _ in find_platform_edges(ways, "3")] == [2]
    assert [w for w, _ in find_platform_edges(ways, "4")] == [2]  # the high side too
    assert find_platform_edges(ways, "5") == []


def test_compound_platform_edge_ref_matches_either_track():
    ways = _ways((1, {"railway": "platform_edge", "ref": "5;6"}))
    assert [w for w, _ in find_platform_edges(ways, "5")] == [1]
    assert [w for w, _ in find_platform_edges(ways, "6")] == [1]


def test_compound_local_ref_matches():
    ways = _ways((3, {"public_transport": "platform", "local_ref": "7;8"}))
    assert [w for w, _ in find_platform_edges(ways, "8")] == [3]


def test_label_with_space_does_not_false_match_bare_number():
    ways = _ways((2, {"railway": "platform", "ref": "Steig 1"}))
    assert find_platform_edges(ways, "1") == []


def test_compound_edge_still_beats_compound_area():
    """Precedence is unchanged for compounds: a platform_edge wins over an area
    even when both are compound and both contain the queried track."""
    ways = _ways(
        (1, {"railway": "platform_edge", "ref": "3;4"}),
        (2, {"railway": "platform", "ref": "3;4"}),
    )
    assert [w for w, _ in find_platform_edges(ways, "4")] == [1]


# ---------------------------------------------------------------------------
# DB-gated: the SQL token-containment fallback in _find_platform_edges_near
# (the GIN-indexed path -- distinct code from the in-memory matcher above).
# Stations pinned from the live transfr_eu data where the compound way is the
# ONLY source for the bare track number, so these fail if the fallback breaks.
# ---------------------------------------------------------------------------

def _resolve(rel, a, b):
    import db

    conn = db.connect(connect_timeout=5)
    try:
        with conn.cursor() as cur:
            return SearchContext(cur, rel, a, b)
    finally:
        conn.close()


@DB
def test_db_bare_ref_resolves_via_compound_way():
    """Messina Centrale tags one island platform way ref='7;8' and nothing else
    carries 7 or 8: both bare numbers must resolve to that same compound way."""
    ctx = _resolve(10744837, "7", "8")
    assert ctx.error is None, f"setup failed: {ctx.error}"
    assert [w for w, _, _ in ctx.edges_1] == [1112073446]
    assert [w for w, _, _ in ctx.edges_2] == [1112073446]


@DB
def test_db_mixed_precise_and_compound_resolution():
    """SKM Podjuchy: track 1 has its own ref='1' way (precise tier must still win
    for it); track 2 exists only inside the compound ref='1;2' way."""
    ctx = _resolve(19731101, "1", "2")
    assert ctx.error is None, f"setup failed: {ctx.error}"
    assert [w for w, _, _ in ctx.edges_1] == [1108049527]  # precise, NOT the compound
    assert [w for w, _, _ in ctx.edges_2] == [1108049535]  # the '1;2' compound


@DB
def test_db_single_ref_stations_unaffected_by_token_fallback():
    """Tallinn has both single-ref platform_edges and compound areas; the precise
    tier must keep winning (the compound fallback never fires)."""
    ctx = _resolve(9725786, "7", "8")
    assert ctx.error is None, f"setup failed: {ctx.error}"
    assert all(t.get("ref") == "7" for _, _, t in ctx.edges_1)
    assert all(t.get("ref") == "8" for _, _, t in ctx.edges_2)
