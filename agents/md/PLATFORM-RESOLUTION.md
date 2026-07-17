# Platform resolution: how a MOTIS track becomes routable geometry

To assess a transfer, the API must turn a journey's **track number** (from
MOTIS, e.g. "arrive track 3, depart track 5") into **walkable platform geometry**
`core/` can run a shortest-path between. This note documents how that mapping
works today (Tier 1, done), the next lever (Tier 2, proposed), and the cases
neither tier can fix.

All counts below are measured against the live `transfr_eu` database.

## The tag landscape (why this is hard)

OSM tags a platform's track number in several places, in decreasing precision
and increasing breadth:

| where the ref lives | count (with a ref) | notes |
|---|---|---|
| `railway=platform_edge` way | **10,516** | the boardable edge; rarest, most reliable |
| `railway=platform` way (area) | 38,913 | the rail platform surface |
| `public_transport=platform` way (area) | 67,255 | broadest; mode-agnostic (can be bus/tram) |
| `stop_position` / `railway=stop` **node** | **112,023** | a point *on the track*, carries `ref`/`local_ref` |

`core/` historically matched **only** `railway=platform_edge` — the smallest
pool — so any station that maps its platforms differently returned
`platform_not_found`, even though the track number was in the data under another
tag.

## Tier 1 — match platform *areas* (DONE)

`core/search_context._find_platform_edges_near` (and its in-memory twin
`find_platform_edges`) now try, **precise-first**:

1. `railway=platform_edge` by `ref`
2. `railway=platform_edge` by composite `railway:track_ref` (e.g. "412/422" ⊇ "12")
3. `railway=platform` / `public_transport=platform` **area** by `ref`
4. same area tags by `local_ref`

Steps 3–4 only run when 1–2 miss, so platform_edge stations resolve **byte-for-byte
as before** (verified: Colmar A→B = 34.8 s, Berlin 1→16 = 122.1 s, Strasbourg
1→3 = 72.2 s all unchanged; `tests/test_ground_truth.py` 43/43 green). Platform
areas are walkable (`graph.is_walkable_way` only excludes `public_transport=station`),
so once matched they route with no other change.

Supported by two partial indexes (`idx_osm_ways_platform_area_ref`,
`idx_osm_ways_platform_area_local_ref`, ~250k rows each — see `schema.sql`), so
the fallback is an index scan, not a seq scan.

**Measured impact**: München Ost — platforms mapped as `public_transport=platform`
areas, no `platform_edge` — now routes (3→5 = 103.1 s, 1→4 = 32.0 s) where it
previously returned `platform_not_found`.

## Tier 2 — anchor on `stop_position` nodes (BUILT)

The biggest remaining pool is `stop_position` / `railway=stop` **nodes** (112k
with a ref — 10× platform_edge). Every station that still fails Tier 1 was
measured to carry its numeric tracks here:

- Aarau: `local_ref` 1–6, 11–13 → tracks **4, 5 present**
- Olten: `local_ref` 1–4, 7–12 → tracks **9, 12 present**
- Basel SBB: `ref`/`local_ref` 1–16 → tracks **7, 8 present** (its platform *ways*
  use sector letters A–M, but the numeric tracks are on the stop nodes)

### The catch

A `stop_position` node sits **on the track**, not on the walkable platform, and
the ETL excluded bare track geometry — so these nodes are **isolated in our
graph** (they have coordinates but belong to no way; `node_way_ids` is empty for
them). They can't be used as search endpoints directly. Measured: Aarau's track-4
and track-5 stop nodes are **12.9 m apart** (a classic island platform) but
neither is on any imported way.

### Mechanism (BUILT)

1. `station_stops` (`core/dbgen/build_platform_index.py`) — every **rail** stop_position
   / railway=stop node with a `ref`/`local_ref`, by coordinate. Resolve track *N*
   to its coordinate near the station seed. Bus/tram stops are excluded: their
   letter/number refs (Verkehrsverbund bays "A"/"B"/"C" …) collide with rail
   tracks, and two thirds of all European stop_position nodes with a ref are
   bus/tram. Without the filter, Koblenz Hbf's rail-side departure "track C"
   (actually a forecourt bus bay) resolved to a same-lettered bus stop 500 m away
   and routed a bogus 2 km "platform transfer"; now it finds no rail `C` and fails
   cleanly (`platform_not_found`). The index drops from ~112k rows to ~29k.
