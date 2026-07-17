# Issue: the 3D viz over-detects vertical transitions ("~7 elevators")

**Area:** visualisation only (`core/viz/viz_export.py`). The routing engine
(`core/pathfinding/`) is **not** implicated in the symptom below — its paths,
times and distances for these two transfers are correct. This is distinct from
`ISSUE-node-vertical-cost.md`, whose routing fix (per-level "ports" + priced
vertical edges) is already implemented and still holds (Berlin Hbf 1→16 is
charged +45.5 s of vertical cost; see the regression test).

## Symptom

Two real transfers render as if you ride a stack of elevators:

```
core/viz/viz_export.py --relation 215808 --ref1 4 --ref2 16   # Stuttgart Hbf
core/viz/viz_export.py --relation 140179 --ref1 3 --ref2 12   # Karlsruhe Hbf
```

- **Stuttgart 4→16:** 86.1 s / 120.6 m, `levels_present [0,1]`, yet **7
  transitions, all `kind="vertical"`**, z alternating 4→0→4→0… along one
  corridor.
- **Karlsruhe 3→12:** 94.2 s / 131.8 m, **9 transitions** (3 `vertical` + 6
  `other`), with non-monotonic z (…1.51→0.47→4.0→3.51…).

## Diagnosis — two different over-detections, one symptom

The viz assigns each path node a height from *whichever way carries the current
hop* (`way_for_hop` → `node_z`), then emits a "transition" wherever consecutive
heights differ (`viz_export.export`, the `transitions` loop). Both stations feed
that detector bogus heights; **neither path actually oscillates.**

### Stuttgart = viz over-detection from untagged duplicate geometry (mechanism c)

The router path is a **strictly monotonic walk** along the boundary of one way,
`230821002` — a closed `area=yes highway=footway level=1` polygon, the "Neuer
Querbahnsteig" concourse (path nodes map to boundary indices 31,30,29,…,13 in
order). Time is exactly horizontal: 120.6 m / 1.4 m/s = 86.1 s, **zero vertical
cost charged.** It is a flat level-1 walk.

The phantom z-flips come from four **empty-tagged (`{}`) 3-node ways**
(`1418963280`, `1418963277`, `1418963274`, `1418963265`) laid over stretches of
that same boundary — duplicate geometry sharing the area's nodes. An untagged
way has no `level`, so `parse_levels(None) → [0.0]` puts it on the ground.
`way_for_hop` returned *whichever way the `node_to_ways` set yielded first*, so
some hops took their height from the level-1 area (z=4) and adjacent hops from a
stub (z=0). Every flip fired a `vertical` transition → 7 of them.

- **Not** free vertical cost: there is no level change in the path at all.
- **Not** garbage level tags: the area's `level=1` is correct; the stubs simply
  have *no* tag and inherit the ground default.

### Karlsruhe = real 2-elevator route + viz over-detection of a room polygon (mechanisms a + c)

The route is physically sensible: platform 3 (L1) → **down** through
`270880470` (`room=elevator`, `building:part=elevator`, `level=0;1`, a closed
13-node polygon) → L0 pedestrian underpass → **up** `270880465`
(`building=elevator`, `level=1`, closed polygon) at door D03 → platform 12 (L1).
Two elevators = two real level changes.

Two things inflate that to nine:

1. **Room-polygon interpolation (the 6 `other` transitions).** `270880470` is a
   multi-level *area*, but `way_node_heights` interpolated it "first node → last
   node along the perimeter" as if it were a linear ramp. Going around a room's
   boundary that produces non-monotonic garbage z, one spurious sloped
   transition per boundary hop.
2. **Free vertical cost (mechanism a), the routing under-count.** The two
   elevators here are mapped as `room=elevator` / `building=elevator`
   **polygons**, not as `highway=elevator` ways or nodes, so *neither* the
   way-penalty path (`way_speed_and_penalty`) *nor* the node-port path
   (`is_vertical_node`, which keys on `highway/railway=elevator`,
   `conveying`, `highway=steps`) recognises them. The transfer is therefore
   costed as pure horizontal (94.2 s == 131.8 m / 1.4), i.e. two elevator rides
   for free. This under-count is real but is **not** the visible "7 elevators"
   symptom, and fixing it safely is a separate, deeper change (see below).

## Fix (shipped — visualisation only)

`core/viz/viz_export.py`:

1. **`way_for_hop` now prefers the most reliably-levelled way** among those that
   place a hop's two nodes adjacent (`_hop_way_rank`: explicit `level` tag, then
   any `highway`/`railway` tag). The level-1 concourse always beats an empty
   stub, so Stuttgart's hops all take z=4 → **0 transitions**, and the reported
   time/distance/node_path are unchanged.
2. **Multi-level *areas* are rendered flat, not ramped** (`is_area_way` +
   `way_node_heights(is_area=…)`). A polygon (`area=yes`, `indoor=room/area`,
   `building`/`building:part`, or a closed node ring) with >1 level renders flat
   at its lowest level. Karlsruhe's room stops fabricating slopes; the two real
   elevators survive as **2 transitions** (at doors D10 and D03). Genuine linear
   connectors (open ways: stairs, escalators, ramps) still slope end-to-end, so
   Berlin 1→16 is untouched (still 2 real transitions).

Result: Stuttgart 7→**0**, Karlsruhe 9→**2**, Berlin 2→**2** (guardrail).

Regression tests: `tests/test_viz_transitions.py` (the three stations, DB-backed)
and new unit tests in `tests/test_viz_export.py` (`is_area_way`, area flattening,
`way_for_hop` preference). Routing/ground-truth suite is untouched and still
green, including `test_berlin_1_16_charges_node_mapped_vertical_cost`.

## Not fixed here (deliberately): Karlsruhe's routing under-count

Charging the two Karlsruhe elevators would require the router to treat a
**multi-level building-part / room polygon** as vertical circulation. Today
`is_vertical_node` deliberately fires *only* on nodes OSM explicitly tags as a
mechanism, precisely because ~45 % of ways carry no `level` and default to
ground — keying on a mere level mismatch at a shared node would split ordinary
platform/approach nodes into ports and risks the ground-truth optimality and
Colmar-disconnection invariants. That is the deep change `ISSUE-node-vertical-cost.md`
warns about; it is left as a diagnosed follow-up rather than merged with this
low-risk viz fix. (A natural next step: recognise `building=elevator` /
`building:part=elevator` / `room=elevator` polygons spanning ≥2 levels as
node-mapped elevators at their door nodes, guarded by tests for the platform
false-positive.) Their transitions currently render with the generic
`kind="vertical"` for the same reason node classification can't see the polygon;
the count — the actual complaint — is now correct.
