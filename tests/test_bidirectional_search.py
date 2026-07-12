"""
Rigorous, DB-free tests for core/bidirectional_search.py.

This algorithm is subtle enough that it gets a dedicated test file with a
constructed counterexample (not just "run it and see if it looks right"):
bidirectional Dijkstra's classic bug is stopping as soon as some node has
been settled by *both* searches and returning distF[node] + distB[node] --
which can converge to a confidently-wrong (too large) answer, because the
true shortest path's meeting point is often an *edge* whose two endpoints
are settled on *different* sides, not a single node settled on both.

Nothing here touches a database. core/algo_bidirectional.py (the DB-backed
wrapper) is only written after every test in this file passes.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from bidirectional_search import bidirectional_shortest_path, reverse_graph  # noqa: E402
from dijkstra import shortest_path  # noqa: E402


# ---------------------------------------------------------------------------
# reverse_graph()
# ---------------------------------------------------------------------------

def test_reverse_graph_transposes_edges():
    graph = {"A": [("B", 5.0, "w1")], "B": [("C", 2.0, "w2")]}
    rev = reverse_graph(graph)
    assert rev == {"B": [("A", 5.0, "w1")], "C": [("B", 2.0, "w2")]}


def test_reverse_graph_of_reverse_graph_is_original_edge_set():
    graph = {"A": [("B", 1.0, "w1"), ("C", 2.0, "w2")], "B": [("C", 3.0, "w3")]}
    rev_rev = reverse_graph(reverse_graph(graph))
    edges = lambda g: sorted((u, v, w, lbl) for u, es in g.items() for v, w, lbl in es)  # noqa: E731
    assert edges(rev_rev) == edges(graph)


# ---------------------------------------------------------------------------
# Basic sanity (mirrors test_dijkstra.py's coverage, bidirectional version)
# ---------------------------------------------------------------------------

def _undirected(edges):
    """Build forward+backward graphs for an undirected weighted graph
    given as (a, b, weight, label) tuples."""
    fwd = {}
    for a, b, w, label in edges:
        fwd.setdefault(a, []).append((b, w, label))
        fwd.setdefault(b, []).append((a, w, label))
    return fwd, reverse_graph(fwd)


def test_straight_line():
    fwd, bwd = _undirected([("A", "B", 5.0, "w1"), ("B", "C", 3.0, "w2")])
    result = bidirectional_shortest_path(fwd, bwd, {"A"}, {"C"})
    assert result is not None
    total, path = result
    assert total == 8.0
    assert path == ["A", "B", "C"]


def test_source_equals_target():
    fwd, bwd = _undirected([("A", "B", 1.0, "w1")])
    total, path = bidirectional_shortest_path(fwd, bwd, {"A"}, {"A"})
    assert total == 0.0
    assert path == ["A"]


def test_unreachable_returns_none():
    fwd = {"A": [("B", 1.0, "w1")], "B": [], "C": []}
    bwd = reverse_graph(fwd)
    assert bidirectional_shortest_path(fwd, bwd, {"A"}, {"C"}) is None


def test_multi_source_multi_target():
    fwd = {
        "S1": [("T2", 100.0, "w")],
        "S2": [("T1", 1.0, "w"), ("T2", 50.0, "w")],
        "T1": [],
        "T2": [],
    }
    bwd = reverse_graph(fwd)
    total, path = bidirectional_shortest_path(fwd, bwd, {"S1", "S2"}, {"T1", "T2"})
    assert total == 1.0
    assert path == ["S2", "T1"]


def test_empty_sources_or_targets():
    fwd = {"A": [("B", 1.0, "w")]}
    bwd = reverse_graph(fwd)
    assert bidirectional_shortest_path(fwd, bwd, set(), {"B"}) is None
    assert bidirectional_shortest_path(fwd, bwd, {"A"}, set()) is None


# ---------------------------------------------------------------------------
# Directed edges: forward-only, must not be traversable backward as if
# undirected. Mirrors test_dijkstra.py's directed-edge coverage.
# ---------------------------------------------------------------------------

def test_directed_edge_respected_forward():
    fwd = {"A": [("B", 2.0, "escalator")], "B": []}
    bwd = reverse_graph(fwd)
    assert bidirectional_shortest_path(fwd, bwd, {"A"}, {"B"}) == (2.0, ["A", "B"])


def test_directed_edge_not_traversable_reverse():
    fwd = {"A": [("B", 2.0, "escalator")], "B": []}
    bwd = reverse_graph(fwd)
    assert bidirectional_shortest_path(fwd, bwd, {"B"}, {"A"}) is None


def test_directed_edge_wrong_reverse_graph_would_be_caught_by_ground_truth_diff():
    """Sanity-check that reverse_graph() actually matters: if backward
    search used the *forward* graph by mistake (a plausible copy-paste bug
    when wiring this into SearchContext), a one-way A->B edge would
    incorrectly look traversable B->A. This test pins the *correct*
    behavior so that mistake would fail test_directed_edge_not_traversable_reverse
    above, not silently pass."""
    fwd = {"A": [("B", 2.0, "escalator")], "B": []}
    wrong_bwd = fwd  # the bug: using forward graph as if it were reversed
    # With the buggy "reversed" graph, backward search from A can reach B
    # via the (mis-used) forward edge -- demonstrating why this specific
    # mistake would NOT be caught unless we assert the correct-graph
    # behavior explicitly, which the test above does.
    result = bidirectional_shortest_path(fwd, wrong_bwd, {"B"}, {"A"})
    assert result is not None, (
        "this demonstrates the bug scenario produces a path where none should "
        "exist -- confirming test_directed_edge_not_traversable_reverse (which "
        "uses the correct reverse_graph()) is actually discriminating"
    )


# ---------------------------------------------------------------------------
# THE key correctness test: a graph constructed so that the true shortest
# path's meeting point is an edge (A, B) where A only ever gets
# forward-settled and B only ever gets backward-settled -- a naive
# "same node settled by both sides" implementation converges on a
# DIFFERENT, suboptimal "distractor" path instead. Verified two ways:
# (1) our implementation must match the ground-truth forward Dijkstra,
# (2) a deliberately-naive reference implementation (defined right here,
# not in production code) must NOT match it, proving this graph actually
# discriminates between correct and incorrect implementations rather than
# just trivially passing regardless.
# ---------------------------------------------------------------------------

def _make_meeting_edge_graph():
    # True shortest path: S -A-> -B-> -T, total 1+1+1 = 3.
    # Distractor path: S -C-> -T, total 2+2 = 4, deliberately reaches a
    # single shared node (C) from both directions at equal cost so a
    # node-overlap-only rule finds and trusts it first.
    fwd = {
        "S": [("A", 1.0, "sa"), ("C", 2.0, "sc")],
        "A": [("B", 1.0, "ab")],
        "B": [("T", 1.0, "bt")],
        "C": [("T", 2.0, "ct")],
        "T": [],
    }
    return fwd, reverse_graph(fwd)


def _naive_bidirectional_node_overlap_only(forward_graph, backward_graph, sources, targets):
    """Deliberately-flawed reference implementation for this one test:
    updates mu ONLY when a node is settled by both sides (the classic bug),
    never checks whether a relaxed-to neighbor is already settled on the
    other side. Exists purely to prove _make_meeting_edge_graph() actually
    discriminates between this and the correct algorithm -- not something
    any production code should import."""
    import heapq

    INF = float("inf")
    distF, distB = {}, {}
    settledF, settledB = set(), set()
    heapF, heapB = [], []
    counter = 0
    for s in sources:
        distF[s] = 0.0
        heapq.heappush(heapF, (0.0, counter, s))
        counter += 1
    for t in targets:
        distB[t] = 0.0
        heapq.heappush(heapB, (0.0, counter, t))
        counter += 1

    mu = INF

    def relax(u, dist, heap, graph):
        nonlocal counter
        for v, w, _label in graph.get(u, []):
            nd = dist[u] + w
            if v not in dist or nd < dist[v] - 1e-9:
                dist[v] = nd
                heapq.heappush(heap, (nd, counter, v))
                counter += 1

    # Deliberately uses the SAME (correct) "while heapF or heapB", empty-heap
    # -contributes-0 loop as the real implementation -- the only thing this
    # reference is meant to isolate is node-overlap-only vs. edge-aware mu
    # checking, not the separate empty-heap bug that was fixed earlier. If
    # this also used the naive "while heapF and heapB" condition, a failure
    # here couldn't distinguish which of the two bugs was being exercised.
    while heapF or heapB:
        top_f = heapF[0][0] if heapF else 0.0
        top_b = heapB[0][0] if heapB else 0.0
        if top_f + top_b >= mu:
            break
        if heapF and (not heapB or top_f <= top_b):
            _, _, u = heapq.heappop(heapF)
            if u in settledF:
                continue
            settledF.add(u)
            if u in settledB:
                mu = min(mu, distF[u] + distB[u])
            relax(u, distF, heapF, forward_graph)
        else:
            _, _, u = heapq.heappop(heapB)
            if u in settledB:
                continue
            settledB.add(u)
            if u in settledF:
                mu = min(mu, distF[u] + distB[u])
            relax(u, distB, heapB, backward_graph)

    return None if mu == INF else mu


def test_meeting_edge_graph_ground_truth_is_three():
    """First confirm, independent of either bidirectional implementation,
    what the actual shortest path is (via the already-trusted
    forward-only dijkstra.shortest_path)."""
    fwd, _ = _make_meeting_edge_graph()
    total, path = shortest_path(fwd, {"S"}, {"T"})
    assert total == 3.0
    assert path == ["S", "A", "B", "T"]


def test_correct_bidirectional_finds_true_shortest_path():
    fwd, bwd = _make_meeting_edge_graph()
    result = bidirectional_shortest_path(fwd, bwd, {"S"}, {"T"})
    assert result is not None
    total, path = result
    assert total == 3.0, f"expected the true shortest path (3), got {total} -- meeting-via-edge case is broken"
    assert path[0] == "S" and path[-1] == "T"
    # Path must be a real walk in fwd with matching total weight.
    edge_weight = {(u, v): w for u, es in fwd.items() for v, w, _ in es}
    assert sum(edge_weight[(a, b)] for a, b in zip(path, path[1:])) == total


def test_naive_same_node_termination_would_be_wrong():
    """Proves the counterexample graph is discriminating: the naive
    node-overlap-only rule converges on the distractor path (4), not the
    true shortest path (3). If this assertion ever fails, the graph
    construction above no longer exercises the bug this test file exists
    to catch, and needs to be revisited -- it would mean the test above
    (test_correct_bidirectional_finds_true_shortest_path) could be passing
    for the wrong reasons."""
    fwd, bwd = _make_meeting_edge_graph()
    naive_result = _naive_bidirectional_node_overlap_only(fwd, bwd, {"S"}, {"T"})
    assert naive_result == 4.0, (
        f"expected the naive (buggy) implementation to converge on the distractor "
        f"path (4), got {naive_result} -- counterexample graph needs revisiting"
    )


# ---------------------------------------------------------------------------
# Cross-validation against trusted forward Dijkstra on many random graphs --
# the broadest, least hand-wavy correctness check: if these two algorithms
# ever disagree on a random graph, one of them has a bug.
# ---------------------------------------------------------------------------

def test_matches_forward_dijkstra_on_random_graphs():
    import random

    rng = random.Random(20260710)  # fixed seed -- reproducible, not flaky
    mismatches = []

    for trial in range(200):
        n = rng.randint(2, 12)
        nodes = [f"n{i}" for i in range(n)]
        fwd = {node: [] for node in nodes}
        edge_count = rng.randint(n, n * 3)
        for _ in range(edge_count):
            a, b = rng.sample(nodes, 2)
            w = round(rng.uniform(0.1, 20.0), 2)
            fwd[a].append((b, w, f"{a}-{b}"))
        bwd = reverse_graph(fwd)

        k_sources = rng.randint(1, min(3, n))
        k_targets = rng.randint(1, min(3, n))
        sources = set(rng.sample(nodes, k_sources))
        targets = set(rng.sample(nodes, k_targets))

        expected = shortest_path(fwd, sources, targets)
        actual = bidirectional_shortest_path(fwd, bwd, sources, targets)

        expected_total = expected[0] if expected else None
        actual_total = actual[0] if actual else None
        if expected_total is None or actual_total is None:
            if expected_total != actual_total:
                mismatches.append((trial, "reachability", expected, actual))
            continue
        if abs(expected_total - actual_total) > 1e-6:
            mismatches.append((trial, "distance", expected, actual))

    assert not mismatches, f"{len(mismatches)} mismatch(es) vs forward Dijkstra: {mismatches[:5]}"


def test_matches_forward_dijkstra_on_random_directed_graphs_with_oneways():
    """Same as above but every edge is one-directional only (no automatic
    reverse edge added) -- specifically exercises the directed-graph path,
    since undirected test graphs can't catch a forward/backward mixup."""
    import random

    rng = random.Random(99182337)
    mismatches = []

    for trial in range(200):
        n = rng.randint(3, 12)
        nodes = [f"n{i}" for i in range(n)]
        fwd = {node: [] for node in nodes}
        edge_count = rng.randint(n, n * 3)
        for _ in range(edge_count):
            a, b = rng.sample(nodes, 2)
            w = round(rng.uniform(0.1, 20.0), 2)
            fwd[a].append((b, w, f"{a}-{b}"))  # directed only, no reverse added
        bwd = reverse_graph(fwd)

        sources = {rng.choice(nodes)}
        targets = {rng.choice(nodes)}

        expected = shortest_path(fwd, sources, targets)
        actual = bidirectional_shortest_path(fwd, bwd, sources, targets)
        expected_total = expected[0] if expected else None
        actual_total = actual[0] if actual else None
        if expected_total is None or actual_total is None:
            if expected_total != actual_total:
                mismatches.append((trial, "reachability", sources, targets, expected, actual))
            continue
        if abs(expected_total - actual_total) > 1e-6:
            mismatches.append((trial, "distance", sources, targets, expected, actual))

    assert not mismatches, f"{len(mismatches)} mismatch(es) on directed graphs: {mismatches[:5]}"
