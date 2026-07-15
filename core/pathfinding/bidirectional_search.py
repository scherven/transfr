"""
Pure, database-independent bidirectional Dijkstra.

Deliberately separated from any DB-backed wiring (see core/algo_bidirectional.py,
added only after this module's tests pass) so the tricky part -- the
termination condition and directed-edge handling -- can be rigorously
tested against synthetic graphs and cross-checked against the
already-trusted dijkstra.shortest_path() before anything touches real data.

THE COMMON BUG THIS AVOIDS: naive bidirectional search stops "as soon as
some node has been visited by both searches" and returns
distF[node] + distB[node]. This is WRONG -- it can return a suboptimal
path, because the true shortest path's meeting point is frequently an EDGE
whose two endpoints are settled on *different* sides, never a single node
settled on both. See test_bidirectional_search.py's
test_correct_bidirectional_finds_true_shortest_path for a constructed graph
where this actually happens.

A SECOND, subtler bug (found via 200-random-graph cross-validation against
dijkstra.shortest_path(), not by inspection) is fixed here too: gating the
cross-side check on `settled` sets (only fully-processed nodes) instead of
the FULL tentative distance dicts. `topF + topB >= mu` is only a valid
"nothing left can improve on mu" bound when topF/topB describe nodes not
yet known *at all* on their side. If a node is already fully settled on
ONE side but still sitting, unprocessed, in the OTHER side's queue at a
cheap tentative distance (e.g. it's a source that's also a target, or just
generically close to both endpoints), that pending entry is a "free"
improvement the settled-only check can't see -- topF/topB then describe
some *unrelated*, more expensive node, and termination can fire before the
cheap match is ever checked. Concretely: sources={A}, A is also a target
-> the correct answer is 0, but a settled-only implementation can converge
on and confidently report a longer, wrong distance instead.

Fix: check cross-side existence against the full `distF`/`distB` dicts
(tentative OR final), not just `settledF`/`settledB`, and do it (a) at
initialization for direct source/target overlap, and (b) on *every* edge
relaxation, regardless of whether that relaxation improves the neighbor's
own best-known distance. This is safe because a tentative Dijkstra
distance is always an upper bound on the true shortest distance (it only
ever decreases before settling) -- using one in a candidate can produce an
upper bound that isn't yet the tightest, never an incorrect too-low value.
It also fully subsumes the old "settled node matches settled node" check as
a special case: when a relaxation is the one that first establishes a
node's distance, the candidate computed from that same relaxation equals
exactly what the settled-vs-settled check would have computed later.

Termination: track `mu`, the best complete source-to-target path length
found so far (initialized via any direct source/target overlap, then
tightened by every qualifying relaxation above); stop alternating once
`topF + topB >= mu`, treating an exhausted (empty) side's contribution as
0, not infinity (see the loop for why: once a side is fully drained, its
distances are final and every future check against it is already correct,
so it isn't "still owed" anything, but it also isn't a live obstacle -- the
other side should just keep draining until it, too, is exhausted or mu is
proven tight). This is the standard, provably-correct termination rule for
bidirectional Dijkstra (see e.g. Goldberg & Harrelson, "Computing the
Shortest Path: A* Search Meets Graph Theory").
"""

import heapq
from typing import Dict, Hashable, List, Optional, Set, Tuple

from dijkstra import Graph

INF = float("inf")


def reverse_graph(graph: Graph) -> Graph:
    """Build the transpose of a directed graph: for every (u, v, w, label)
    edge, the result has a (v, u, w, label) edge. Used by tests and by
    callers that can afford to materialize the whole graph; the DB-backed
    version instead implements reverse traversal directly (see
    core/algo_bidirectional.py's reverse_neighbors)."""
    reversed_graph: Graph = {}
    for u, edges in graph.items():
        for v, w, label in edges:
            reversed_graph.setdefault(v, []).append((u, w, label))
    return reversed_graph


