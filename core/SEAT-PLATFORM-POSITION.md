# Seat → platform-position: findings, conclusions, and future work

**Status:** paused 2026-07-12. This documents a self-contained workstream that
extends transfr from *platform-to-platform* routing to *seat-to-platform*
routing. All code described here is committed and tested; nothing here is a
proposal-only sketch unless explicitly marked "not yet built." Read this before
picking the thread back up.

---

## 1. The question

transfr today answers *"which platform → which platform, and can I walk it in
time."* It seeds the walk search from **every node of the arrival platform
edge** — i.e. "you could have alighted anywhere along the platform," which is
the correct model when you know nothing about where the traveller is.

But a mainline platform is 300–430 m long. "You arrive on platform 7" hides a
multi-minute walk difference depending on whether your coach stopped at sector A
or sector G. If we know the traveller's **seat**, we can pin down where they
physically step off, and give a materially better transfer estimate.

This workstream built the front of that chain and proved it end-to-end.

## 2. Where it fits — the pipeline

```
seat/coach ──(formation feed)──▶ offset along platform (metres)
           ──(platform geometry)──▶ a lat/lon point
           ──(this work)──▶ seed the walk search from that point
           ──(existing algorithm)──▶ the real door-to-platform route
```

**The key insight that made this cheap:** the algorithm already solves the last
hop. transfr's `SearchContext` seeds Dijkstra/A\* from a *set* of source nodes.
The seat doesn't need a new algorithm — it just replaces "all platform-edge
nodes at distance 0" with "the specific alighting point." Everything downstream
is unchanged.

**Only the START matters, not the end.** You can board a *departing* train at
any coach, so the target stays "any node on the departure platform" (the
existing multi-target behaviour). The seat only fixes where you start walking.

## 3. Architecture — two pipelines, one contract

The hard rule of this workstream: **the data-process pipeline and the algorithm
pipeline stay separate.** Data adapters format their output into the types the
algorithm already consumes; they never reach into the algorithm. The only things
crossing the boundary are `NormalizedFormation` / `TrainFormation` and
`PlatformGeometry`.

```
  DATA-PROCESS SIDE                    │  ALGORITHM SIDE (pre-existing, untouched)
  ─────────────────                    │  ────────────────────────────────────────
  live_sources.py   (real feeds)       │  boarding.py       (seat → point → route)
  formation_providers.py (per-operator)│  dijkstra.py / algo_*.py / search_context.py
  formation_model.py (normalize) ──────┼──▶ TrainFormation / PlatformGeometry
                                       │  graph.py (edge weights, haversine)
```

No tested algorithm file was modified to add any of this. `boarding.py` is new
but is itself part of the algorithm side (it wraps `dijkstra.shortest_path`
unchanged).

## 4. What was built

| Module | Side | Role | Tests |
|---|---|---|---|
| [`core/boarding.py`](boarding.py) | algorithm | `PlatformGeometry`, `TrainFormation`, seat→point (`resolve_alighting_point`), graph seeding (`insert_start_point`, `boarding_source_distances`), `find_path_from_seat`. Pure, DB-independent. | [`test_boarding.py`](../tests/test_boarding.py) — 21 |
| [`core/formation_model.py`](formation_model.py) | data | Normalizes any operator's data into `NormalizedFormation`; resolution ladder metres→sector→order; `PlatformSectorMap` (incl. OSM section-sign constructor); `to_train_formation()` bridge. | covered via providers |
| [`core/formation_providers.py`](formation_providers.py) | data | Per-operator adapters (DE/CH/AT/NL/FR/GB) + capability-gap entries (IT/ES/JP); `promise` scoring; `rank_providers()`. Schemas *representative*. | [`test_formation_providers.py`](../tests/test_formation_providers.py) — 47 |
| [`core/live_sources.py`](live_sources.py) | data | **Live** fetch from real APIs + real-schema parsers: `fetch_db_departures`, `fetch_transitous_plan`, `parse_wagenreihung`, `fetch_db_formation`, `sector_map_from_wagenreihung`. | [`test_live_sources.py`](../tests/test_live_sources.py) — 8 offline + 3 live (gated) |
| [`core/pull_live_demo.py`](pull_live_demo.py) | data | Runnable end-to-end demo on real data. | — |
| [`tests/fixtures/`](../tests/fixtures/) | — | Real captures: a derf departure board, a Transitous itinerary; plus the real DB Wagenreihung schema. | — |

**Test totals:** the pure suite is **115 passed, 4 skipped** (3 live tests gated
behind `TRANSFR_LIVE=1`; 1 pre-existing hash-order-flaky bidirectional test,
skipped with a documented reason — unrelated to this work).

### The seam into the DB-backed system (important, and only half-done)

