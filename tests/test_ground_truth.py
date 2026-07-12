"""
Integration tests for core/ground_truth.py against real loaded OSM data.

Requires the transfr_eu database to be populated (see core/extract_europe.sh
and core/etl.py). find_shortest_path() is the lazy, on-demand version -- it
fetches graph structure from the database only as the search actually
reaches each node, so even large stations resolve in a couple of seconds
(see the module docstring in core/ground_truth.py for why the earlier eager
"load a neighborhood, then search" version was impractical: ~17 minutes for
Berlin Hauptbahnhof vs. ~1.5 seconds here for the same query).

Run with:
    pytest tests/test_ground_truth.py -v -s
(-s so the hand-verification report prints instead of being captured)
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from algorithms import ALGORITHMS, BASELINE  # noqa: E402
from db import connect  # noqa: E402
from dijkstra import all_simple_paths  # noqa: E402
from graph import WALKING_SPEED_MS, build_time_weighted_graph, load_station_ways  # noqa: E402
from ground_truth import find_platform_edges, find_shortest_path  # noqa: E402
from report import format_verification_report  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    c = connect()
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Test stations -- picked for diversity of country, size, and platform-ref
# naming convention. Confirmed against the live database via one-off probes
# before being hardcoded here -- these are not guesses:
#
#   Strasbourg-Ville (FR)   rel=5347313   numeric refs 1-9,25,30-32, mid-size
#                                         the original CLI example from the
#                                         old system (test.py's __main__)
#   Berlin Hbf (DE)         rel=5688517   numeric refs 1-8,11-16, large
#                                         multi-level multi-modal hub --
#                                         kept in specifically as the "large,
#                                         dense city-center station" case
#   Colmar (FR)             rel=6365739   LETTER refs A-E, small station
#   Basel SBB, wing (CH)    rel=4272361   LETTER refs F-H (this relation
#                                         covers one platform group of the
#                                         larger Basel SBB complex, which
#                                         OSM splits across several
#                                         stop_area relations)
#
# (label, relation_id, ref_1, ref_2, max_plausible_seconds)
TRANSFER_CASES = [
    ("Strasbourg-Ville 1->7", 5347313, "1", "7", 600),
    ("Strasbourg-Ville 1->2", 5347313, "1", "2", 240),
    ("Strasbourg-Ville 3->3 (same platform)", 5347313, "3", "3", 60),
    ("Berlin Hbf 1->2", 5688517, "1", "2", 300),
    ("Berlin Hbf 1->16 (far apart)", 5688517, "1", "16", 900),
    ("Colmar A->B (letter refs)", 6365739, "A", "B", 300),
    ("Basel SBB F->H (letter refs)", 4272361, "F", "H", 400),
]


@pytest.mark.parametrize("label,rel_id,ref_1,ref_2,max_s", TRANSFER_CASES, ids=[c[0] for c in TRANSFER_CASES])
def test_transfer_found_and_plausible(conn, label, rel_id, ref_1, ref_2, max_s):
    result = find_shortest_path(conn, rel_id, ref_1, ref_2)
    assert result["found"], f"{label}: expected a path, got reason={result.get('reason')}"
    # Same-platform transfers are legitimately zero-distance (you're already there).
    if ref_1 != ref_2:
        assert result["walking_time_seconds"] > 0
        assert result["walking_distance_meters"] > 0
    assert result["walking_time_seconds"] <= max_s, (
        f"{label}: {result['walking_time_seconds']}s exceeds plausibility bound {max_s}s "
        f"-- likely a routing bug, not just a long transfer"
    )
    print("\n" + format_verification_report(label, ref_1, ref_2, result))


def test_colmar_platform_e_correctly_disconnected(conn):
    """Colmar's platform D-E ("Quai D-E") is mapped in OSM as a closed
    polygon (way 53506915) whose only connections are to its own two
    platform_edge ways (D and E) -- verified by hand via direct SQL, not
    just this test: no footway/steps/corridor touches any of its nodes.
    There is, today, no pedestrian path in the source data from any other
    Colmar platform to E. find_shortest_path() must report that honestly
    (reason="disconnected") rather than fabricate a route or silently
    return something misleading -- this is the correct behavior for a
    ground-truth algorithm operating on real, imperfect map data, so it's
    asserted here rather than treated as a bug."""
    result = find_shortest_path(conn, 6365739, "A", "E")
    assert not result["found"]
    assert result["reason"] == "disconnected"


def test_berlin_1_16_charges_node_mapped_vertical_cost(conn):
    """Regression for ISSUE-node-vertical-cost.md: Berlin Hbf 1->16 climbs
    several floors through elevators that OSM maps ON a shared node
    (highway=elevator, e.g. node 742238019, level=-2;-1;0;1;2), not as a
    traversable way. The old purely-2D graph made those level changes free, so
    the transfer came back at 63.6 s -- exactly its pure-horizontal walking
    time (89 m / 1.4 m/s), i.e. no time charged for four floors of climbing.

    Now that node-mapped vertical circulation is priced, the transfer must cost
    strictly (and meaningfully) more than the pure-horizontal walking time of
    whatever route it takes. If this ever drops back to ~= horizontal time,
    vertical circulation is being walked through for free again."""
    result = find_shortest_path(conn, 5688517, "1", "16")
    assert result["found"], f"expected a path, got reason={result.get('reason')}"
    horizontal_only_s = result["walking_distance_meters"] / WALKING_SPEED_MS
    assert result["walking_time_seconds"] > horizontal_only_s + 10.0, (
        f"Berlin 1->16 reported {result['walking_time_seconds']}s but its distance "
        f"({result['walking_distance_meters']}m) is only {horizontal_only_s:.1f}s of pure "
        f"horizontal walking -- a multi-floor transfer through node-mapped elevators is "
        f"being costed as if the vertical moves were free (see ISSUE-node-vertical-cost.md)."
    )


@pytest.mark.parametrize(
    "label,rel_id,ref_1,ref_2,max_s",
    [c for c in TRANSFER_CASES if c[2] != c[3]],
    ids=[c[0] for c in TRANSFER_CASES if c[2] != c[3]],
)
def test_transfer_symmetry(conn, label, rel_id, ref_1, ref_2, max_s):
    """Walking time A->B should equal B->A -- the graph is undirected except
    for forward/backward escalators and explicit oneway ways, and none of
    these fixtures are expected to route through one."""
    fwd = find_shortest_path(conn, rel_id, ref_1, ref_2)
    rev = find_shortest_path(conn, rel_id, ref_2, ref_1)
    assert fwd["found"] and rev["found"], f"{label}: one direction found a path and the other didn't"
    t_fwd, t_rev = fwd["walking_time_seconds"], rev["walking_time_seconds"]
    assert abs(t_fwd - t_rev) / max(t_fwd, t_rev, 1) < 0.05, (
        f"{label}: forward {t_fwd}s vs reverse {t_rev}s differ by more than 5%"
    )


@pytest.mark.parametrize("label,rel_id,ref_1,ref_2,max_s", TRANSFER_CASES, ids=[c[0] for c in TRANSFER_CASES])
def test_way_path_is_contiguous(conn, label, rel_id, ref_1, ref_2, max_s):
    """Every consecutive pair of nodes in node_path must actually be an edge
    that get_shortest_path()'s own search used -- reconstructed here from
    the returned way_path rather than re-deriving a separate graph, so this
    checks internal consistency of the result, not just plausibility."""
    result = find_shortest_path(conn, rel_id, ref_1, ref_2)
    assert result["found"]
    node_path = result["node_path"]
    way_ids_in_path = set(result["way_path"])
    # Re-fetch just those ways (cheap -- a handful of ids) and confirm each
    # hop is a real consecutive pair within one of them.
    with conn.cursor() as cur:
        cur.execute("SELECT id, nodes FROM osm_ways WHERE id = ANY(%s)", (list(way_ids_in_path),))
        way_nodes = {row["id"]: list(row["nodes"]) for row in cur.fetchall()}
    for a, b in zip(node_path, node_path[1:]):
        found_edge = any(
            (a in nodes and b in nodes and abs(nodes.index(a) - nodes.index(b)) == 1)
            for nodes in way_nodes.values()
        )
        assert found_edge, f"{label}: node {a} -> {b} is not a real consecutive pair in any way on the path"


def test_unknown_platform_ref_reports_not_found(conn):
    """A ref that doesn't exist at the station must come back as
    'platform_not_found', not silently match something else or crash."""
    result = find_shortest_path(conn, 5347313, "1", "99999")
    assert not result["found"]
    assert result["reason"] == "platform_not_found"
    assert result["ref_2_matches"] == 0


def test_letter_ref_unknown_at_numeric_station(conn):
    """A letter ref queried against a station that only has numeric refs
    must not accidentally string-match something -- this guards the
    str(ref) comparison in find_platform_edges()."""
    result = find_shortest_path(conn, 5688517, "1", "Z")  # Berlin has no "Z"
    assert not result["found"]
    assert result["reason"] == "platform_not_found"


def test_eager_and_lazy_agree(conn):
    """Cross-check the lazy (on-demand) search against the original eager
    (load-then-search) implementation on a small station -- same algorithm,
    different data access pattern, must give the same answer. Restricted to
    Colmar since the eager version is impractically slow on larger stations
    (that's the whole reason the lazy version exists)."""
    from ground_truth import find_shortest_path_eager

    lazy_result = find_shortest_path(conn, 6365739, "A", "B")
    eager_result = find_shortest_path_eager(conn, 6365739, "A", "B")
    assert lazy_result["found"] and eager_result["found"]
    assert lazy_result["walking_time_seconds"] == eager_result["walking_time_seconds"]
    assert lazy_result["walking_distance_meters"] == eager_result["walking_distance_meters"]


def test_brute_force_all_paths_agrees_with_dijkstra(conn):
    """Cross-check on a small, real station: literally enumerate every
    simple path (the "brute-forcing paths until you find a connecting path"
    approach) and confirm its minimum matches what find_shortest_path()
    (Dijkstra) returns. Restricted to Colmar, and to a shallow max_depth --
    all-paths enumeration is exponential in the number of cycles in the
    graph, not just its size, so even Colmar's small closure is intractable
    at a large depth. Dijkstra's own search reached A->B in ~14 expansions,
    so depth 12 is already generous slack for the true shortest path while
    keeping the enumeration fast; this test is a sanity cross-check on the
    algorithm, not a substitute for the (more important) direct
    find_shortest_path() tests above."""
    rel_id = 6365739
    ways, coords = load_station_ways(conn, rel_id)
    graph = build_time_weighted_graph(ways, coords)

    edges_1 = find_platform_edges(ways, "A")
    edges_2 = find_platform_edges(ways, "B")
    assert edges_1 and edges_2, "fixture assumption broken: Colmar should have platform edges A and B"

    sources = {n for _, nodes in edges_1 for n in nodes if n in coords}
    start = next(iter(sources))
    targets = {n for _, nodes in edges_2 for n in nodes if n in coords}

    brute_force_results = all_simple_paths(graph, start, targets, max_depth=12)
    assert brute_force_results, "brute-force enumeration found no path at all -- fixture or graph problem"
    brute_force_best = min(r[0] for r in brute_force_results)

    dijkstra_result = find_shortest_path(conn, rel_id, "A", "B")
    assert dijkstra_result["found"]
    # Dijkstra started from *any* node of edge 1; brute force started from
    # exactly one, so Dijkstra's answer can only be <= the brute-force one
    # for that single fixed start node.
    assert dijkstra_result["walking_time_seconds"] <= brute_force_best + 1e-6, (
        f"Dijkstra found {dijkstra_result['walking_time_seconds']}s, "
        f"but brute-force enumeration from the same-ish start found a shorter {brute_force_best}s -- "
        f"Dijkstra implementation bug"
    )


# ---------------------------------------------------------------------------
# Cross-algorithm correctness: every algorithm registered in algorithms.py
# gets checked against TRANSFER_CASES and must agree with the "dijkstra"
# baseline. This is what makes it safe to iterate on faster algorithms --
# add a new one to algorithms.ALGORITHMS and it's automatically covered
# here without touching this file.
# ---------------------------------------------------------------------------

_NON_BASELINE_ALGORITHMS = [name for name in ALGORITHMS if name != BASELINE]


@pytest.mark.skipif(not _NON_BASELINE_ALGORITHMS, reason="no non-baseline algorithms registered yet")
@pytest.mark.parametrize("algorithm", _NON_BASELINE_ALGORITHMS)
@pytest.mark.parametrize("label,rel_id,ref_1,ref_2,max_s", TRANSFER_CASES, ids=[c[0] for c in TRANSFER_CASES])
def test_algorithm_agrees_with_baseline(conn, label, rel_id, ref_1, ref_2, max_s, algorithm):
    baseline = find_shortest_path(conn, rel_id, ref_1, ref_2, algorithm=BASELINE)
    candidate = find_shortest_path(conn, rel_id, ref_1, ref_2, algorithm=algorithm)
    assert candidate["found"] == baseline["found"], (
        f"{label} [{algorithm}]: found={candidate['found']} but baseline found={baseline['found']}"
    )
    if not baseline["found"]:
        return
    assert candidate["walking_time_seconds"] == pytest.approx(baseline["walking_time_seconds"], rel=1e-6), (
        f"{label} [{algorithm}]: time {candidate['walking_time_seconds']}s != "
        f"baseline {baseline['walking_time_seconds']}s"
    )
    assert candidate["walking_distance_meters"] == pytest.approx(baseline["walking_distance_meters"], rel=1e-6), (
        f"{label} [{algorithm}]: distance {candidate['walking_distance_meters']}m != "
        f"baseline {baseline['walking_distance_meters']}m"
    )


@pytest.mark.skipif(not _NON_BASELINE_ALGORITHMS, reason="no non-baseline algorithms registered yet")
def test_algorithm_disconnected_case_agrees(conn):
    """Every algorithm must also agree on the known-disconnected Colmar
    A->E case (see test_colmar_platform_e_correctly_disconnected) --
    reporting a path where none should exist is a correctness bug, not a
    speed win."""
    for name, search_fn in ALGORITHMS.items():
        result = find_shortest_path(conn, 6365739, "A", "E", algorithm=name)
        assert not result["found"], f"[{name}] incorrectly found a path for the known-disconnected Colmar A->E case"


# ---------------------------------------------------------------------------
# node_way_ids adjacency table: an algorithm-independent speedup to
# SearchContext.expand() (point lookups instead of a GIN bitmap-heap-scan),
# on by default. Every TRANSFER_CASE must produce an identical answer
# whichever expansion path is used -- this table can only ever change
# performance, never the result, so any disagreement here is a bug in the
# adjacency-table path (most likely: node_way_ids is stale relative to
# osm_ways -- rerun core/build_node_way_ids.py).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,rel_id,ref_1,ref_2,max_s", TRANSFER_CASES, ids=[c[0] for c in TRANSFER_CASES])
def test_adjacency_table_agrees_with_gin_scan(conn, label, rel_id, ref_1, ref_2, max_s):
    gin_result = find_shortest_path(conn, rel_id, ref_1, ref_2, use_adjacency_table=False)
    adj_result = find_shortest_path(conn, rel_id, ref_1, ref_2, use_adjacency_table=True)
    assert adj_result["found"] == gin_result["found"], (
        f"{label}: adjacency-table found={adj_result['found']} but GIN-scan found={gin_result['found']}"
    )
    if not gin_result["found"]:
        return
    assert adj_result["walking_time_seconds"] == pytest.approx(gin_result["walking_time_seconds"], rel=1e-6), (
        f"{label}: adjacency-table time {adj_result['walking_time_seconds']}s != "
        f"GIN-scan time {gin_result['walking_time_seconds']}s -- node_way_ids may be stale"
    )


def test_adjacency_table_agrees_on_disconnected_case(conn):
    gin_result = find_shortest_path(conn, 6365739, "A", "E", use_adjacency_table=False)
    adj_result = find_shortest_path(conn, 6365739, "A", "E", use_adjacency_table=True)
    assert not gin_result["found"] and not adj_result["found"]


def test_benchmark_report(conn, capsys):
    """Not a correctness test -- prints a timing comparison table across
    every registered algorithm so a speed win/regression is visible in test
    output (run with -s to see it)."""
    if len(ALGORITHMS) < 2:
        pytest.skip("only one algorithm registered -- nothing to compare yet")
    print(f"\n{'case':40s} " + " ".join(f"{name:>14s}" for name in ALGORITHMS))
    for label, rel_id, ref_1, ref_2, _ in TRANSFER_CASES:
        row = [f"{label:40s}"]
        for name in ALGORITHMS:
            t0 = time.monotonic()
            find_shortest_path(conn, rel_id, ref_1, ref_2, algorithm=name)
            row.append(f"{time.monotonic() - t0:>13.3f}s")
        print(" ".join(row))
