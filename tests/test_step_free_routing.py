"""
Step-free routing (avoid_elevators): the accessibility guarantee that a walk with
``step_free=True`` never routes THROUGH a lift. The DB-backed smoke test
(test_walks.py) only asserts "a route was found", never that the elevator was
actually avoided -- so a regression that stopped threading ``avoid_elevators`` into
the search would stay green.

These exercise ``SearchContext.neighbors`` directly over a synthetic in-memory graph
(no DB: ``expand`` is stubbed and the caches are pre-populated), for BOTH places an
elevator can appear in OSM: a traversable elevator WAY (``highway=elevator``) and an
elevator mapped ON a shared node (the per-level "port" model). Each is checked with
avoid_elevators off (the lift is usable) and on (the lift is gone from the graph).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "pathfinding"))

from search_context import SearchContext  # noqa: E402


def _ctx(avoid_elevators, way_cache, node_to_ways, coord_cache, node_tags=None):
    """A SearchContext carrying only the fields ``neighbors`` reads, with ``expand``
    stubbed to a no-op so it never touches a DB (the caches are already populated).
    Mirrors the ``SearchContext.__new__`` bypass used in test_platform_tier2.py."""
    ctx = SearchContext.__new__(SearchContext)   # bypass __init__ (it needs a cursor)
    ctx.avoid_elevators = avoid_elevators
    ctx.way_cache = way_cache
    ctx.node_to_ways = node_to_ways
    ctx.coord_cache = coord_cache
    ctx.node_tags = node_tags or {}
    ctx.bridges = {}
    ctx.expand = lambda u: None                  # caches pre-populated; no DB fetch
    return ctx


def _targets(neighbors):
    """The set of neighbour vertices (dropping weight and way_id)."""
    return {v for v, _weight, _way in neighbors}


# ---------------------------------------------------------------------------
# Way-mapped elevator (highway=elevator): the whole way is untraversable step-free
# (search_context.py:778-779)
# ---------------------------------------------------------------------------

def _way_elevator_graph():
    # node 1 --footway--> 2   and   node 1 --elevator--> 3
    coord = {1: (52.5, 13.3), 2: (52.5, 13.3010), 3: (52.5, 13.3020)}
    ways = {
        10: {"nodes": [1, 2], "tags": {"highway": "footway"}},
        11: {"nodes": [1, 3], "tags": {"highway": "elevator"}},
    }
    node_to_ways = {1: {10, 11}, 2: {10}, 3: {11}}
    return ways, node_to_ways, coord


def test_way_elevator_is_traversable_normally():
    ways, n2w, coord = _way_elevator_graph()
    ctx = _ctx(False, ways, n2w, coord)
    assert _targets(ctx.neighbors(1)) == {2, 3}          # both the footway and the lift


def test_way_elevator_is_skipped_step_free():
    ways, n2w, coord = _way_elevator_graph()
    ctx = _ctx(True, ways, n2w, coord)
    # The elevator way (to node 3) is simply not in the graph; only the footway stays.
    assert _targets(ctx.neighbors(1)) == {2}


# ---------------------------------------------------------------------------
# Node-mapped elevator (a lift tagged on a shared node): no vertical edge step-free
# (search_context.py:817-820)
# ---------------------------------------------------------------------------

def _node_elevator_graph():
    # node 5 is a lift tagged on a shared node serving level 0 (footway to 6) and
    # level 1 (footway to 7). Its ports are (5, 0.0) and (5, 1.0).
    coord = {5: (52.5, 13.3), 6: (52.5, 13.3010), 7: (52.5010, 13.3)}
    ways = {
        20: {"nodes": [5, 6], "tags": {"highway": "footway", "level": "0"}},
        21: {"nodes": [5, 7], "tags": {"highway": "footway", "level": "1"}},
    }
    node_to_ways = {5: {20, 21}, 6: {20}, 7: {21}}
    node_tags = {5: {"highway": "elevator", "level": "0;1"}}
    return ways, node_to_ways, coord, node_tags


def test_node_elevator_offers_a_vertical_edge_normally():
    ways, n2w, coord, tags = _node_elevator_graph()
    ctx = _ctx(False, ways, n2w, coord, tags)
    # From the level-0 port: step onto the level-0 footway (node 6) AND ride the lift
    # up to the level-1 port.
    assert _targets(ctx.neighbors((5, 0.0))) == {6, (5, 1.0)}


def test_node_elevator_offers_no_vertical_edge_step_free():
    ways, n2w, coord, tags = _node_elevator_graph()
    ctx = _ctx(True, ways, n2w, coord, tags)
    # Step-free: the lift's vertical edge is withheld, so the level-1 port is
    # unreachable through this node -- only the same-level footway remains. This is
    # exactly what makes a lift-only interchange come back `disconnected` under
    # step_free instead of silently routing through the elevator.
    assert _targets(ctx.neighbors((5, 0.0))) == {6}