`boarding.insert_start_point()` implements the pure form: it inserts a `START`
sentinel node into a graph and calls `dijkstra.shortest_path` unchanged.
`boarding.boarding_source_distances()` is the **bridge** to the real system: it
returns `{platform_node: initial_distance_seconds}`, which is exactly how a
DB-backed `SearchContext` would seed its search — replacing today's "every
platform node at distance 0" with "the two nodes bracketing the alighting point,
at their partial-walk distances." The two forms are tested to agree. **Wiring
this into the live `SearchContext`/`algo_*` path is not yet done** (see §8).

## 5. Findings — data sources

### 5.1 Coach-formation feeds, by operator

Positional granularity varies enormously; that's the whole story.

| Operator | Granularity | Openness | Notes |
|---|---|---|---|
| **CH — SBB** | sector | open (key) | opentransportdata.swiss Train Formation Service; per-vehicle sector + track. The standout for open access. |
| **DE — Deutsche Bahn** | **metres** + percent + sector | gated / geo-blocked | Richest positional data of anyone (exact metres). Official RIS is contractual; the public `ist-wr`/`apps-bahn` hosts are geo-restricted (see §6). |
| **AT — ÖBB** | sector | partner | Vehicle-layout API; united/divided sets common. |
| **FR — SNCF** | order only | open | Composition (car count, class); platform sector rarely in open data. |
| **GB — National Rail** | order (+ loading) | open | Darwin feed; coach positional/sector data still nascent. |
| **NL — NS** | order only | partner | Composition + per-coach crowding, no platform sector in the open feed. |
| **IT / ES / JP** | none | — | No usable open coach-position feed found (JP ODPT is open but exposes no car-stop position). |

### 5.2 The promise ranking (computed, not asserted by hand)

`formation_providers.rank_providers()` scores `granularity × openness`
(granularity weighted higher — an exact metre you must license still beats an
open feed that only knows coach order). Result:

```
 1  CH  SBB                    open     sector    promise 12
 2  DE  Deutsche Bahn          gated    metres    promise 11
 3  AT  ÖBB                    partner  sector    promise 10
 4  FR  SNCF                   open     order     promise  9
 5  GB  National Rail (Darwin) open     order     promise  9
 6  NL  NS                     partner  order     promise  7
 —  IT / ES / JP               —        none      promise  0
```

### 5.3 The geometry half — OSM `railway:platform:section`

To turn "sector C" into a coordinate you need to know where C is. OSM has
`railway:platform:section` nodes (the geolocated sector signs), and **transfr
already ingests OSM** — but:

- Coverage is **sparse: 3,048 nodes globally**, all nodes, values A–G, and
  (being an OpenRailwayMap/DACH convention) heavily concentrated in
  German-speaking countries.
- **The current extract drops them.** [`extract_europe.sh`](extract_europe.sh)
  filters on `railway=platform,platform_edge,…`; the section key isn't in the
  list, so section nodes survive only incidentally. Fix is one line (see §8).

Where metres are available (DB), you don't need the section map — you can
interpolate directly along the platform. `live_sources.sector_map_from_wagenreihung()`
even derives a sector→metre map straight from the feed's own `allSektor` block.

## 6. Findings — live API reachability (measured 2026-07-12, generic non-DE host)

This was probed hard. Concrete results, saved to memory as well:

**Reachable — used live in the pipeline:**
- `dbf.finalrewind.org/{eva}.json` — real DB departure boards (train, time,
  platform, class, route, delays). eva 8000105 = Frankfurt Hbf.
- `api.transitous.org/api/v5/plan` — MOTIS 2, real per-stop platform/track +
  coordinates, no key. (Same API `journeys.py` already uses.)

