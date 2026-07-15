# Issue: vertical circulation mapped on nodes is free to the pathfinder

**Area:** routing model (`core/pathfinding/graph.py`, `core/pathfinding/search_context.py`) — not the
visualisation. Surfaced by the 3D viz (`core/viz/viz_export.py` transitions), which
made it visible; the bug itself is in pathfinding and affects every algorithm,
including the `dijkstra` ground-truth baseline.

**Severity:** medium–high. It makes transfer times too low and can make the
chosen route wrong, not just mis-timed.

## Symptom

Berlin Hbf platform 1 → 16 (`relation 5688517`) is reported as **63.6 s / 89 m**
for a transfer that climbs from **level −2 to level +2** — four floors — with no
time charged for the climb. Repro:

```
.venv/bin/python core/viz/viz_export.py --relation 5688517 --ref1 1 --ref2 16
# path.transitions shows two level changes, both kind="elevator",
# both with 0.0 m horizontal distance (they happen at a single node).
```

The two level changes occur at nodes `3625410103` and `742238019`. The latter is
a real, tagged OTIS elevator serving five levels:

```
node 742238019: highway=elevator, ref=F.PA1, brand=OTIS,
                level=-2;-1;0;1;2, operator=DB Station&Service AG
```

## Root cause

The routing graph is purely 2D (lat/lon). Edge weight (see
`SearchContext.neighbors()` and `build_time_weighted_graph()`):

```
weight = haversine_meters(u, v) / speed + penalty
```

- There is **no edge that represents a vertical move**. When two ways tagged at
  different `level`s share a node (the standard OSM way to map an elevator/stair
  as a point), that node is a single graph vertex on both ways. The search walks
  `footway(L−1) → shared node → footway(L2)`; the level change is implicit in the
  shared node and therefore costs **0 s / 0 m**.
- The elevator/stairs penalties in `way_speed_and_penalty()` (`ELEVATOR_WAIT_S =
  30 s`, `STEPS_SPEED_MS = 0.5`) only apply when the elevator/steps is a **way**
  being traversed. A node-mapped elevator is never a traversed way, so its cost
  is never added.
- The `level` tags themselves are read by nothing in the routing path — only the
  visualisation uses them.

## Impact

1. **Times are underestimated.** Berlin 1→16 omits two elevator rides; adding
   even a nominal 30 s wait each would roughly double the 63.6 s estimate.
2. **Route choice can be wrong.** A free node-shortcut between levels will be
   preferred over a correctly-costed stairs/escalator *way* nearby, so the
   "shortest" path may not be the one a traveller would actually take.
3. **The baseline is affected too.** `dijkstra` is the ground truth every other
   algorithm is checked against, so this is a systematic bias, not an
   algorithm-specific bug — cross-checks won't catch it.

## Proposed fix (sketch)

Charge a cost when the path changes level, using the `level` tags routing
currently ignores. Options, roughly in order of preference:

1. **Junction transition cost.** When an edge/hop moves between two `level`s
   (detectable from the ways' `level` tags at the shared node), add a fixed cost
   per level crossed: an elevator-node wait (~30 s) + per-floor ride, or a
   stairs climb time (`floors × steps × step_time`). Classify the node the same
   way the viz now does (`viz_export.node_kind`: elevator / escalator / stairs /
   vertical).
2. **Explicit vertical edges.** Give elevator/stair nodes real graph edges
   between the levels they serve, weighted with the mechanism's cost, instead of
   relying on incidental shared-node adjacency.
3. At minimum, **a flat penalty per level change** so vertical moves are never
   literally free, even where the mechanism is untagged (`vertical`).

**Watch out for:** double-counting when a real elevator/stairs *way* is also
present; and preserving the existing correctness tests
(`tests/test_ground_truth.py`) — the Colmar A→E disconnection and the
optimality cross-checks must still hold. Add a regression test asserting Berlin
1→16 costs more than the pure-horizontal walking time once vertical cost lands.

## Note

The `level`-based data needed to fix this is already loaded (it is what the
visualisation's Z axis is built from), so no re-import is required — only the
graph construction / edge weighting needs to start reading it.
