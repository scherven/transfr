r"""
Pure unit tests for core/boarding.py -- no database needed.

These prove the seat-aware boarding layer end to end on a small, fully
hand-computable synthetic station, the same way test_dijkstra.py proves the
underlying search: including a cross-check against exhaustive all-simple-paths
enumeration (the most literal "brute force") so we trust the result, not the
cleverness.

The synthetic station is deliberately one-dimensional (everything on a single
east-west line at one latitude) so every distance is a clean metre value we can
assert against by hand:

      T2 --20-- T1 --20-- T0 --60-- C --40-- p0 --25-- p1 -- ... -- p10
      \___departure platform___/          \______ arrival platform ______/
                                              (250 m, sectors A..)

The only connection between the arrival platform and the rest of the station is
at p0 (the A-end). So the walk from any seat is exactly
"(metres from the A-end where you alighted) + 100 m" -- which makes the whole
point testable: a passenger further down the platform has a strictly longer
transfer, by exactly the distance between their coaches.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from boarding import (  # noqa: E402
    START,
    PlatformGeometry,
    TrainFormation,
    boarding_source_distances,
    find_path_from_seat,
    insert_start_point,
    resolve_alighting_point,
)
from dijkstra import all_simple_paths, shortest_path  # noqa: E402
from graph import WALKING_SPEED_MS, haversine_meters  # noqa: E402

BASE_LAT = 48.0   # Strasbourg-ish; keeps longitude degrees ~74.5 km, nothing special
BASE_LON = 7.75
_R = 6_371_000.0  # must match graph.haversine_meters / boarding._EARTH_RADIUS_M


def _east(dist_m):
    """(lat, lon) `dist_m` metres due east of (BASE_LAT, BASE_LON), using the
    same sphere as haversine_meters so distances round-trip exactly. Negative
    dist_m goes west."""
    phi, theta, delta = math.radians(BASE_LAT), math.radians(90.0), dist_m / _R
    lat2 = math.asin(math.sin(phi) * math.cos(delta) + math.cos(phi) * math.sin(delta) * math.cos(theta))
    lon2 = math.radians(BASE_LON) + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi),
        math.cos(delta) - math.sin(phi) * math.sin(lat2),
    )
    return (math.degrees(lat2), math.degrees(lon2))


def _walk(graph, coords, a, b):
    """Add a symmetric walking edge a<->b, weight = distance / walking speed."""
    d = haversine_meters(*coords[a], *coords[b])
    w = d / WALKING_SPEED_MS
    graph.setdefault(a, []).append((b, w, None))
    graph.setdefault(b, []).append((a, w, None))


# Node id layout: platform p0..p10 = ids 0..10; C = 100; departure T0/T1/T2 = 200/201/202.
PLATFORM_NODE_IDS = list(range(11))          # 11 nodes, 25 m apart -> 250 m platform
PLATFORM_OFFSETS = [25.0 * i for i in range(11)]
C = 100
T0, T1, T2 = 200, 201, 202
TARGETS = {T0, T1, T2}
REST_M = 100.0  # p0 -> C (40) -> T0 (60)


def _fake_station():
    """(graph, coords, geometry, formation) for the station drawn in the module
    docstring. 8 coaches, 60 seats each, coach 1 at the A-end (p0)."""
    geometry = PlatformGeometry.straight_line(BASE_LAT, BASE_LON, PLATFORM_OFFSETS, PLATFORM_NODE_IDS)
    coords = dict(geometry.coords)
    coords[C] = _east(-40.0)
    coords[T0] = _east(-100.0)
    coords[T1] = _east(-120.0)
    coords[T2] = _east(-140.0)

    graph = {}
    for a, b in zip(PLATFORM_NODE_IDS, PLATFORM_NODE_IDS[1:]):
        _walk(graph, coords, a, b)
    _walk(graph, coords, 0, C)     # p0 -> concourse, 40 m
    _walk(graph, coords, C, T0)    # concourse -> departure platform, 60 m
    _walk(graph, coords, T0, T1)   # departure platform is 3 nodes, 20 m apart
    _walk(graph, coords, T1, T2)

    formation = TrainFormation.uniform("ICE-fake", num_coaches=8, coach_length_m=26.4, seats_per_coach=60)
    return graph, coords, geometry, formation


# ---------------------------------------------------------------------------
# PlatformGeometry
# ---------------------------------------------------------------------------

def test_straight_line_offsets_roundtrip():
    geom = PlatformGeometry.straight_line(BASE_LAT, BASE_LON, PLATFORM_OFFSETS, PLATFORM_NODE_IDS)
    assert geom.length_m == pytest.approx(250.0, abs=1e-3)
    for i, want in enumerate(PLATFORM_OFFSETS):
        assert geom.cum_m[i] == pytest.approx(want, abs=1e-3)


def test_point_at_offset_endpoints_and_midpoint():
    geom = PlatformGeometry.straight_line(BASE_LAT, BASE_LON, PLATFORM_OFFSETS, PLATFORM_NODE_IDS)
    assert geom.point_at_offset(0.0) == pytest.approx(geom.coords[0], abs=1e-9)
    assert geom.point_at_offset(250.0) == pytest.approx(geom.coords[10], abs=1e-9)
    # 37.5 m is halfway between p1 (25) and p2 (50); interpolated point should be
    # 12.5 m past p1, i.e. haversine to p1 ~= 12.5 m.
    mid = geom.point_at_offset(37.5)
    assert haversine_meters(*geom.coords[1], *mid) == pytest.approx(12.5, abs=1e-2)


def test_locate_offset_segment_and_clamp():
    geom = PlatformGeometry.straight_line(BASE_LAT, BASE_LON, PLATFORM_OFFSETS, PLATFORM_NODE_IDS)
    i, along, clamped = geom.locate_offset(60.0)   # between p2 (50) and p3 (75)
    assert (i, clamped) == (2, False)
    assert along == pytest.approx(10.0, abs=1e-3)
    # out of range clamps to the nearest end and flags it
    i, along, clamped = geom.locate_offset(999.0)
    assert clamped is True and i == 9 and along == pytest.approx(25.0, abs=1e-3)
    i, along, clamped = geom.locate_offset(-5.0)
    assert clamped is True and i == 0 and along == pytest.approx(0.0, abs=1e-9)


def test_geometry_validation():
    with pytest.raises(ValueError):
        PlatformGeometry(nodes=[1], coords={1: _east(0)})              # needs >= 2 nodes
    with pytest.raises(ValueError):
        PlatformGeometry(nodes=[1, 2], coords={1: _east(0)})            # missing coord
    with pytest.raises(ValueError):
        PlatformGeometry(nodes=[1, 2], coords={1: _east(0), 2: _east(0)})  # zero-length segment


# ---------------------------------------------------------------------------
# TrainFormation
# ---------------------------------------------------------------------------

def test_uniform_formation_spans():
    f = TrainFormation.uniform("t", num_coaches=3, coach_length_m=26.4, seats_per_coach=60)
    assert set(f.coach_span_m) == {1, 2, 3}
    assert f.coach_span_m[1] == pytest.approx((0.0, 26.4))
    assert f.coach_span_m[2] == pytest.approx((26.4, 52.8))
    assert f.coach_span_m[3] == pytest.approx((52.8, 79.2))


def test_seat_offset_monotonic_and_bounded():
    f = TrainFormation.uniform("t", num_coaches=8, coach_length_m=26.4, seats_per_coach=60)
    prev = -1.0
    for coach in range(1, 9):
        start_m, end_m = f.coach_span_m[coach]
        offsets = [f.seat_offset_m(coach, s) for s in range(1, 61)]
        assert offsets == sorted(offsets)                 # increasing within a coach
        assert start_m < offsets[0] and offsets[-1] < end_m  # every seat inside its coach
        assert offsets[0] > prev                          # increasing across coaches
        prev = offsets[-1]


def test_seat_offset_delta_between_coaches_is_one_coach_length():
    f = TrainFormation.uniform("t", num_coaches=8, coach_length_m=26.4, seats_per_coach=60)
    for coach in range(1, 8):
        delta = f.seat_offset_m(coach + 1, 30) - f.seat_offset_m(coach, 30)
        assert delta == pytest.approx(26.4, abs=1e-9)


def test_invalid_coach_and_seat():
    f = TrainFormation.uniform("t", num_coaches=4, seats_per_coach=60)
    with pytest.raises(KeyError):
        f.seat_offset_m(5, 1)      # no coach 5
    with pytest.raises(ValueError):
        f.seat_offset_m(1, 0)      # seat below range
    with pytest.raises(ValueError):
        f.seat_offset_m(1, 61)     # seat above range


# ---------------------------------------------------------------------------
# resolve_alighting_point -- the seat -> point crux (incl. the user's example)
# ---------------------------------------------------------------------------

def test_resolve_coach7_seat46_is_consistent():
    _, _, geom, formation = _fake_station()
    ap = resolve_alighting_point(formation, geom, coach=7, seat=46)
    # offset must land inside coach 7's span, and the point must be exactly the
    # geometry's interpolation of that offset -- the two halves of the chain agree.
    lo, hi = formation.coach_span_m[7]
    assert lo <= ap.offset_m <= hi
    assert ap.point == pytest.approx(geom.point_at_offset(ap.offset_m), abs=1e-12)
    assert ap.clamped is False
    # coach 7 seat 46 concretely: (6*26.4) + (45.5/60)*26.4
    assert ap.offset_m == pytest.approx(6 * 26.4 + (45.5 / 60) * 26.4, abs=1e-9)


def test_resolve_flags_overlong_train():
    # A train longer than the 250 m platform: a far coach overhangs the end.
    geom = PlatformGeometry.straight_line(BASE_LAT, BASE_LON, PLATFORM_OFFSETS, PLATFORM_NODE_IDS)
    long_train = TrainFormation.uniform("long", num_coaches=12, coach_length_m=26.4, seats_per_coach=60)
    near = resolve_alighting_point(long_train, geom, coach=1, seat=1)
    far = resolve_alighting_point(long_train, geom, coach=12, seat=60)
    assert near.clamped is False
    assert far.clamped is True                      # coach 12 (~290 m) overhangs 250 m
    assert far.point == pytest.approx(geom.coords[10], abs=1e-9)  # snapped to the far end


# ---------------------------------------------------------------------------
# insert_start_point + boarding_source_distances (the graph wiring)
# ---------------------------------------------------------------------------

def test_insert_start_edges_are_partial_platform_walks():
    graph, coords, geom, _ = _fake_station()
    # offset 60 m -> between p2 (50) and p3 (75); 10 m past p2, 15 m short of p3.
    new_graph, new_coords, start_edges = insert_start_point(graph, coords, geom, 60.0)
    assert set(start_edges) == {2, 3}
    assert start_edges[2] * WALKING_SPEED_MS == pytest.approx(10.0, abs=1e-3)
    assert start_edges[3] * WALKING_SPEED_MS == pytest.approx(15.0, abs=1e-3)
    assert new_graph[START] == [(n, w, None) for n, w in start_edges.items()]
    assert START in new_coords


def test_source_distances_match_insertion_and_route_the_same():
    graph, coords, geom, _ = _fake_station()
    offset = 137.5
    _, _, start_edges = insert_start_point(graph, coords, geom, offset)
    assert boarding_source_distances(geom, offset) == start_edges  # the two wirings agree

    # And seeding those distances as Dijkstra initial distances (the DB-backed
    # SearchContext form) reaches the target with the same total as the START
    # sentinel form.
    seeded = _dijkstra_with_initial(graph, boarding_source_distances(geom, offset), TARGETS)
    new_graph, _, _ = insert_start_point(graph, coords, geom, offset)
    sentinel_total, _ = shortest_path(new_graph, {START}, TARGETS)
    assert seeded == pytest.approx(sentinel_total, abs=1e-9)


def test_insert_requires_bracket_nodes_in_graph():
    graph, coords, geom, _ = _fake_station()
    del graph[0]  # remove the p0 vertex so the offset-0 bracket is missing
    with pytest.raises(ValueError):
        insert_start_point(graph, coords, geom, 5.0)


def _dijkstra_with_initial(graph, initial, targets):
    """Plain Dijkstra seeded with per-source initial distances (not all 0).
    Local to the test: this is exactly what a DB-backed SearchContext would do
    with boarding_source_distances() instead of the START sentinel."""
    import heapq
    dist = dict(initial)
    heap = [(d, i, n) for i, (n, d) in enumerate(initial.items())]
    heapq.heapify(heap)
    counter = len(heap)
    visited = set()
    while heap:
        d, _, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        if u in targets:
            return d
        for v, w, _label in graph.get(u, []):
            nd = d + w
            if v not in dist or nd < dist[v] - 1e-12:
                dist[v] = nd
                heapq.heappush(heap, (nd, counter, v))
                counter += 1
    return None


# ---------------------------------------------------------------------------
# find_path_from_seat -- the headline behaviour
# ---------------------------------------------------------------------------

def test_coach7_seat46_end_to_end():
    graph, coords, geom, formation = _fake_station()
    res = find_path_from_seat(graph, coords, formation, geom, coach=7, seat=46, targets=TARGETS)
    assert res["found"] is True
    assert res["reached_target"] == T0            # nearest departure-platform node
    assert res["node_path"][-1] == T0
    assert res["node_path"][0] == res["entry_node"]
    # walk = (offset from A-end) + 100 m rest, time = that / walking speed
    offset = formation.seat_offset_m(7, 46)
    assert res["walking_distance_meters"] == pytest.approx(offset + REST_M, abs=0.05)
    assert res["walking_time_seconds"] == pytest.approx((offset + REST_M) / WALKING_SPEED_MS, abs=0.1)


def test_further_down_the_platform_is_a_longer_transfer():
    """The whole reason this layer exists: seat position changes the answer."""
    graph, coords, geom, formation = _fake_station()
    dists = [
        find_path_from_seat(graph, coords, formation, geom, coach=c, seat=1, targets=TARGETS)["walking_distance_meters"]
        for c in range(1, 9)
    ]
    assert dists == sorted(dists)                 # monotonic non-decreasing in coach
    assert dists[0] < dists[-1]                   # and strictly longer end to end
    # each extra coach adds exactly one coach length of platform walking
    for a, b in zip(dists, dists[1:]):
        assert b - a == pytest.approx(26.4, abs=0.05)


def test_matches_brute_force_from_alighting_point():
    """Trust-no-cleverness: the layer's time must equal the true minimum found
    by exhaustively enumerating every simple path from the inserted start."""
    graph, coords, geom, formation = _fake_station()
    offset = resolve_alighting_point(formation, geom, coach=6, seat=12).offset_m
    new_graph, _, _ = insert_start_point(graph, coords, geom, offset)
    brute = all_simple_paths(new_graph, START, TARGETS, max_depth=30)
    best_time = min(cost for cost, _ in brute)

    res = find_path_from_seat(graph, coords, formation, geom, coach=6, seat=12, targets=TARGETS)
    assert res["walking_time_seconds"] == pytest.approx(best_time, abs=0.1)


def test_agrees_with_manual_start_insertion():
    graph, coords, geom, formation = _fake_station()
    offset = resolve_alighting_point(formation, geom, coach=4, seat=33).offset_m
    new_graph, _, _ = insert_start_point(graph, coords, geom, offset)
    total, path = shortest_path(new_graph, {START}, TARGETS)

    res = find_path_from_seat(graph, coords, formation, geom, coach=4, seat=33, targets=TARGETS)
    assert res["walking_time_seconds"] == pytest.approx(round(total, 1), abs=1e-9)
    assert res["node_path"] == path[1:]           # same path, minus the sentinel


def test_end_point_need_not_be_precise_boards_at_nearest():
    """You can board a departing train at any coach, so the target is the whole
    departure platform. The search reaches the nearest of its nodes, and giving
    it the full set is never worse than pinning a single far node."""
    graph, coords, geom, formation = _fake_station()
    kw = dict(graph=graph, coords=coords, formation=formation, geometry=geom, coach=5, seat=20)

    full = find_path_from_seat(targets={T0, T1, T2}, **kw)
    only_far = find_path_from_seat(targets={T2}, **kw)
    assert full["reached_target"] == T0                       # nearest is chosen
    assert full["walking_distance_meters"] <= only_far["walking_distance_meters"]
    assert only_far["walking_distance_meters"] - full["walking_distance_meters"] == pytest.approx(40.0, abs=0.05)


def test_alighting_exactly_on_a_node():
    graph, coords, geom, formation = _fake_station()
    # Build a formation whose coach 1 seat spans put a seat exactly at p4 (100 m).
    # Simplest: query the graph search with an offset that lands on a node via
    # insert_start_point directly, then confirm find_path_from_seat is consistent.
    new_graph, _, start_edges = insert_start_point(graph, coords, geom, 100.0)
    assert start_edges[4] == pytest.approx(0.0, abs=1e-9)      # exactly on p4
    total, path = shortest_path(new_graph, {START}, TARGETS)
    assert path[1] == 4                                        # enters at p4, zero-cost hop
    assert total == pytest.approx((100.0 + REST_M) / WALKING_SPEED_MS, abs=1e-3)


def test_disconnected_returns_not_found():
    graph, coords, geom, formation = _fake_station()
    # Sever the only link between the platform and the rest of the station.
    graph[0] = [e for e in graph[0] if e[0] != C]
    graph[C] = [e for e in graph[C] if e[0] != 0]
    res = find_path_from_seat(graph, coords, formation, geom, coach=3, seat=3, targets=TARGETS)
    assert res["found"] is False and res["reason"] == "disconnected"
    assert res["coach"] == 3 and res["seat"] == 3               # context still reported


def test_find_path_does_not_mutate_inputs():
    graph, coords, geom, formation = _fake_station()
    import copy
    graph_before = copy.deepcopy(graph)
    coords_before = dict(coords)
    find_path_from_seat(graph, coords, formation, geom, coach=2, seat=2, targets=TARGETS)
    assert graph == graph_before
    assert coords == coords_before
    assert START not in graph and START not in coords
