"""
Generic multi-source shortest path search.

Deliberately plain: a min-heap and a distance dict, textbook Dijkstra, no
heuristics or bidirectional tricks. Correctness only depends on Dijkstra's
standard non-negative-edge-weight guarantee, which is easy to verify by
reading this file top to bottom.

Kept independent of the database/OSM types so it can be unit-tested against
small hand-built graphs (see tests/test_dijkstra.py) without a live connection.
"""

import heapq
from typing import Dict, Hashable, List, Optional, Set, Tuple

# graph[node] = [(neighbor, weight, edge_label), ...]
Graph = Dict[Hashable, List[Tuple[Hashable, float, object]]]


def shortest_path(
    graph: Graph,
    sources: Set[Hashable],
    targets: Set[Hashable],
) -> Optional[Tuple[float, List[Hashable]]]:
    """Shortest path from any node in *sources* (all starting at distance 0)
    to the nearest node in *targets*.

    Returns (total_weight, [node, node, ...]) from a source to the reached
    target (inclusive), or None if no target is reachable.

    Multi-source is implemented by seeding the heap with every source at
    distance 0 -- equivalent to adding a virtual node with zero-weight edges
    to each source, without materializing that node.
    """
    if not sources or not targets:
        return None

    dist: Dict[Hashable, float] = {}
    prev: Dict[Hashable, Optional[Hashable]] = {}
    heap: List[Tuple[float, int, Hashable]] = []
    counter = 0  # tie-breaker so heap never compares two node ids directly

    for s in sources:
        dist[s] = 0.0
        prev[s] = None
        heapq.heappush(heap, (0.0, counter, s))
        counter += 1

    visited: Set[Hashable] = set()

    while heap:
        d, _, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)

        if u in targets:
            return d, _reconstruct(prev, u)

        for v, w, _label in graph.get(u, []):
            if w < 0:
                raise ValueError(f"negative edge weight {w} not supported by Dijkstra")
            nd = d + w
            if v not in dist or nd < dist[v] - 1e-12:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, counter, v))
                counter += 1

    return None


def _reconstruct(prev: Dict[Hashable, Optional[Hashable]], target: Hashable) -> List[Hashable]:
    path = [target]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def all_simple_paths(
    graph: Graph,
    start: Hashable,
    ends: Set[Hashable],
    max_depth: int = 12,
) -> List[Tuple[float, List[Hashable]]]:
    """Exhaustive DFS enumeration of every simple path (no repeated nodes)
    from *start* to any node in *ends*, up to *max_depth* edges.

    This is the literal "brute force, try every path" method -- exponential,
    only tractable on small graphs. It exists purely to cross-check
    shortest_path()'s correctness independently in tests, not for production
    use (see tests/test_dijkstra.py).
    """
    results: List[Tuple[float, List[Hashable]]] = []
    visited = {start}
    path = [start]

    def dfs(node: Hashable, cost: float) -> None:
        if node in ends:
            results.append((cost, list(path)))
        if len(path) - 1 >= max_depth:
            return
        for neighbor, weight, _label in graph.get(node, []):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            path.append(neighbor)
            dfs(neighbor, cost + weight)
            path.pop()
            visited.remove(neighbor)

    dfs(start, 0.0)
    return results
