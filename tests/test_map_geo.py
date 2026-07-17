"""
Tests for scripts/build_map_geo.py — the Natural Earth → design/europe-geo.json
generator behind the route map's Europe outline (#18).

Offline and deterministic: everything runs on synthetic rings built in-test, so
none of this needs the 3 MB Natural Earth download. The one test that touches the
committed asset only parses it.

The invariant worth protecting is the *shared-arc* one. Douglas-Peucker is a
path-global operation, so simplifying two neighbours' rings independently pulls a
shared border two different ways and opens slivers of sea between countries that
are supposed to touch. The generator cuts rings into arcs and simplifies each arc
once; `test_shared_border_*` is what proves the neighbours still agree afterwards.
"""

import importlib.util
import json
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_map_geo",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "build_map_geo.py"),
)
bmg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bmg)


# ── Fixtures: two countries sharing a wiggly border ──────────────────────────
# A is the left square, B the right one. They meet along x≈1, and the border
# carries wiggle vertices that a coarse tolerance is meant to eat. Both rings list
# the shared vertices identically (as Natural Earth does) but traverse them in
# opposite directions — which is exactly the case that regressed.

WIGGLE = [(1.0, 0.0), (1.03, 0.5), (0.97, 1.0), (1.03, 1.5), (1.0, 2.0)]


def _ring_a():
    return [(0.0, 0.0)] + WIGGLE + [(0.0, 2.0), (0.0, 0.0)]


def _ring_b():
    return [(2.0, 0.0), (2.0, 2.0)] + list(reversed(WIGGLE)) + [(2.0, 0.0)]


def _edges(ring):
    return {(a, b) if a <= b else (b, a) for a, b in zip(ring, ring[1:])}


def _area(ring):
    a = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


BIG_BBOX = (-10.0, -10.0, 10.0, 10.0)


# ── The shared-arc invariant ────────────────────────────────────────────────

@pytest.mark.parametrize("tol", [0.0, 0.01, 0.05, 0.1, 0.2])
def test_shared_border_is_identical_in_both_neighbours(tol):
    """No slivers: every edge of the shared border appears in *both* land rings.

    If each ring were simplified on its own, a tolerance that drops (0.97, 1.0)
    from A but keeps it in B would leave a lens-shaped hole between them.
    """
    land, _, _ = bmg.build_topology([_ring_a(), _ring_b()], BIG_BBOX, tol)
    assert len(land) == 2
    a_edges, b_edges = _edges(land[0]), _edges(land[1])
    shared = a_edges & b_edges
    assert shared, f"neighbours share no edges at tol={tol} — the border was torn"

    # Every vertex on the border (x≈1) that survives in A must survive in B too.
    on_border = lambda ring: {p for p in ring if abs(p[0] - 1.0) < 0.1}  # noqa: E731
    assert on_border(land[0]) == on_border(land[1])


def test_shared_border_simplifies_identically_not_independently():
    """A tolerance big enough to eat the wiggles eats them in both rings at once.

    0.1 clears the ±0.03 border wiggles but not the squares' own 1.0-wide corners,
    so the shapes survive and only the border detail is at stake.
    """
    land, _, _ = bmg.build_topology([_ring_a(), _ring_b()], BIG_BBOX, 0.1)
    # `[:-1]` drops the closing vertex — the two rings close at opposite ends of
    # the shared border, which would otherwise show up as a spurious difference.
    a_border = {p for p in land[0][:-1] if abs(p[0] - 1.0) < 0.1}
    b_border = {p for p in land[1][:-1] if abs(p[0] - 1.0) < 0.1}
    assert a_border == b_border
    # …and the wiggles really are gone, i.e. the tolerance did something.
    assert (0.97, 1.0) not in set(land[0])


def test_absurd_tolerance_collapses_a_ring_rather_than_tearing_it():
    """A tolerance on the order of the shape itself flattens every arc to its two
    endpoints; the ring then has no area and is dropped, not emitted degenerate."""
    land, _, _ = bmg.build_topology([_ring_a(), _ring_b()], BIG_BBOX, 1.0)
    assert land == []


