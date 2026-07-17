# Path visualisation (3D, Python) — notes

A minimal 3D viewer for a single platform-to-platform path, built to be the
stepping stone to the eventual Swift/AR client. Two decoupled steps:

```
# 1. resolve + gather + project  (slow, hits the DB)  -> JSON
.venv/bin/python core/viz/viz_export.py --relation 5688517 --ref1 1 --ref2 16

# 2. draw  (instant, reads only the JSON)             -> self-contained HTML
.venv/bin/python core/viz/viz_render.py core/viz_out/5688517_1_16.json --open
```

Output lands in `core/viz_out/` (git-ignored). Run both from the repo root.

## Why two steps

Resolving the path and gathering context ways is the DB-bound, cold-cache-slow
part; drawing should be instant and re-runnable while you tweak the view. The
JSON in between is the **contract** — the Swift/AR client will read the same
shape, so the export is not throwaway. It carries local-ENU-metre geometry,
semantic way kinds, level info and a georeferencing origin; nothing OSM- or
Postgres-specific leaks past it.

## The vertical axis is `level`, not elevation (decision)

OSM nodes store only lat/lon. Real indoor elevation is effectively absent
(measured: **0** nodes with `ele` inside Berlin Hbf; `step_count` missing on
~85% of connector stairs; `height` on <0.1% of ways). So Z is the OSM `level`
tag (Simple Indoor Tagging: 0 = ground, − below, + above) times a **nominal**
per-floor height (default 4 m, `--floor-height`). Levels are evenly spaced;
this is stated in the view and flagged as `z_is_level_not_elevation` in the
JSON. A node's height is a property of *which way it is walked on* — the same
node legitimately sits at two heights where you change floors (e.g. Berlin Hbf,
where the path steps from a `level=-1` way onto a `level=2` way at a shared
elevator node), so heights are computed per way-segment, and multi-level
connector ways (`-1;0`, `0;1;2`) are interpolated end-to-end so they slope
between floors.

If a real per-floor height ever matters, `step_count × ~0.17 m` on the minority
of connectors that carry it is the only usable signal — but it is too sparse to
build the geometry on, so it is deliberately unused.

## Render decisions

- **Framed on the path**: context ways are cropped to `--margin` metres (45 m
  default) around the route, so a station's 400 m platform tails don't swamp a
  40 m transfer. The export keeps full geometry; only the render crops.
- **Orthographic** projection: a level reads at the same screen height
  everywhere, so the labelled reference planes line up with the geometry.
- **Reference planes + shaft labels** (L2…L−2) give the vertical structure;
  **endpoints are labelled with their level** (`16 · L2`) since those two
  points are what matters most.
- **Vertical circulation is split by colour**: stairs / escalator / elevator /
  ramp, each in the legend — "which one do I take between floors" is the whole
  question.
- **The path's own level changes are drawn as coloured risers** (not left as
  bare orange), labelled by kind. OSM often maps an elevator/stair as a single
  *node* shared between the floors' footways (e.g. Berlin Hbf's OTIS node
  `742238019`), so the path can change floors "at a point" with no way to
  colour; `viz_export.node_kind` classifies that node and the render colours the
  riser accordingly. NB this is also what exposed the routing bug in
  `ISSUE-node-vertical-cost.md` — those node transitions are free to the
  pathfinder.
- **Context modes**: default is only the ways the search touched
  (`--radius 0`); `--radius <=350` also unions the walkable closure within that
  many metres to reveal off-route corridors/stairs (slower — eager DB load).
- **Details layer + distance slider** (`--details`): buildings + POIs
  (shops / amenities / tourism / offices / leisure — the landmarks and stores
  the DB itself doesn't carry) as coloured, hover-for-name dots plus faint
  building footprints. The render bins them into distance rings and a Plotly
  slider reveals them ring by ring (`off → 250 m`), default a single nearest
  ring so the base view stays minimal. Names are on hover/tap, not always-on
  labels — a major station packs hundreds of shops too densely for static text;
  a category colour key sits in the legend instead. Because `transfr_eu` is
  tag-scoped to railway/pedestrian (no shops/buildings, verified: 0 near Berlin
  Hbf), this layer is sourced from a local `osmium extract` of
  `server-admin/planet.pbf` — offline, same tool as `extract_europe.sh`, no
  external API. Slow once (~2–3 min against the 84 GB planet), then cached per
  bbox under `core/viz_out/_detail_cache/`. Street-furniture amenities (benches,
  waste baskets, vending machines, …) are filtered out so it shows places, not
  clutter.
- Mobile-first: responsive full-viewport canvas, wrapping HTML overlay header,
  wrapping horizontal legend, touch rotate/zoom.

## JSON shape (the AR contract)

```jsonc
{
  "meta": {
    "relation_id", "station_name", "ref_1", "ref_2", "algorithm",
    "context_mode",                 // "touched" | "touched+radius:200"
    "floor_height_m",               // nominal metres per level
    "z_is_level_not_elevation": true,
    "origin_lat", "origin_lon",     // local ENU frame origin (georef anchor)
    "levels_present": [...],        // sorted level numbers, for the planes
    "bbox": {min_x, max_x, min_y, max_y}
  },
  "ways": [                         // faint context
    { "id", "kind",                 // walkway|platform|stairs|escalator|elevator|ramp|...
      "is_connector", "level_raw",
      "points": [[x, y, z], ...] }  // metres, local ENU; z = level*floor_height
  ],
  "path": {
    "found": true,
    "node_ids": [...], "way_ids": [...],
    "points": [[x, y, z], ...],     // per hop; vertical transitions included
    "transitions": [               // each level change, for colouring the risers
      { "kind": "elevator|escalator|stairs|ramp|vertical",
        "from": [x,y,z], "to": [x,y,z],
        "node_id": ... | "way_id": ... }   // node = mapped on a node; way = sloped way
    ],
    "walking_time_seconds", "walking_distance_meters",
    "endpoints": { "start": {ref, xyz}, "end": {ref, xyz} }
  },
  "details": [                     // only with --details; landmarks/stores/buildings
    { "kind": "poi" | "building",
      "category": "shop|amenity|tourism|office|leisure|building",
      "subtype": ...,  "name": ...,  "dist": <m from path>,
      "xyz": [x,y,z]   // poi  — or —
      "points": [[x,y,z], ...] }   // building footprint outline
  ]
}
```

Coordinates are metres in a local East/North/Up frame centred on
`origin_lat/lon`, so an ARKit client can drop the whole scene at a georeferenced
anchor and read X/Y/Z directly. Z is level-derived (see above), not real height.

## Known edges / possible next steps

- Judging the level of an *arbitrary context* line by eye still needs the
  planes or a rotate; only the endpoints are explicitly level-labelled.
- No guard yet against a mis-tagged extreme `level` (e.g. `level=100`) blowing
  up the Z range; real test stations are all within ~±6.
- Natural follow-ups: label/emphasise the specific connector the path takes;
  per-segment hover with time/level; a small level filter; then port the render
  to SceneKit/RealityKit reading this same JSON.
