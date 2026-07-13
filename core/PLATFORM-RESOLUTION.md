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

## Tier 2 — anchor on `stop_position` nodes (PROPOSED)

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

### Proposed mechanism

1. Resolve track *N* → the `stop_position`/`railway=stop` node whose
   `ref`/`local_ref` = *N*, near the station seed (a new indexed lookup; needs a
   partial index on stop nodes' ref/local_ref, or a `station_stops` materialized
   table analogous to `station_points`).
2. **Snap** that node's coordinate to the nearest node that *is* in the walkable
   graph (within ~25 m — the platform surface beside the track), and use that as
   the search source/target. Reuses the existing proximity machinery.
3. **Island-platform fast path**: if both tracks' stop nodes are < ~20 m apart
   with no third track between them, return `feasible` directly — a cross-platform
   step needs no routing (handles Aarau 4↔5 even where the platform surface isn't
   mapped as a way at all).

### What it would unlock, and its limits

Unlocks the large class of stations that carry track numbers only on stop nodes
(Aarau, Olten, Basel SBB, …). **Bounded by** whether walkable platform geometry
exists near the stop node to snap onto — where a station's platform surfaces
aren't mapped as ways at all, only the island-platform fast path (step 3) can
answer, and only for adjacent tracks. Effort: moderate `core/` change (new
resolution strategy + a spatial snap + an index/table); it also needs the
snap-target search to stay cheap per transfer.

## Won't be fixed by either tier

These are genuine gaps, not resolution bugs — surfaced honestly as `unknown`:

- **Bus/tram platforms.** MOTIS sometimes routes a transfer via a non-rail
  platform: **Köln Hbf tracks 87/88** are `highway=platform`/`public_transport=platform`
  **bus** bays (not near the rail station's geometry); **München Ost track 91** is
  a **tram** platform. Different infrastructure and numbering scheme; neither tier
  targets non-rail modes, and matching them would risk confidently-wrong walks.
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