def test_border_arc_emitted_once_coast_arcs_separately():
    """One owner ⇒ coastline, two ⇒ internal border. Stroking every country ring
    instead would draw each border twice and render it darker than the coast."""
    land, coast, borders = bmg.build_topology([_ring_a(), _ring_b()], BIG_BBOX, 0.0)
    assert len(borders) == 1, "the shared border should be a single deduplicated arc"
    border = borders[0]
    assert {p[0] for p in border} <= {1.0, 1.03, 0.97}
    assert len(coast) == 2, "each square keeps its own outer coastline arc"
    # The border is not duplicated into the coast set.
    for arc in coast:
        assert _edges(arc) & _edges(border) == set()


# ── Rebuild fidelity ─────────────────────────────────────────────────────────

def test_rebuild_is_lossless_at_zero_tolerance():
    """tol=0 must return the input rings (bar exactly-collinear vertices)."""
    rings = [_ring_a(), _ring_b()]
    land, _, _ = bmg.build_topology(rings, BIG_BBOX, 0.0)
    for src, out in zip(rings, land):
        assert set(out) <= set(src), "rebuild invented a vertex"
        assert _area(out) == pytest.approx(_area(src), rel=1e-9)


def test_rebuild_preserves_ring_orientation():
    """A ring must come back traversed the same way round.

    Regression: arcs were stored in traversal order but keyed in canonical order,
    so any ring that happened to walk an arc 'backwards' was rebuilt reversed —
    which tore shared rings open into triangular wedges of sea.
    """
    ring = _ring_a()
    land, _, _ = bmg.build_topology([ring], BIG_BBOX, 0.0)
    out = land[0]

    def signed(r):
        a = 0.0
        for (x0, y0), (x1, y1) in zip(r, r[1:]):
            a += x0 * y1 - x1 * y0
        return a / 2.0

    assert signed(out) * signed(ring) > 0, "ring came back wound the other way"

    # Stronger: the output is an order-preserving (cyclic) subsequence of the input.
    src = ring[:-1]
    doubled = src + src
    it = iter(doubled)
    assert all(p in it for p in out[:-1]), "vertex order was scrambled"


def test_island_is_a_single_closed_arc():
    """A ring sharing nothing with anyone has no ownership change to cut at."""
    island = [(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 6.0), (5.0, 5.0)]
    land, coast, borders = bmg.build_topology([island], BIG_BBOX, 0.0)
    assert len(land) == 1 and len(coast) == 1 and borders == []
    assert _area(land[0]) == pytest.approx(1.0)


# ── Douglas-Peucker ─────────────────────────────────────────────────────────

def test_dp_keeps_endpoints_and_drops_the_middle_of_a_straight_line():
    pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    assert bmg._douglas_peucker(pts, 0.1) == [(0.0, 0.0), (3.0, 0.0)]


def test_dp_keeps_a_spike_above_tolerance():
    pts = [(0.0, 0.0), (1.0, 5.0), (2.0, 0.0)]
    assert bmg._douglas_peucker(pts, 0.1) == pts


def test_dp_handles_a_degenerate_closed_arc():
    """A lone island arc starts and ends on the same point; the perpendicular
    distance formula divides by the segment length, so it must not blow up."""
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    out = bmg._douglas_peucker(pts, 0.01)
    assert out[0] == out[-1] == (0.0, 0.0)
    assert len(out) >= 4


def test_dp_is_shortcut_free_on_two_points():
    assert bmg._douglas_peucker([(0.0, 0.0), (1.0, 1.0)], 99.0) == [(0.0, 0.0), (1.0, 1.0)]


# ── Clipping ────────────────────────────────────────────────────────────────

def test_clip_keeps_an_inside_ring_unchanged():
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    assert bmg._clip_ring(ring, (-5.0, -5.0, 5.0, 5.0)) == ring


def test_clip_drops_a_ring_fully_outside():
    ring = [(90.0, 90.0), (91.0, 90.0), (91.0, 91.0), (90.0, 90.0)]
    assert bmg._clip_ring(ring, (-5.0, -5.0, 5.0, 5.0)) == []


