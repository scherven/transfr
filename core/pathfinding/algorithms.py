"""
Registry of pathfinding algorithms.

Every algorithm takes a search_context.SearchContext (already resolved:
sources/targets known, lazy neighbor access ready) plus algorithm-specific
kwargs, and returns the same result-dict shape (see SearchContext.build_result
/ build_not_found). That shared contract is what makes it possible to run
the identical test fixtures and benchmark harness against any of them.

"dijkstra" is the ground-truth baseline (see core/algo_dijkstra.py) --
provably optimal, so it's what every other algorithm's output gets checked
against. Add a new algorithm by writing core/algo_<name>.py with a
search(ctx, **kwargs) function and registering it here.
"""

import algo_astar
import algo_dijkstra

ALGORITHMS = {
    "dijkstra": algo_dijkstra.search,
    "astar": algo_astar.search,
}

BASELINE = "dijkstra"


def register(name: str, search_fn) -> None:
    ALGORITHMS[name] = search_fn