def bidirectional_shortest_path(
    forward_graph: Graph,
    backward_graph: Graph,
    sources: Set[Hashable],
    targets: Set[Hashable],
) -> Optional[Tuple[float, List[Hashable]]]:
    """Shortest path from any node in *sources* to any node in *targets*.

    backward_graph MUST be the transpose of forward_graph (see
    reverse_graph()) -- every directed edge u->v in forward_graph must
    appear as v->u in backward_graph, same weight and label. Getting this
    wrong silently produces a bidirectional search over the wrong graph;
    there is no way to detect that from inside this function, which is
    exactly why the DB-backed version's reverse_neighbors() needs its own
    dedicated tests (see core/algo_bidirectional.py).

    Returns (total_weight, [node, node, ...]) or None if unreachable.
    """
    if not sources or not targets:
        return None

    distF: Dict[Hashable, float] = {}
    distB: Dict[Hashable, float] = {}
    prevF: Dict[Hashable, Optional[Hashable]] = {}
    prevB: Dict[Hashable, Optional[Hashable]] = {}
    settledF: Set[Hashable] = set()
    settledB: Set[Hashable] = set()
    heapF: List[Tuple[float, int, Hashable]] = []
    heapB: List[Tuple[float, int, Hashable]] = []
    counter = 0

    mu = INF
    # (edge_u, edge_v): a real forward-direction edge (edge_u -> edge_v) in
    # the ORIGINAL graph such that source ->...-> edge_u -> edge_v ->...-> target
    # achieves `mu`. The trivial "meeting at a single node x" case is just
    # edge_u == edge_v == x (a zero-length "self edge") -- reconstruction
    # below dedupes that instead of needing a second code path.
    meeting_edge: Optional[Tuple[Hashable, Hashable]] = None

    def consider(edge_u: Hashable, edge_v: Hashable, edge_u_dist: float, w: float, edge_v_dist: float) -> None:
        nonlocal mu, meeting_edge
        candidate = edge_u_dist + w + edge_v_dist
        if candidate < mu - 1e-9:
            mu = candidate
            meeting_edge = (edge_u, edge_v)

    for s in sources:
        distF[s] = 0.0
        prevF[s] = None
        heapq.heappush(heapF, (0.0, counter, s))
        counter += 1
    for t in targets:
        distB[t] = 0.0
        prevB[t] = None
        heapq.heappush(heapB, (0.0, counter, t))
        counter += 1

    # Direct source/target overlap: distance 0 via a trivial self-edge.
    # Must be checked explicitly and up front -- it does NOT reliably fall
    # out of the queue-driven checks below, because queue processing order
    # (which side happens to pop first) can let a more expensive, unrelated
    # candidate set `mu` before a shared node's own pending queue entry is
    # ever reached (see module docstring).
    for x in sources & targets:
        consider(x, x, 0.0, 0.0, 0.0)

    def relax_side(u, dist, prev, heap, graph, other_dist, is_forward: bool) -> None:
        nonlocal counter
        for v, w, _label in graph.get(u, []):
            # Cross-side check first, using dist[u] (final -- u is only
            # ever relaxed from once settled) and w and other_dist.get(v)
            # (tentative-or-final, if v is known there AT ALL) --
            # deliberately independent of whether this specific relaxation
            # improves v's own best-known distance on side `dist`.
            if v in other_dist:
                if is_forward:
                    consider(u, v, dist[u], w, other_dist[v])
                else:
                    # backward_graph[u] containing (v, w, ...) means the
                    # ORIGINAL forward graph has edge v -> u, weight w.
                    consider(v, u, other_dist[v], w, dist[u])
            nd = dist[u] + w
            if v not in dist or nd < dist[v] - 1e-9:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, counter, v))
                counter += 1

    # See module docstring for why the loop condition is `or` (not `and`)
    # and why an empty heap contributes 0 (not infinity) to the sum: once a
    # side is fully drained, its distances are final and every future
    # settle/relax on the *other* side already checks against them, so
    # draining the non-empty side is both safe and necessary, and the
    # exhausted side isn't blocking anything by contributing 0.
    while heapF or heapB:
        top_f = heapF[0][0] if heapF else 0.0
        top_b = heapB[0][0] if heapB else 0.0
        if top_f + top_b >= mu:
            break

        if heapF and (not heapB or top_f <= top_b):
            d, _, u = heapq.heappop(heapF)
            if u in settledF:
                continue
            settledF.add(u)
            relax_side(u, distF, prevF, heapF, forward_graph, distB, is_forward=True)
        else:
            d, _, u = heapq.heappop(heapB)
            if u in settledB:
                continue
            settledB.add(u)
            relax_side(u, distB, prevB, heapB, backward_graph, distF, is_forward=False)

    if mu == INF:
        return None

    u, v = meeting_edge  # type: ignore[misc]
    forward_part: List[Hashable] = []
    cur = u
    while cur is not None:
        forward_part.append(cur)
        cur = prevF[cur]
    forward_part.reverse()  # [source, ..., u]

    backward_part: List[Hashable] = []
    cur = v
    while cur is not None:
        backward_part.append(cur)
        cur = prevB[cur]
    # [v, ..., target]

    full_path = forward_part + (backward_part[1:] if u == v else backward_part)
    return mu, full_path
