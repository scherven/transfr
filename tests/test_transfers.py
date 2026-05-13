"""
Parametrized integration tests for walking time between platform edges.

Each test case specifies a station, two platform numbers, and an upper bound
on the expected walking time in seconds.  Tests require a live PostgreSQL
connection to the openrailwaymap database.

Run with:
    pytest tests/test_transfers.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from test import find_path, DB_CONFIG
from pathfind import init_pool, close_pool


@pytest.fixture(scope="module", autouse=True)
def db():
    init_pool(DB_CONFIG)
    yield
    close_pool()


# ---------------------------------------------------------------------------
# Test cases: (station, platform_a, platform_b, max_time_seconds, description)
# ---------------------------------------------------------------------------
TRANSFER_CASES = [
    # Strasbourg-Ville — existing CLI example, cross-platform walk
    ("Strasbourg-Ville", 1, 7, 600, "Strasbourg platforms 1→7"),
    # Strasbourg-Ville — adjacent platforms, should be fast
    ("Strasbourg-Ville", 1, 2, 180, "Strasbourg adjacent platforms 1→2"),
    # München Hauptbahnhof — adjacent tracks referenced in CLI comment
    ("München Hauptbahnhof", 20, 22, 180, "München adjacent tracks 20→22"),
    # München Hauptbahnhof — far apart platforms, longer walk
    ("München Hauptbahnhof", 1, 20, 600, "München far platforms 1→20"),
    # Same platform both sides (trivial crossing)
    ("Strasbourg-Ville", 3, 3, 60, "Strasbourg same platform 3→3"),
]


@pytest.mark.parametrize(
    "station,p1,p2,max_time_s,label",
    TRANSFER_CASES,
    ids=[c[4] for c in TRANSFER_CASES],
)
def test_transfer_found(station, p1, p2, max_time_s, label):
    """Path must be found between the two platforms."""
    result = find_path(station, p1, station, p2)
    assert result is not None, f"No path found for: {label}"


@pytest.mark.parametrize(
    "station,p1,p2,max_time_s,label",
    [(c[0], c[1], c[2], c[3], c[4]) for c in TRANSFER_CASES if c[1] != c[2]],
    ids=[c[4] for c in TRANSFER_CASES if c[1] != c[2]],
)
def test_transfer_has_distance(station, p1, p2, max_time_s, label):
    """Walking distance must be positive for distinct platforms."""
    result = find_path(station, p1, station, p2)
    if result is None:
        pytest.skip(f"Path not found for {label} — skipping distance check")
    assert result.get("walking_distance_meters", 0) > 0, \
        f"Expected positive distance for {label}, got {result.get('walking_distance_meters')}"


@pytest.mark.parametrize(
    "station,p1,p2,max_time_s,label",
    [(c[0], c[1], c[2], c[3], c[4]) for c in TRANSFER_CASES if c[1] != c[2]],
    ids=[c[4] for c in TRANSFER_CASES if c[1] != c[2]],
)
def test_transfer_has_time(station, p1, p2, max_time_s, label):
    """Walking time must be positive and within plausible upper bound."""
    result = find_path(station, p1, station, p2)
    if result is None:
        pytest.skip(f"Path not found for {label} — skipping time check")
    t = result.get("walking_time_seconds", 0)
    assert t > 0, f"Expected positive walking time for {label}, got {t}"
    assert t <= max_time_s, \
        f"Walking time {t}s exceeds {max_time_s}s limit for {label}"


@pytest.mark.parametrize(
    "station,p1,p2,max_time_s,label",
    [(c[0], c[1], c[2], c[3], c[4]) for c in TRANSFER_CASES if c[1] != c[2]],
    ids=[c[4] for c in TRANSFER_CASES if c[1] != c[2]],
)
def test_transfer_has_breakdown(station, p1, p2, max_time_s, label):
    """Path breakdown must be non-empty and internally consistent."""
    result = find_path(station, p1, station, p2)
    if result is None:
        pytest.skip(f"Path not found for {label} — skipping breakdown check")
    breakdown = result.get("path_breakdown", [])
    assert len(breakdown) > 0, f"Expected non-empty breakdown for {label}"
    # Breakdown totals must roughly match top-level totals
    total_d = sum(s["distance_m"] for s in breakdown)
    total_t = sum(s["time_s"] for s in breakdown)
    assert abs(total_d - result.get("walking_distance_meters", 0)) < 1.0, \
        f"Breakdown distance {total_d} doesn't match total {result.get('walking_distance_meters')}"
    assert abs(total_t - result.get("walking_time_seconds", 0)) < 1.0, \
        f"Breakdown time {total_t} doesn't match total {result.get('walking_time_seconds')}"


def test_transfer_symmetry():
    """Walking time A→B should equal B→A (undirected graph)."""
    station = "Strasbourg-Ville"
    result_fwd = find_path(station, 1, station, 7)
    result_rev = find_path(station, 7, station, 1)
    if result_fwd is None or result_rev is None:
        pytest.skip("Path not found — skipping symmetry check")
    t_fwd = result_fwd.get("walking_time_seconds", 0)
    t_rev = result_rev.get("walking_time_seconds", 0)
    # Allow 5% tolerance for floating-point differences
    assert abs(t_fwd - t_rev) / max(t_fwd, t_rev, 1) < 0.05, \
        f"Forward {t_fwd}s vs reverse {t_rev}s differ by more than 5%"
