"""
Tests for the level-aware isolated-platform snap (core/search_context).

Some real platforms resolve to an OSM *island* polygon: a platform surface that
shares no node with the footway network (Bruxelles-Midi platform 1, a ~570 m
level-1 elevated platform; Koeln's 'A' wing). A* from such a platform returns
`disconnected` even though a walkable way lies metres away -- just unjoined. The
snap bridges the island to the nearest walkable way, under two guards that keep
the join real rather than a teleport:

  * LEVEL   -- the nearest way is often a lower-level concourse footway ~4 m off;
               snapping to it would fabricate a cross-level step with no stairs.
               The snap requires a way on the platform's own level (pf 1 = level 1;
               its real same-level join is ~19 m out).
  * CONNECTED -- pf 1's very nearest same-level neighbour is itself an island
               (shares no node with the concourse either), so the snap skips it
               and takes the nearest same-level way that actually reaches the
               network.

All DB-gated (TRANSFR_DB=1). These assert the recovery is real (Bruxelles pf 1
now routes, level-correctly), honest (only-off-level stays `disconnected`), and
strictly additive (a connected pair is untouched; a multi-way island -- the
stitch-bridge domain -- is not over-reached).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from db import connect  # noqa: E402
from graph import haversine_meters  # noqa: E402
from ground_truth import find_shortest_path  # noqa: E402
from search_context import (  # noqa: E402
    STOP_SNAP_RADIUS_M,
    SearchContext,
    _nearest_walkable_way,
    _PEDESTRIAN_SNAP_WAY_SQL,
)

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs transfr_eu with osm_nodes coord index; set TRANSFR_DB=1",
)

# Bruxelles-Midi - Brussel-Zuid (stop_area relation). Platform 1 is way 116006718,
# a level=1 island polygon ~570 m long that shares no node with the concourse. The
# nearest same-level way that is itself connected to the network is way 116005554,
# ~19 m off (the real join). Verified against the live DB.
BXL_MIDI = 6261364
PF1_WAY = 116006718
PF1_SNAP_WAY = 116005554

# A point ON platform 1 where the UNGUARDED nearest walkable way is a level-0
# footway ~8 m off (the fake teleport), while the nearest level-1 way is ~13 m off.
PF1_POINT = (50.836741, 4.3354475)

# Colmar (FR): platform D-E is a closed polygon joined only to its own two
# platform_edge ways -- a multi-way island, the stitch-bridge domain, NOT a bare
# single-polygon island. The snap must not touch it, so A->E stays disconnected.
COLMAR = 6365739


@pytest.fixture(scope="module")
def conn():
    c = connect()
    yield c
    c.close()


def _min_join_distance(cur, way_a: int, way_b: int) -> float:
    """Nearest-node distance in metres between two ways' geometries -- how far the
    snap actually bridged."""
    coords = {}
    for wid in (way_a, way_b):
        cur.execute("SELECT nodes FROM osm_ways WHERE id = %s", (wid,))
        ns = cur.fetchone()["nodes"]
        cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (ns,))
        coords[wid] = [(r["lat"], r["lon"]) for r in cur.fetchall()]
    return min(haversine_meters(a[0], a[1], b[0], b[1])
               for a in coords[way_a] for b in coords[way_b])


def _way_level(cur, way_id: int):
    cur.execute("SELECT tags->>'level' AS lv FROM osm_ways WHERE id = %s", (way_id,))
    return cur.fetchone()["lv"]


# ---------------------------------------------------------------------------
# The recovery: an isolated platform routes again, level-correctly
# ---------------------------------------------------------------------------

@DB
@pytest.mark.parametrize("algorithm", ["astar", "dijkstra"])
def test_bruxelles_isolated_platform_recovers_on_level(conn, algorithm):
    """Bruxelles-Midi pf 1 is a level-1 island polygon: without the snap, 1->5 is
    `disconnected`. With it, the search bridges pf 1 onto the nearest SAME-LEVEL,
    CONNECTED way (116005554, ~19 m off) and routes -- a real same-level join in
    the tens of metres, not the ~4 m level-0 teleport, not a hundred-metre jump to
    a far platform. Both algorithms agree."""
    r = find_shortest_path(conn, BXL_MIDI, "1", "5", algorithm=algorithm,
                           use_adjacency_table=True)
    assert r.get("found"), f"pf 1 -> 5 did not route: {r.get('reason')}"
    assert r["source_by_coord"], "the isolated-platform snap should have fired on pf 1"
    assert r["edge_1_way_ids"] == [PF1_SNAP_WAY], r["edge_1_way_ids"]

    with conn.cursor() as cur:
        assert _way_level(cur, PF1_SNAP_WAY) == _way_level(cur, PF1_WAY) == "1"  # same level
        join_m = _min_join_distance(cur, PF1_WAY, PF1_SNAP_WAY)
    # tens of metres -- NOT the ~4 m level-0 footway, NOT a multi-hundred-metre teleport
    assert 10.0 < join_m < STOP_SNAP_RADIUS_M, f"join was {join_m:.1f} m"


@DB
def test_bruxelles_pf1_was_disconnected_without_the_snap(conn):
    """Baseline the snap fixes: the *reverse* (5 -> 1) is the same island, and a
    connected level-1 platform routing INTO pf 1 also had to be recovered -- the
    target side snaps and target_by_coord is set."""
    r = find_shortest_path(conn, BXL_MIDI, "5", "1", algorithm="astar")
    assert r.get("found"), r.get("reason")
    assert r["target_by_coord"] and not r["source_by_coord"]
    assert r["edge_2_way_ids"] == [PF1_SNAP_WAY]


# ---------------------------------------------------------------------------
# The LEVEL guard
# ---------------------------------------------------------------------------

@DB
def test_level_guard_prefers_on_level_over_nearer_off_level(conn):
    """From a point on the level-1 platform, the unguarded snap grabs the nearest
    walkable way -- a LEVEL-0 footway a few metres off (the fake teleport). With
    the level guard it must instead return a LEVEL-1 way (farther), and with a
    level nothing nearby serves it must return nothing at all."""
    lat, lon = PF1_POINT
    with conn.cursor() as cur:
        flat = _nearest_walkable_way(cur, lat, lon, {}, way_sql=_PEDESTRIAN_SNAP_WAY_SQL,
                                     exclude_way_ids={PF1_WAY})
        on_level = _nearest_walkable_way(cur, lat, lon, {}, way_sql=_PEDESTRIAN_SNAP_WAY_SQL,
                                         level=1.0, exclude_way_ids={PF1_WAY})
        off_level = _nearest_walkable_way(cur, lat, lon, {}, way_sql=_PEDESTRIAN_SNAP_WAY_SQL,
                                          level=5.0, exclude_way_ids={PF1_WAY})
        assert flat is not None and _way_level(cur, flat[0]) == "0"      # the fake, 2D
        assert on_level is not None and _way_level(cur, on_level[0]) == "1"  # the guard's pick
        assert flat[0] != on_level[0]                                    # guard changed the outcome
    assert off_level is None  # nothing on level 5 -> honest miss (search stays disconnected)


@DB
def test_isolated_snap_off_level_only_stays_disconnected(conn, monkeypatch):
    """Synthetic 'only an off-level way is near': force pf 1's level to one nothing
    around it serves. The snap finds no on-level way within reach and returns
    nothing, so the search stays honestly `disconnected` -- it must NEVER fall back
    to a wrong-level way (the crux guard). Restoring the real level (1) routes."""
    monkeypatch.setattr(SearchContext, "_platform_level", lambda self, edges: 5.0)
    blocked = find_shortest_path(conn, BXL_MIDI, "1", "5", algorithm="astar")
    assert not blocked.get("found")
    assert blocked.get("reason") == "disconnected"
    monkeypatch.undo()
    ok = find_shortest_path(conn, BXL_MIDI, "1", "5", algorithm="astar")
    assert ok.get("found") and ok["edge_1_way_ids"] == [PF1_SNAP_WAY]


# ---------------------------------------------------------------------------
# Strictly additive: connected pairs and multi-way islands are untouched
# ---------------------------------------------------------------------------

@DB
def test_connected_pair_is_untouched_by_the_snap(conn):
    """A pair where both platforms already route (Bruxelles 5 <-> 7, both connected
    level-1 platforms) must be resolved by ref alone: neither by_coord flag set, so
    the isolation path never fired."""
    r = find_shortest_path(conn, BXL_MIDI, "5", "7", algorithm="astar")
    assert r.get("found"), r.get("reason")
    assert not r["source_by_coord"] and not r["target_by_coord"]
    assert r["walking_distance_meters"] > 0


@DB
def test_adjacency_and_gin_agree_on_recovered_platform(conn):
    """The recovery must be independent of the neighbour-fetch strategy: the
    adjacency-table and GIN-scan paths must agree that pf 1 -> 5 now routes (the
    snap uses neither table exclusively)."""
    adj = find_shortest_path(conn, BXL_MIDI, "1", "5", algorithm="astar", use_adjacency_table=True)
    gin = find_shortest_path(conn, BXL_MIDI, "1", "5", algorithm="astar", use_adjacency_table=False)
    assert adj.get("found") and gin.get("found")
    assert adj["edge_1_way_ids"] == gin["edge_1_way_ids"] == [PF1_SNAP_WAY]
    assert adj["walking_distance_meters"] == pytest.approx(gin["walking_distance_meters"], rel=1e-6)


@DB
def test_colmar_multiway_island_not_over_reached(conn):
    """Colmar A->E is a MULTI-way island: platform E's polygon shares nodes with
    its own platform_edge ways, so E is not a bare single-polygon island and the
    snap must not fire. It stays `disconnected` (the stitch-bridge mechanism owns
    that case) -- guarding against the snap over-reaching."""
    r = find_shortest_path(conn, COLMAR, "A", "E", algorithm="astar")
    assert not r.get("found")
    assert r.get("reason") == "disconnected"
