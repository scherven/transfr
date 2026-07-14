"""
A* search: Dijkstra with a haversine/max-speed admissible heuristic that
biases expansion toward the target, so nodes that are geometrically the
wrong way get explored later (or not at all) instead of purely in
distance-from-source order.

Provably optimal, same as Dijkstra -- not an approximation:

    h(u) = min over targets of haversine_meters(u, target) / WALKING_SPEED_MS

never overestimates the true remaining walking-time cost, because no edge
in this graph is ever faster than WALKING_SPEED_MS (steps/escalators/
elevators are all slower or equal -- see graph.py's way_speed_and_penalty).
That makes h admissible; because it's derived from a real metric (great-
circle distance), it's also consistent. Both are what A* needs to guarantee
it finds the same optimal answer as Dijkstra, not just "usually right."

Multi-target: h(u) takes the min over every target node, which still never
overestimates the cost to the *nearest* target -- the only one the search
actually needs to reach. Multi-source needs no special handling: sources are
just seeded at g=0 with their own h(), exactly like Dijkstra seeds them at
dist=0. Directed edges (oneway/forward-backward escalators) don't affect
admissibility either -- the heuristic only lower-bounds cost along *some*
legal path; it doesn't need to know which edges are legal to traverse.

Every node this doesn't have to pop is one Postgres round trip never made
(see SearchContext.expand()) -- that's the whole motivation: cut the number
of *distinct nodes visited*, which the search_context.py benchmarks show is
the dominant real-world cost, not query complexity per node.
"""

import heapq
import time
from typing import Any, Dict, List, Optional, Tuple

from graph import WALKING_SPEED_MS, haversine_meters, node_id_of
from search_context import SearchContext

DEFAULT_MAX_SEARCH_SECONDS = 1800.0


def search(
    ctx: SearchContext,
    max_search_seconds: float = DEFAULT_MAX_SEARCH_SECONDS,
    progress_cb=None,
) -> Dict[str, Any]:
    target_coords: List[Tuple[float, float]] = [
        ctx.coord_cache[t] for t in ctx.targets if t in ctx.coord_cache
    ]

    def h(u) -> float:
        # Coordinates are guaranteed known for any node neighbors() ever
        # yields (it's a precondition of the yield itself -- see
        # SearchContext.neighbors()), and for every source (resolved during
        # SearchContext setup) -- so this is never a guess in practice. u may
        # be a plain node id or a (node, level) port; both share the node's
        # coord, and the horizontal heuristic stays admissible because a
        # vertical edge adds only non-negative cost at zero horizontal progress.
        nid = node_id_of(u)
        if nid not in ctx.coord_cache or not target_coords:
            return 0.0
        lat, lon = ctx.coord_cache[nid]
        return min(haversine_meters(lat, lon, tlat, tlon) for tlat, tlon in target_coords) / WALKING_SPEED_MS

    dist: Dict[int, float] = {}
    prev: Dict[int, Optional[int]] = {}
    prev_way: Dict[int, Optional[int]] = {}
    heap: List[Tuple[float, int, int]] = []  # (g + h, tie-breaker, node)
    counter = 0
    for s in ctx.sources:
        dist[s] = 0.0
        prev[s] = None
        prev_way[s] = None
        heapq.heappush(heap, (h(s), counter, s))
        counter += 1

    # Give up past whichever is tighter: the caller's compute budget, or the
    # geometry-derived bound on a plausible transfer (rejects a ref that resolved
    # to a wrong, far-away feature -- see SearchContext.plausibility_bound_seconds).
    cutoff = min(max_search_seconds, ctx.plausibility_bound_seconds())

    visited: set = set()
    expansions = 0
    t_start = time.monotonic()

    while heap:
        _f, _, u = heapq.heappop(heap)
        if u in visited:
            continue
        # A* pops in f-order and f = g + h lower-bounds the cost of any path to a
        # target through u, so once the cheapest frontier f exceeds the cutoff, no
        # path within it exists -- give up. Bounding on f (not g) means we don't
        # sweep the whole cutoff-radius disc, just the wedge aimed at the target.
        if _f > cutoff:
            return ctx.build_not_found(
                "exceeded_plausibility_bound", expansions, max_search_seconds=max_search_seconds
            )
        g = dist[u]
        visited.add(u)
        expansions += 1
        if progress_cb and expansions % 200 == 0:
            progress_cb(expansions, len(ctx.way_cache), len(ctx.coord_cache), time.monotonic() - t_start)

        if u in ctx.targets:
            node_path = [u]
            while prev[node_path[-1]] is not None:
                node_path.append(prev[node_path[-1]])
            node_path.reverse()
            return ctx.build_result(node_path, prev_way, g, expansions)

        for v, w, way_id in ctx.neighbors(u):
            if v in visited:
                continue
            ng = g + w
            if v not in dist or ng < dist[v] - 1e-9:
                dist[v] = ng
                prev[v] = u
                prev_way[v] = way_id
                heapq.heappush(heap, (ng + h(v), counter, v))
                counter += 1

    return ctx.build_not_found("disconnected", expansions)
