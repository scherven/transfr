"""
Pure unit tests for core/dijkstra.py -- no database needed.

These validate the shortest-path *algorithm* itself against small,
hand-computable graphs, including a cross-check against exhaustive
all-simple-paths enumeration (the most literal possible "brute force").
If these pass, we can trust Dijkstra as the oracle that
core/ground_truth.py builds on.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from dijkstra import all_simple_paths, shortest_path  # noqa: E402


def test_straight_line():
    graph = {
        "A": [("B", 5.0, "w1")],
        "B": [("A", 5.0, "w1"), ("C", 3.0, "w2")],
        "C": [("B", 3.0, "w2")],
    }
    result = shortest_path(graph, {"A"}, {"C"})
    assert result is not None
    total, path = result
    assert total == 8.0
    assert path == ["A", "B", "C"]


def test_picks_shorter_of_two_routes():
    # A -> C direct (weight 10) vs A -> B -> C (weight 3 + 3 = 6)
    graph = {
        "A": [("C", 10.0, "direct"), ("B", 3.0, "w1")],
        "B": [("C", 3.0, "w2")],
        "C": [],
    }
    total, path = shortest_path(graph, {"A"}, {"C"})
    assert total == 6.0
    assert path == ["A", "B", "C"]


def test_unreachable_returns_none():
    graph = {"A": [("B", 1.0, "w1")], "B": [], "C": []}
    assert shortest_path(graph, {"A"}, {"C"}) is None


def test_source_equals_target():
    graph = {"A": [("B", 1.0, "w1")]}
    total, path = shortest_path(graph, {"A"}, {"A"})
    assert total == 0.0
    assert path == ["A"]


def test_multi_source_multi_target_picks_global_best():
    # Two possible sources (S1, S2), two possible targets (T1, T2).
    # Best pairing is S2 -> T1 at cost 1, everything else is more expensive.
    graph = {
        "S1": [("T2", 100.0, "w")],
        "S2": [("T1", 1.0, "w"), ("T2", 50.0, "w")],
        "T1": [],
        "T2": [],
    }
    total, path = shortest_path(graph, {"S1", "S2"}, {"T1", "T2"})
    assert total == 1.0
    assert path == ["S2", "T1"]


def test_directed_edge_only_traversable_one_way():
    # A -> B allowed, B -> A NOT in the graph (models conveying=forward)
    graph = {"A": [("B", 2.0, "escalator")], "B": []}
    assert shortest_path(graph, {"A"}, {"B"}) == (2.0, ["A", "B"])
    assert shortest_path(graph, {"B"}, {"A"}) is None


def test_negative_weight_rejected():
    graph = {"A": [("B", -1.0, "w")]}
    try:
        shortest_path(graph, {"A"}, {"B"})
        assert False, "expected ValueError for negative edge weight"
    except ValueError:
        pass


def test_matches_brute_force_all_paths_on_random_small_graphs():
    """Cross-check: on a graph with cycles and multiple routes, Dijkstra's
    answer must match the true minimum found by exhaustively enumerating
    every simple path. This is the "trust no cleverness" sanity check."""
    graph = {
        "A": [("B", 4.0, "w"), ("C", 1.0, "w")],
        "B": [("D", 1.0, "w"), ("A", 4.0, "w")],
        "C": [("B", 2.0, "w"), ("D", 6.0, "w"), ("A", 1.0, "w")],
        "D": [("B", 1.0, "w"), ("C", 6.0, "w")],
    }
    dijkstra_result = shortest_path(graph, {"A"}, {"D"})
    brute_force_results = all_simple_paths(graph, "A", {"D"})
    best_brute_force = min(brute_force_results, key=lambda r: r[0])

    assert dijkstra_result is not None
    assert dijkstra_result[0] == best_brute_force[0]
    # A -1-> C -2-> B -1-> D = 4.0, matches A -4-> B -1-> D = 5.0? check both:
    # A->C->B->D = 1+2+1 = 4.0 is the true minimum.
    assert dijkstra_result[0] == 4.0


def test_empty_sources_or_targets():
    graph = {"A": [("B", 1.0, "w")]}
    assert shortest_path(graph, set(), {"B"}) is None
    assert shortest_path(graph, {"A"}, set()) is None