2. **Snap** that coordinate to the nearest node that IS in the walkable graph,
   within `STOP_SNAP_RADIUS_M` (40 m) — the platform surface (a footway or a
   platform area) beside the track. This needed a general coordinate index on
   `osm_nodes` (the DB otherwise had none, and `platform_nodes`-only snapping
   missed footway-tagged platforms); the isolated track/stop nodes are skipped
   because they touch no walkable way.
3. Return the snap node's **whole** walkable way (so SearchContext loads real
   geometry to traverse) but anchor the source/target to the single snap node
   (tagged `_snap_anchor`), so two tracks on one island platform anchor to
   *different* points — the real cross-platform walk, not a zero-distance overlap
   of the shared way.

Wired as the last fallback in `_find_platform_edges_near`, so platform_edge and
platform-area stations are untouched (`test_ground_truth` 43/43 unchanged).

### Measured (BUILT)

| station | tracks | tag reality | result |
|---|---|---|---|
| Basel SBB | 7→8 | platform ways lettered A–M; numbers on stop nodes | **26.8 m / 19.1 s** |
| Aarau | 4→5 | island platform, footway surface | **15.8 m / 11.3 s** |
| Aachen Hbf | 6→9 | underpass + two flights of stairs | **44.6 m / 51.5 s** |
| Olten | 9→12 | platforms not linked by any mapped footway | `disconnected` (real OSM gap) |

**Bounded by** whether *any* walkable geometry exists near the stop node and
whether the platforms are actually connected in OSM (Olten's aren't — same class
as Colmar's platform E). The separate island-platform fast path in the original
design proved unnecessary: snapping to the nearest walkable node already handles
island platforms (Aarau routes across the surface).

## Won't be fixed by either tier

These are genuine gaps, not resolution bugs — surfaced honestly as `unknown`:

- **Bus/tram platforms.** MOTIS sometimes routes a transfer via a non-rail
  platform: **Köln Hbf tracks 87/88** are `highway=platform`/`public_transport=platform`
  **bus** bays (not near the rail station's geometry); **München Ost track 91** is
  a **tram** platform; **Koblenz Hbf "track C"** is a forecourt bus bay. Different
  infrastructure and numbering scheme; neither tier targets non-rail modes, and
  matching them would risk confidently-wrong walks. Three guards keep these honest
  `unknown`s rather than bogus walks: `station_stops` is rail-only (above, so a
  bus ref finds no rail stop); `SearchContext.plausibility_bound_seconds` makes
  core/'s `exceeded_plausibility_bound` a real geometry bound on the *resolved*
  platforms' separation (not a flat time budget), so a walk far longer than they
  are apart is abandoned mid-search; and `api/transfers.walk_is_implausible`
  applies the same test against the two platforms' *own MOTIS coordinates* — the
  stronger signal, since a mis-resolution inflates the resolved endpoints but not
  the journey's coordinates, which core/ never sees.
- **`no_platform_data` (MOTIS side).** MOTIS omits the platform for a leg entirely
  (`p52→pNone`) — patchy and asymmetric even in DACH. There is no identifier to
  resolve; this is a data-source limit, not a `core/` concern, and is intentionally
  out of scope ("if the data doesn't exist the app won't work anyway").
- **Genuinely unmapped platforms.** A minor halt whose platforms aren't in OSM in
  *any* form (no edge, no area, no stop node with the ref) can't be routed —
  nothing to match. Rare for mainline stations.
- **Numbering-scheme mismatch with no stop-node bridge.** A station tagged with
  only sector letters where MOTIS sends a number *and* which lacks numeric stop
  nodes would be unresolvable. (Basel SBB looked like this but is actually a Tier 2
  case — its numeric tracks are on stop nodes; a true instance would need both
  conditions.)