def test_clip_cuts_a_straddling_ring_to_the_window():
    ring = [(-10.0, 0.0), (10.0, 0.0), (10.0, 2.0), (-10.0, 2.0), (-10.0, 0.0)]
    out = bmg._clip_ring(ring, (-5.0, -5.0, 5.0, 5.0))
    assert out, "clip removed a ring that overlaps the window"
    assert all(-5.0 <= x <= 5.0 for x, _ in out)


def test_clip_is_deterministic_per_segment():
    """Two countries whose shared border crosses the window edge must be cut to the
    same new vertex, or the topology dies at the seam."""
    a = [(-10.0, 0.0), (0.0, 0.0), (0.0, 2.0), (-10.0, 2.0), (-10.0, 0.0)]
    b = [(0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
    bbox = (-5.0, -5.0, 5.0, 5.0)
    ca, cb = bmg._clip_ring(a, bbox), bmg._clip_ring(b, bbox)
    assert (0.0, 0.0) in ca and (0.0, 0.0) in cb


def test_bbox_edge_arcs_are_not_drawn_as_coastline():
    """Land cut off by the clip window is not a shore — drawing it would put a
    ruler-straight coastline through the middle of Russia."""
    ring = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0), (-10.0, -10.0)]
    bbox = (-5.0, -5.0, 5.0, 5.0)
    clipped = bmg._clip_ring(ring, bbox)
    land, coast, borders = bmg.build_topology([clipped], bbox, 0.0)
    assert land, "the clipped land itself must still be filled"
    assert coast == [], "every arc lies on the clip window, so none is coastline"


# ── Quantization ────────────────────────────────────────────────────────────

def test_quantize_is_local_so_shared_vertices_stay_shared():
    """Rounding is per-vertex, which is why it is safe to do before the topology
    build: identical inputs round to identical outputs in every ring."""
    p = (1.23456, 7.65432)
    a = bmg._quantize([p, (2.0, 2.0), (3.0, 3.0), p], 3)
    b = bmg._quantize([p, (9.0, 9.0), (8.0, 8.0), p], 3)
    assert a[0] == b[0] == (1.235, 7.654)


def test_quantize_drops_consecutive_duplicates_and_closes_the_ring():
    ring = [(0.0, 0.0), (0.0001, 0.0), (1.0, 0.0), (1.0, 1.0)]
    out = bmg._quantize(ring, 2)
    assert out[0] == out[-1], "ring must come back closed"
    assert len(out) == len(set(out)) + 1


# ── The committed asset ─────────────────────────────────────────────────────

def _asset():
    path = os.path.join(os.path.dirname(__file__), "..", "design", "europe-geo.json")
    with open(path) as f:
        return json.load(f)


def test_committed_asset_is_well_formed():
    geo = _asset()
    assert geo["bbox"] == list(bmg.BBOX)
    for key in ("land", "coast", "borders"):
        assert geo[key], f"{key} is empty"
        for arc in geo[key]:
            assert len(arc) >= 4 and len(arc) % 2 == 0, "flat lon,lat pairs"
    for ring in geo["land"]:
        assert ring[0] == ring[-2] and ring[1] == ring[-1], "land rings must close"


def test_committed_asset_covers_the_countries_the_app_can_autocomplete():
    """#18 in one assertion: the map has to contain the places you can search for."""
    geo = _asset()
    lons = [v for ring in geo["land"] for v in ring[0::2]]
    lats = [v for ring in geo["land"] for v in ring[1::2]]
    # Paris, Wien, Milano, København — the four the Germany-only outline threw off-canvas.
    for name, lon, lat in [("Paris", 2.35, 48.86), ("Wien", 16.37, 48.21),
                           ("Milano", 9.19, 45.46), ("København", 12.57, 55.68)]:
        assert min(lons) <= lon <= max(lons), f"{name} lon outside the map"
        assert min(lats) <= lat <= max(lats), f"{name} lat outside the map"


def test_committed_asset_cities_are_inside_the_bbox():
    geo = _asset()
    minlon, minlat, maxlon, maxlat = geo["bbox"]
    assert len(geo["cities"]) >= 40
    for name, lon, lat, rank in geo["cities"]:
        assert minlon <= lon <= maxlon and minlat <= lat <= maxlat, name
        assert rank in (1, 2), name
    names = [c[0] for c in geo["cities"]]
    assert len(names) == len(set(names)), "duplicate city"
