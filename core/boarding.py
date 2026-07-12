"""
Seat-aware boarding/alighting layer on top of the platform pathfinder.

transfr already answers "which platform -> which platform, and how do I walk
between them" (see core/ground_truth.py, search_context.py). It seeds the
shortest-path search from *every* node of the arrival platform edge -- i.e.
"you could have alighted anywhere along the platform." That is the right model
when you know nothing about the traveller's position.

This module adds the missing FRONT of the chain: turn a specific seat into a
specific *point* on the arrival platform, then seed the search from that point
instead of from the whole platform edge. A mainline platform is 300-430 m long,
so "you arrive on platform 7" hides a multi-minute walk difference depending on
whether your coach stops at sector A or sector G. Resolving the seat pins that
down.

    seat/coach  --(formation data)-->  offset along the platform (metres)
                --(platform geometry)-->  a lat/lon point
                --(this module)-->  a START vertex wired into the walk graph
                --(existing Dijkstra)-->  the real door-to-platform route

The pipeline's last hop is exactly what transfr already does; this module only
manufactures the start point and hands it to core/dijkstra.shortest_path()
unchanged -- it is a layer *on top of* the algorithm, not a new algorithm.

FAKE DATA, ON PURPOSE. The coach->offset formation data built here (see
TrainFormation) is a placeholder. In production it comes from a real coach-
sequence feed -- DB RIS::Transports (metres + percent along the platform),
SBB's open Train Formation Service (sector-level), OSM railway:platform:section
nodes, etc. (see the data-source deep dive). The platform *geometry* comes from
OSM, which transfr already ingests. Everything here is pure and database-
independent so it can be proven against synthetic stations first, mirroring
core/bidirectional_search.py's "correct on synthetic data before it touches the
DB" approach.

ONLY THE START POINT MATTERS. You can board a departing train at any coach, so
the *end* stays "any node on the departure platform edge" -- the existing
multi-target behaviour. The seat only fixes where you START walking. So this
module takes an arbitrary `targets` set (the departure platform's nodes, or a
station exit, or anything) and does not try to resolve a precise end point.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Hashable, List, Optional, Set, Tuple, Union

from dijkstra import Graph, shortest_path
from graph import WALKING_SPEED_MS, haversine_meters

# Coach labels are not always integers: they can be "12"/"26" or letters
# "A".."E" (Colmar). Everything here treats a coach id as an opaque hashable
# key, so both forms work; the alias just documents that.
CoachId = Union[int, str]

__all__ = [
    "PlatformGeometry",
    "TrainFormation",
    "AlightingPoint",
    "resolve_alighting_point",
    "boarding_source_distances",
    "insert_start_point",
    "find_path_from_seat",
    "START",
]

Coord = Tuple[float, float]  # (lat, lon)

# Great-circle radius used by graph.haversine_meters. Repeated here (not
# imported -- it's a local constant there) so the synthetic-geometry
# constructor below is the exact inverse of the distance function the rest of
# the system uses: a platform built from metre offsets round-trips back to
# those same metres through haversine_meters to floating precision, instead of
# drifting ~0.1% because a different Earth radius was assumed.
_EARTH_RADIUS_M = 6_371_000.0


# ---------------------------------------------------------------------------
# Platform geometry
# ---------------------------------------------------------------------------

@dataclass
class PlatformGeometry:
    """A platform edge as an ordered polyline of OSM-style nodes.

    `nodes` is ordered from the reference end (call it sector A) to the far
    end; `coords` maps every node id to (lat, lon). Offsets are measured in
    metres along the polyline from nodes[0]. This is exactly the shape of an
    OSM railway=platform_edge way plus its node coordinates -- i.e. what
    SearchContext already loads as its `sources` -- so a point placed here maps
    directly onto the real routing graph later.
    """

    nodes: List[int]
    coords: Dict[int, Coord]
    cum_m: List[float] = field(init=False)  # cum_m[i] = distance nodes[0]..nodes[i]

    def __post_init__(self) -> None:
        if len(self.nodes) < 2:
            raise ValueError("a platform needs at least two nodes to have a length")
        missing = [n for n in self.nodes if n not in self.coords]
        if missing:
            raise ValueError(f"missing coordinates for platform nodes: {missing}")
        cum = [0.0]
        for a, b in zip(self.nodes, self.nodes[1:]):
            (alat, alon), (blat, blon) = self.coords[a], self.coords[b]
            step = haversine_meters(alat, alon, blat, blon)
            if step == 0.0:
                raise ValueError(f"zero-length platform segment between nodes {a} and {b}")
            cum.append(cum[-1] + step)
        self.cum_m = cum

    @property
    def length_m(self) -> float:
        return self.cum_m[-1]

    def locate_offset(self, offset_m: float) -> Tuple[int, float, bool]:
        """Map an along-platform offset to the segment that contains it.

        Returns (i, dist_from_node_i_m, clamped): the point lies on segment
        nodes[i] -> nodes[i+1], `dist_from_node_i_m` metres past nodes[i].
        Offsets outside [0, length] are clamped onto the nearest end (a coach
        can overhang a short platform in reality -- you still step out onto the
        platform's end, not into the void); `clamped` flags that this happened
        so callers can surface it rather than silently mislead.
        """
        clamped = not (0.0 <= offset_m <= self.length_m)
        offset_m = min(max(offset_m, 0.0), self.length_m)
        # Last segment index whose start is <= offset. Linear scan: platforms
        # have a handful of nodes, not thousands.
        i = 0
        for k in range(len(self.nodes) - 1):
            if self.cum_m[k] <= offset_m:
                i = k
            else:
                break
        return i, offset_m - self.cum_m[i], clamped

    def point_at_offset(self, offset_m: float) -> Coord:
        """Interpolate a (lat, lon) at `offset_m` along the platform."""
        i, along, _ = self.locate_offset(offset_m)
        a, b = self.nodes[i], self.nodes[i + 1]
        seg = self.cum_m[i + 1] - self.cum_m[i]
        t = 0.0 if seg == 0 else along / seg
        (alat, alon), (blat, blon) = self.coords[a], self.coords[b]
        return (alat + (blat - alat) * t, alon + (blon - alon) * t)

    @classmethod
    def straight_line(
        cls,
        base_lat: float,
        base_lon: float,
        node_offsets_m: List[float],
        node_ids: Optional[List[int]] = None,
        bearing_deg: float = 90.0,
    ) -> "PlatformGeometry":
        """Build a straight platform from metre offsets -- a fake-data helper.

        Places nodes at the given offsets along a constant bearing from
        (base_lat, base_lon). `bearing_deg=90` (due east) keeps every node at
        `base_lat`, which makes the geometry trivial to reason about in tests.
        The metre->degree conversion is the exact inverse of
        graph.haversine_meters, so haversine of the generated coordinates
        reproduces `node_offsets_m` to floating precision.
        """
        if node_offsets_m != sorted(node_offsets_m):
            raise ValueError("node_offsets_m must be non-decreasing (ordered A-end -> far end)")
        if node_ids is None:
            node_ids = list(range(1, len(node_offsets_m) + 1))
        if len(node_ids) != len(node_offsets_m):
            raise ValueError("node_ids and node_offsets_m must be the same length")
        phi = math.radians(base_lat)
        theta = math.radians(bearing_deg)
        coords: Dict[int, Coord] = {}
        for nid, d in zip(node_ids, node_offsets_m):
            # Angular distance, then destination-point formula on a sphere of
            # radius _EARTH_RADIUS_M (matching haversine_meters).
            delta = d / _EARTH_RADIUS_M
            lat2 = math.asin(
                math.sin(phi) * math.cos(delta)
                + math.cos(phi) * math.sin(delta) * math.cos(theta)
            )
            lon2 = math.radians(base_lon) + math.atan2(
                math.sin(theta) * math.sin(delta) * math.cos(phi),
                math.cos(delta) - math.sin(phi) * math.sin(lat2),
            )
            coords[nid] = (math.degrees(lat2), math.degrees(lon2))
        return cls(nodes=list(node_ids), coords=coords)


# ---------------------------------------------------------------------------
# Train formation (coach -> platform offset) -- placeholder data
# ---------------------------------------------------------------------------

@dataclass
class TrainFormation:
    """Where each coach of a train stops along the platform.

    `coach_span_m[c] = (start_m, end_m)` gives coach c's extent in metres from
    the platform's reference (sector-A) end. This is the shape a real coach-
    sequence feed provides (DB reports exactly start/end in metres and percent;
    SBB reports the sector, which maps to an offset range). `seats_per_coach`
    plus the door model turns a seat into an offset within its coach's span.

    Orientation is a real-world unknown this class does NOT try to guess:
    whether coach 1 sits at the A-end or the far end depends on the train's
    direction of travel and is something the formation feed states explicitly.
    Here you pin it down by how you fill coach_span_m (see `uniform`).
    """

    train_id: str
    coach_span_m: Dict[CoachId, Tuple[float, float]]
    seats_per_coach: int = 60

    def __post_init__(self) -> None:
        if not self.coach_span_m:
            raise ValueError("a formation needs at least one coach")
        for c, (s, e) in self.coach_span_m.items():
            if e <= s:
                raise ValueError(f"coach {c} has non-positive length: span {(s, e)}")
        if self.seats_per_coach < 1:
            raise ValueError("seats_per_coach must be >= 1")

    def seat_offset_m(self, coach: CoachId, seat: int) -> float:
        """Along-platform offset (metres from the A-end) of one seat's door.

        Placeholder door model: seats are spread evenly across their coach and
        seat s sits at the centre of its slot, so seat 1 is near the coach's
        A-end and seat `seats_per_coach` near its far end. Real coaches have a
        couple of doors and non-linear seat maps -- swap this method out when a
        real feed with door positions is wired in; nothing else here depends on
        *how* the offset is derived, only that a seat yields one.
        """
        if coach not in self.coach_span_m:
            raise KeyError(f"train {self.train_id} has no coach {coach}")
        if not (1 <= seat <= self.seats_per_coach):
            raise ValueError(
                f"seat {seat} out of range 1..{self.seats_per_coach} for coach {coach}"
            )
        start_m, end_m = self.coach_span_m[coach]
        frac = (seat - 0.5) / self.seats_per_coach
        return start_m + frac * (end_m - start_m)

    @classmethod
    def uniform(
        cls,
        train_id: str,
        num_coaches: int,
        coach_length_m: float = 26.4,
        seats_per_coach: int = 60,
        first_coach_offset_m: float = 0.0,
        gap_m: float = 0.0,
    ) -> "TrainFormation":
        """Evenly spaced coaches 1..num_coaches from the A-end -- a fake-data
        helper. coach_length_m defaults to 26.4 m (a UIC standard coach). `gap_m`
        adds inter-coach spacing; `first_coach_offset_m` shifts the whole train
        down the platform.
        """
        if num_coaches < 1:
            raise ValueError("num_coaches must be >= 1")
        spans: Dict[int, Tuple[float, float]] = {}
        cursor = first_coach_offset_m
        for c in range(1, num_coaches + 1):
            spans[c] = (cursor, cursor + coach_length_m)
            cursor += coach_length_m + gap_m
        return cls(train_id=train_id, coach_span_m=spans, seats_per_coach=seats_per_coach)


# ---------------------------------------------------------------------------
# Resolving a seat to a point, and wiring that point into the graph
# ---------------------------------------------------------------------------

@dataclass
class AlightingPoint:
    """Where a specific seat's occupant steps onto the platform."""

    coach: CoachId
    seat: int
    offset_m: float          # along the platform, from the A-end
    point: Coord             # (lat, lon)
    clamped: bool = False    # offset fell outside the mapped platform, snapped to an end


def resolve_alighting_point(
    formation: TrainFormation, geometry: PlatformGeometry, coach: int, seat: int
) -> AlightingPoint:
    """seat -> AlightingPoint: the crux of this module. Chains the coach-offset
    lookup and the platform interpolation, carrying through the `clamped` flag
    from geometry.locate_offset so an over-long train is surfaced, not hidden."""
    offset = formation.seat_offset_m(coach, seat)
    _, _, clamped = geometry.locate_offset(offset)
    return AlightingPoint(
        coach=coach,
        seat=seat,
        offset_m=offset,
        point=geometry.point_at_offset(offset),
        clamped=clamped,
    )


def boarding_source_distances(geometry: PlatformGeometry, offset_m: float) -> Dict[int, float]:
    """The two platform nodes bracketing `offset_m`, each mapped to the walking
    time (seconds) needed to reach it from the alighting point.

    This is the database-backed wiring bridge. Where the pure path below inserts
    a START sentinel node, the DB-backed SearchContext would instead seed its
    Dijkstra with these {node: initial_distance} entries in place of today's
    "{platform node: 0 for every node}" -- same effect, no synthetic vertex.
    Returned and tested here so the two paths provably agree.
    """
    i, along, _ = geometry.locate_offset(offset_m)
    a, b = geometry.nodes[i], geometry.nodes[i + 1]
    seg = geometry.cum_m[i + 1] - geometry.cum_m[i]
    return {a: along / WALKING_SPEED_MS, b: (seg - along) / WALKING_SPEED_MS}


START: str = "__START__"


def insert_start_point(
    graph: Graph,
    coords: Dict[Hashable, Coord],
    geometry: PlatformGeometry,
    offset_m: float,
    start: Hashable = START,
) -> Tuple[Graph, Dict[Hashable, Coord], Dict[Hashable, float]]:
    """Return copies of `graph`/`coords` with a `start` vertex added at
    `offset_m` along the platform, joined to the two bracketing platform nodes
    by plain walking edges (distance / WALKING_SPEED_MS).

    Only outgoing edges start -> bracket-node are added: the search only ever
    departs from `start`, never routes through it, so wiring the reverse
    direction would be dead weight (and would mutate the bracket nodes'
    adjacency, which we deliberately leave untouched). From either bracket node
    the existing graph already lets you walk further along the platform or off
    into the station, so both directions of "walk from your door" are covered.

    The bracket nodes must already be vertices in `graph` -- they are real
    platform-edge nodes. Non-mutating: the input graph/coords are not modified.
    """
    i, along, _ = geometry.locate_offset(offset_m)
    a, b = geometry.nodes[i], geometry.nodes[i + 1]
    for n in (a, b):
        if n not in graph:
            raise ValueError(f"platform node {n} is not in the routing graph")
    seg = geometry.cum_m[i + 1] - geometry.cum_m[i]
    start_edges = {a: along / WALKING_SPEED_MS, b: (seg - along) / WALKING_SPEED_MS}

    new_graph: Graph = dict(graph)
    new_graph[start] = [(n, w, None) for n, w in start_edges.items()]
    new_coords = dict(coords)
    new_coords[start] = geometry.point_at_offset(offset_m)
    return new_graph, new_coords, start_edges


def find_path_from_seat(
    graph: Graph,
    coords: Dict[Hashable, Coord],
    formation: TrainFormation,
    geometry: PlatformGeometry,
    coach: CoachId,
    seat: int,
    targets: Set[Hashable],
) -> Dict:
    """Route from a seat's alighting point to the nearest target node.

    `graph` is a time-weighted walk graph (edges in seconds, same convention as
    graph.build_time_weighted_graph); `coords` gives node coordinates;
    `targets` is "anywhere you can board the next train" -- the departure
    platform's nodes, a station exit, whatever. The seat fixes the start; the
    end stays a multi-target set exactly as the existing pathfinder handles it.

    Returns a result dict shaped like the rest of the system (a "found" key
    either way). walking_distance_meters is recomputed from coordinates along
    the chosen path -- including the door->first-node walk along the platform --
    because edge weights are seconds and mix speeds (stairs, escalators), so
    distance cannot be back-derived from time.
    """
    alighting = resolve_alighting_point(formation, geometry, coach, seat)
    new_graph, new_coords, start_edges = insert_start_point(graph, coords, geometry, alighting.offset_m)

    result = shortest_path(new_graph, {START}, targets)
    base = {
        "coach": coach,
        "seat": seat,
        "alighting_offset_m": round(alighting.offset_m, 2),
        "alighting_point": alighting.point,
        "alighting_clamped": alighting.clamped,
    }
    if result is None:
        return {"found": False, "reason": "disconnected", **base}

    total_seconds, vertex_path = result
    # vertex_path is [START, entry_node, ..., target]. Drop the sentinel; the
    # real route begins at entry_node, reached by walking `door_walk_m` along
    # the platform from the door.
    entry_node = vertex_path[1]
    door_walk_m = start_edges[entry_node] * WALKING_SPEED_MS
    node_path = vertex_path[1:]
    rest_distance = sum(
        haversine_meters(*new_coords[p], *new_coords[q])
        for p, q in zip(node_path, node_path[1:])
    )
    return {
        "found": True,
        "walking_time_seconds": round(total_seconds, 1),
        "walking_distance_meters": round(door_walk_m + rest_distance, 1),
        "entry_node": entry_node,
        "node_path": node_path,
        "reached_target": node_path[-1],
        **base,
    }
