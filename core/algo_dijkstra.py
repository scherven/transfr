"""
Baseline algorithm: textbook multi-source Dijkstra with lazy (on-demand)
neighbor expansion via SearchContext.

This is the ground-truth reference every other algorithm in this package is
checked against. Dijkstra is provably optimal for non-negative edge
weights, so any other algorithm that disagrees with this one on a real
query has a bug in the new algorithm, not "a different valid answer".
"""

import heapq
import time
from typing import Any, Dict, List, Optional, Tuple

from search_context import SearchContext

DEFAULT_MAX_SEARCH_SECONDS = 1800.0  # 30 min of walking-time-equivalent cost


def search(
    ctx: SearchContext,
    max_search_seconds: float = DEFAULT_MAX_SEARCH_SECONDS,
    progress_cb=None,
) -> Dict[str, Any]:
    """max_search_seconds bounds runaway searches for genuinely disconnected
    platforms directly in the units that matter (walking time), not a
    straight-line proxy: since Dijkstra pops nodes in non-decreasing
    distance order, once the popped distance exceeds the bound we know
    every real path, if one exists at all, is already known to be
    infeasible, so stopping there costs nothing a feasibility check would
    have wanted anyway."""
    dist: Dict[int, float] = {}
    prev: Dict[int, Optional[int]] = {}
    prev_way: Dict[int, Optional[int]] = {}
    heap: List[Tuple[float, int, int]] = []
    counter = 0
    for s in ctx.sources:
        dist[s] = 0.0
        prev[s] = None
        prev_way[s] = None
        heapq.heappush(heap, (0.0, counter, s))
        counter += 1

    visited: set = set()
    expansions = 0
    t_start = time.monotonic()

    while heap:
        d, _, u = heapq.heappop(heap)
        if u in visited:
            continue
        if d > max_search_seconds:
            return ctx.build_not_found("exceeded_plausibility_bound", expansions, max_search_seconds=max_search_seconds)
        visited.add(u)
        expansions += 1
        if progress_cb and expansions % 200 == 0:
            progress_cb(expansions, len(ctx.way_cache), len(ctx.coord_cache), time.monotonic() - t_start)

        if u in ctx.targets:
            node_path = [u]
            while prev[node_path[-1]] is not None:
                node_path.append(prev[node_path[-1]])
            node_path.reverse()
            return ctx.build_result(node_path, prev_way, d, expansions)

        for v, w, way_id in ctx.neighbors(u):
            if v in visited:
                continue
            nd = d + w
            if v not in dist or nd < dist[v] - 1e-9:
                dist[v] = nd
                prev[v] = u
                prev_way[v] = way_id
                heapq.heappush(heap, (nd, counter, v))
                counter += 1

    return ctx.build_not_found("disconnected", expansions)
