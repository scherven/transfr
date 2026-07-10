"""
Pure unit tests for core/graph.py's tag-interpretation and graph-building
logic -- no database needed. These pin down the walking-speed model and the
"don't silently cost a missing segment at zero" behavior.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from graph import (  # noqa: E402
    ELEVATOR_WAIT_S,
    ESCALATOR_SPEED_MS,
    STEPS_SPEED_MS,
    WALKING_SPEED_MS,
    build_time_weighted_graph,
    haversine_meters,
    way_direction,
    way_speed_and_penalty,
)


def test_haversine_zero_distance():
    assert haversine_meters(48.85, 2.35, 48.85, 2.35) == 0.0


def test_haversine_known_distance():
    # Paris (48.8566, 2.3522) to Berlin (52.5200, 13.4050) is ~878 km.
    d = haversine_meters(48.8566, 2.3522, 52.5200, 13.4050)
    assert 870_000 < d < 890_000


def test_haversine_symmetric():
    a = haversine_meters(48.85, 2.35, 52.52, 13.40)
    b = haversine_meters(52.52, 13.40, 48.85, 2.35)
    assert math.isclose(a, b)


def test_speed_plain_footway():
    speed, penalty = way_speed_and_penalty({"highway": "footway"})
    assert speed == WALKING_SPEED_MS
    assert penalty == 0.0


def test_speed_steps_slower_than_footway():
    speed, _ = way_speed_and_penalty({"highway": "steps"})
    assert speed == STEPS_SPEED_MS
    assert speed < WALKING_SPEED_MS


def test_speed_escalator():
    speed, penalty = way_speed_and_penalty({"highway": "footway", "conveying": "yes"})
    assert speed == ESCALATOR_SPEED_MS
    assert penalty == 0.0


def test_speed_elevator_has_wait_penalty():
    speed, penalty = way_speed_and_penalty({"highway": "elevator"})
    assert penalty == ELEVATOR_WAIT_S
    assert penalty > 0


def test_direction_default_undirected():
    assert way_direction({"highway": "footway"}) == 0


def test_direction_conveying_forward():
    assert way_direction({"conveying": "forward"}) == 1


def test_direction_conveying_backward():
    assert way_direction({"conveying": "backward"}) == -1


def test_direction_oneway_yes():
    assert way_direction({"oneway": "yes"}) == 1


def test_direction_oneway_reverse():
    assert way_direction({"oneway": "-1"}) == -1


def test_build_graph_undirected_footway_has_both_directions():
    ways = {1: {"nodes": [10, 20], "tags": {"highway": "footway"}}}
    coords = {10: (0.0, 0.0), 20: (0.0, 0.001)}
    graph = build_time_weighted_graph(ways, coords)
    assert any(nb == 20 for nb, _, _ in graph.get(10, []))
    assert any(nb == 10 for nb, _, _ in graph.get(20, []))


def test_build_graph_forward_escalator_is_one_directional():
    ways = {1: {"nodes": [10, 20], "tags": {"highway": "footway", "conveying": "forward"}}}
    coords = {10: (0.0, 0.0), 20: (0.0, 0.001)}
    graph = build_time_weighted_graph(ways, coords)
    assert any(nb == 20 for nb, _, _ in graph.get(10, []))
    assert not any(nb == 10 for nb, _, _ in graph.get(20, []))


def test_build_graph_skips_segment_with_missing_coordinate_instead_of_zeroing_it():
    # Node 30 has no coordinate -- the (20, 30) segment must be dropped
    # entirely, not silently treated as a free (zero-weight) edge.
    ways = {1: {"nodes": [10, 20, 30], "tags": {"highway": "footway"}}}
    coords = {10: (0.0, 0.0), 20: (0.0, 0.001)}  # 30 missing on purpose
    graph = build_time_weighted_graph(ways, coords)
    assert any(nb == 20 for nb, _, _ in graph.get(10, []))
    assert 30 not in graph
    assert not any(nb == 30 for nb, _, _ in graph.get(20, []))


def test_build_graph_weight_reflects_real_distance_and_speed():
    ways = {1: {"nodes": [10, 20], "tags": {"highway": "steps"}}}
    coords = {10: (0.0, 0.0), 20: (0.0, 0.001)}
    graph = build_time_weighted_graph(ways, coords)
    dist = haversine_meters(0.0, 0.0, 0.0, 0.001)
    (_, weight, _), = [e for e in graph[10] if e[0] == 20]
    assert math.isclose(weight, dist / STEPS_SPEED_MS)