**Blocked — the actual sectors/metres:**
- `ist-wr.noncd.db.de` — **NXDOMAIN** (the formation host doesn't resolve
  outside DB/DE networks; `iris.noncd.db.de` *does* resolve — it's host-specific).
- `www.apps-bahn.de/wr/wagenreihung/1.0/…` — TLS handshake completes (cert
  `CN=bahn.de`, valid), then the host **silently drops the HTTP request** for
  non-DE IPs. A geo-block. This is the canonical endpoint the community
  libraries use.
- `bahn.expert` / `marudor.de` proxy the above but their current REST paths are
  opaque (SPA/trpc; `/api/reihung/*` and `/api/coachSequence/*` all 404).
- derf `/_wr/{train}/{datetime}` is deprecated → always
  `{"error":"Ambiguous station name"}`.

**Other constraints:** the DB Wagenreihung window is **now … +2 h only**, and
not queryable at a train's terminus.

**Consequence in code:** `live_sources.parse_wagenreihung()` is built to the
real `ist-wr`/`apps-bahn` wire schema
(`data.istformation.allFahrzeuggruppe[].allFahrzeug[]` with
`wagenordnungsnummer`, `kategorie`, `fahrzeugsektor`,
`positionamhalt.{startmeter,endemeter,startprozent,endeprozent}`, `allSektor`,
`halt`) — field names confirmed against `juliuste/db-wagenreihung`'s live
client. It produces a real `NormalizedFormation` the instant it runs from a
reachable network; `fetch_db_formation()` raises `FormationUnavailable` (never a
raw traceback) when blocked, so callers can degrade to a platform-level answer.

## 7. Conclusions

1. **The feature works.** On the real DB Wagenreihung schema, coach 11 vs coach
   18 of the same ICE on the same platform routed to a **185 m / 132 s**
   difference — the exact signal the feature exists to capture. Monotonicity
   (further coach ⇒ longer-or-equal transfer) holds across every operator and
   hundreds of seats tested.

2. **The data pipeline is real and reachable — except the crux.** Live
   departures and live platform tracks pull today. The coach-formation feed
   (the crux) is reachable in principle but **geo-blocked from a generic host**;
   the adapter is real-schema and one network hop from live.

3. **Target the DACH corridor, prototype on SBB first.** SBB is the best
   starting point — fully open, sector-level, no contract — and it overlaps
   exactly where DB (metre precision) and OSM section-sign coverage are richest.
   Everything outside DACH is order-only today (equal-division placement: right
   ordering, coarse position), so degrade gracefully there.

4. **Sector granularity is genuinely coarse.** SBB's "coach in sector C" is a
   ~100 m zone, not a 26 m coach (the tests show two coaches sharing a sector).
   Only DB's metres avoid this. Set expectations accordingly.

## 8. Recommendations for future work (prioritized)

1. **Get real formation flowing.** Two independent paths, either unblocks it:
   - **DE egress:** run `fetch_db_formation()` from a German IP / CI / proxy
     (add a `HTTPS_PROXY`-style env knob). The adapter and live tests
     (`TRANSFR_LIVE=1`) are ready; this is the shortest path to real DB data.
   - **SBB key:** register on opentransportdata.swiss and add an `SBBLive`
     provider in `live_sources.py` mirroring `parse_wagenreihung` — open,
     sector-level, no geo-block. This is the recommended *first* real
     integration.

2. **Wire the seam into the DB-backed algorithm.** `boarding_source_distances()`
   already yields the `{node: initial_distance}` seed. Add a `SearchContext`
   entry point that (a) resolves the real OSM `platform_edge` polyline for the
   arrival ref into a `PlatformGeometry`, and (b) seeds `ctx.sources` from the
   alighting point instead of every platform node. This replaces the synthetic
   `straight_geometry_for()` with real station geometry. The pure logic is done
   and tested; this is the integration.

3. **Capture OSM section signs.** Add `n/railway:platform:section` to the
   `osmium tags-filter` call in [`extract_europe.sh`](extract_europe.sh) so the
   3,048 geolocated sector nodes are ingested deterministically, then build
   `PlatformSectorMap` from them for sector-only feeds (SBB/ÖBB) where no metres
   exist. Calibrate against DB metres where both are present.

4. **Resolve orientation and united/divided sets against real data.** The model
   handles both (`reversed` flag; `group` per coach), but they're only tested on
   synthetic/representative payloads. Verify against real ÖBB/DB formations
   (which sector is the A-end for a given direction of travel; how split
   portions are numbered).

5. **Richer seat→door model.** `TrainFormation.seat_offset_m` is a linear
   placeholder (seat evenly across its coach). Real coaches have ~2 doors and
   non-linear seat maps; per-coach seat counts vary by class. Swap in a door
   model when a feed exposes door positions — nothing else depends on *how* the
   offset is derived.

6. **End-to-end on a live station.** Chain it: derf departures → pick a train →
   real formation (once §1 done) → real OSM geometry (§2) → real transfer time,
   and compare against transfr's current platform-level estimate to quantify the
   improvement.

## 9. Limitations & caveats

- `formation_providers.py` schemas are **representative** (real field names,
  synthetic values) — a modeling exercise. `live_sources.parse_wagenreihung` is
  the **real** wire format. Two DB parsers exist for this reason; don't conflate
  them.
- `tests/fixtures/wagenreihung_ice124.json` is the **real schema** with
  representative values (the endpoint is geo-blocked, so it's not a live
  capture). The departure-board and Transitous fixtures **are** real captures.
- Formation data is a long-distance/high-speed thing. Regional/commuter and most
  of Europe outside DACH will have nothing; the system must fall back to today's
  platform-level answer.
- Licensing constrains what can ship publicly: SBB open ✓; SNCF open-ish; DB RIS
  contractual (community wrappers carry ToS risk if productized).

## 10. How to run

```bash
# pure suite (no network)
.venv/bin/python -m pytest tests/test_boarding.py tests/test_formation_providers.py \
    tests/test_live_sources.py -q

# live layer against the real APIs
TRANSFR_LIVE=1 .venv/bin/python -m pytest tests/test_live_sources.py -q -k live

# runnable demo (pulls real departures + tracks, routes on real formation schema)
.venv/bin/python core/pull_live_demo.py
```

## 11. Related

- Live-API reachability is also saved to session memory
  (`formation-api-reachability`).
- The original data-source deep dive and per-operator survey that kicked this
  off is summarized in §5.
- Prior platform-pathfinding rewrite context: [`HANDOFF.md`](HANDOFF.md).
