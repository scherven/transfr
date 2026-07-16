# TODO

Merged backlog: **app-usage triage** (2026-07-15) + the **SwiftUI live-data wiring
status**. Quick fixes are done; this tracks the rest. "Routing from the map" was
dropped intentionally.

Everything under §1–§9 concerns `ios/TransfrApp` (`TransfrUI`) — all **15 prototype
screens** (DESIGN.md §3) exist and are navigable; the work is driving them from the
real `api/` service + live-delay feed. Cross-cutting items that aren't app-screen
wiring live in **Infrastructure / UI polish / Data & maps / Investigation /
Compliance** at the bottom.

**Legend**
- 🟢 **Live-wired** — reads real contract data through the repository.
- 🟠 **Schematic / synthesized** — renders, but from hard-coded/derived values.
- 🟡 **Wired-but-unused** — the data path exists but nothing renders/consumes it.
- 🔴 **Stub / static** — placeholder or fixed example content.
- 🚧 **Blocked on API** — needs a server capability that doesn't exist yet.

---

## Screen status (all 15 exist)

| # | Screen | File | Status | Note |
|---|--------|------|--------|------|
| 1 | Plan / Input | `InputView.swift` | 🟠 | Type mode plans live; **station autocomplete** 🟢 & **editable departure** 🟢 now wired; **walk-only** 🟢 (platforms adapt to the station via `/station-platforms`, live `/walk` geometry); **paste-link has no parser**. |
| 2 | Connections | `ResultsView.swift` | 🟢 | Fully from `/journeys`. |
| 3 | The connection | `JourneyView.swift` | 🟢 | Legs/transfers/delays from live data. |
| 4 | Transfers (carousel) | `CarouselView.swift` | 🟢 | Core stats + **boarding/step-off** now live from `/walk` `boarding` (§3); coach name is the only 🚧 (needs a formation feed). |
| 5 | Walk views | `WalkView.swift` | 🟢 | Section / Levels / **3D** all project real `viz_export` geometry (`WalkGeometryViews.swift`); turn-by-turn from real `transitions`. Schematic only stands in for the sample tier. |
| 6 | AR | `ARView.swift` | 🔴 | Camera/grid mocked (v2, §5); **overlay text now real** — step-off, platform, train, distance from `/walk`. |
| 7 | Live | `LiveView.swift` | 🟠 | **Next-transfer card is live** (verdict/platforms/walk/spare/step-off); route map kept as a labelled "PREVIEW" — no CoreLocation/live feed yet (§4). |
| 8 | Settings | `SettingsView.swift` | 🟠 | Persisted & real; Theme 🟢, **step-free rides `/walk`** 🟢, **units (m/ft) applied** 🟢; makeable-%, pace, buffer still don't affect routing (§6). |
| 9 | Walk lookup | `WalkLookupView.swift` | 🟢 | Resolves the picked station to its real platforms + relation (`/station-platforms`) and projects live `/walk` geometry through the shared Section/Levels/3D canvases; schematic only for the sample tier. |
| 10 | Advanced (hub) | `AdvancedView.swift` | 🟢 | Pure navigation; nothing to wire. |
| 11 | Full station walk | `StationWalkView.swift` | 🔴 | Static Berlin platform list; no per-platform pathfind. |
| 12 | Nearest facility | `NearestFacilityView.swift` | 🔴 | Static POIs; no OSM POI query. |
| 13 | Map health | `MapHealthView.swift` | 🟠 | EU/KR figures are the real survey numbers; JP + station lists illustrative. Read-only by design. |
| 14 | Offline & regions | `OfflineRegionsView.swift` | 🔴 | Static region cards; no real download/storage management. |
| 15 | Attributions | `AttributionsView.swift` | 🟢 | Static **by design** — a licence page, no data to wire. |

---

## 1. Trip input — `InputView.swift`

- 🟢 **Station autocomplete is wired.** From/To and the walk-only station field
  share one debounced (180 ms, 2-char min) suggestion dropdown over
  `repo.stations()` → `TripModel.stations(matching:)`; tapping a row fills the
  focused field. Still sends the station **name** as the query string, not a
  resolved id — `StationSuggestion.id` is optional and the sample seed carries
  none, so the name→stop-id normalisation gap (§9) is unchanged; a bundled
  `stations.csv` for instant offline hits (§8) is still the follow-up.
- 🟢 **Departure time is editable.** ("Wire Depart" — **done.**) The "Depart" chip
  opens a sheet with a graphical date+time `DatePicker` bound to
  `TripModel.departure` (plus a "Leave now" shortcut); the chip label reflects
  Today / Tomorrow / "Wed 16". Left *unrestricted* (past times allowed). `plan()`
  already forwards `departure` to `/journeys?time=`.
- 🔴 **Remove the "Travellers" chip.** Decorative; drop it.
- 🔴 **Paste-link mode has no parser.** The UI/field exist, but nothing turns a
  Google/Apple Maps / DB Navigator link into an itinerary. 🚧 also needs the
  name→stop-id normalisation gap resolved (§9).
- 🟢 **Walk-only mode is live.** Picking a station resolves its coordinate to the
  real platform list + relation id via the new `GET /station-platforms` (built on
  `SearchContext.list_platform_refs` — the same footprint/tag ladder a `/walk`
  resolves), so the two platform inputs become **dropdowns of that station's actual
  platforms** (the medium-TODO ask — only the platforms adapt). "Show walk" fetches
  live `/walk` geometry for the resolved `(relation_id, from, to)` and
  `WalkLookupView` projects it through the shared Section/Levels/3D canvases +
  real turn-by-turn. Free-form text stays as the fallback for an unmapped station;
  the sample tier falls back to a schematic. Still sends the station **name** for
  autocomplete, so the name→stop-id normalisation gap (§9) is unchanged.

## 2. Walk renderers — `WalkView.swift` ✅

- 🟢 **Real `viz_export` geometry is drawn.** `loadGeometry()` builds a `WalkScene`
  and the three canvases in `WalkGeometryViews.swift` project it:
  - **Section** — a true longitudinal elevation from `path.points` (distance-walked
    vs level), with `transitions` coloured by kind (stairs / escalator / vertical).
  - **Levels** — a top-down floor plan per level from `ways` + the on-level path,
    picker driven by `meta.levels_present` (fractional mezzanines collapse to floors).
  - **3D** — an exploded-floor axonometric of `ways` + `path`, drag to rotate.
  Verified headless via `TransfrUITests` (Berlin 1→16 and Dortmund 11→4).
- 🟢 **Turn-by-turn is derived from real `transitions`** + endpoints (step-off →
  each level change → board). Synthesized copy remains only for the sample tier.
- 🟢 **3D view trimmed to what's legible.** The Levels + 3D canvases no longer draw
  the station's whole mapped web (Stuttgart 61 / Karlsruhe 142 / Berlin 372 context
  ways) — the connectors among them, each spanning two floors, exploded across the
  stacked levels into a forest of near-vertical lines that buried the route. Both
  now follow `core/viz/viz_render.py`: the 3D shows faint labelled floor planes, the
  route, and one riser per real `transition` coloured by connector; the scene is
  reframed on the path so the walk fills the view. (Far less per-frame draw work also
  addresses the "3D slowness" note.)
- 🟢 **Levels — vertical changes now obvious.** Each floor-change is a bold,
  connector-coloured disc with an up/down chevron and a dodged label ("Escalator ↑
  L+1") read from the current floor's perspective, over a decluttered plan (only the
  route, the start/end platform slab, and a floor tag). Replaces the tiny ambiguous
  "•/▲/▼" dots that were lost in the context web.

## 3. Boarding & step-off — `CarouselView.swift`, `LiveView.swift`, `WalkView.swift` ✅

- 🟢 **Step-off position is live.** `api/boarding.py` projects the resolved walk's
  step-off node (`node_path[0]` — the arrival-platform node the multi-source search
  actually starts from, i.e. the point closest in walk time to the departure
  platform) onto the OSM platform edge, giving a real along-platform
  `stepoff_fraction`, `platform_length_m`, and the `time_saved_s` a good position
  saves over the far end (an upper bound → shown as "up to ~N"). It rides on
  `WalkResult.boarding` from `/walk` (+ `/walks` batch), mirrored by
  `TransfrCore.BoardingGuidance`. The old hard-coded "Where to sit" box, the A–E
  sector strip with **C** lit, "coach 3", and "saves ~30 s" are gone: `BoardingCard`
  / `BoardingStrip` / `BoardingStepoffCue` (`Components/BoardingViews.swift`) render
  the real position, and the carousel + walk + Live + AR step-off cues all read it.
  The level note is now derived from the walk's real `transitions`, not fabricated.
  Verified: Berlin 1→16 = fraction 0.38 of a 430 m platform, saves up to ~3 min.
- 🟢 **Honest data gaps, not fakes.** No sector *letters* (those are painted signage
  we don't ingest — inventing them was the old mock). `coach` stays `null` with
  `reason=no_formation_feed`: the live formation feed (DB RIS / SBB / OeBB) is
  geo-blocked from a generic host (see `core/boarding/live_sources.py`), so the card
  says "coach numbers need a live formation feed" rather than guessing. A
  coarse-mapped platform (stop-position snap anchor) degrades to
  `platform_geometry_unavailable` — position-less, not wrong.
- 🚧 **Coach naming** is the one remaining piece, and it's server/data-side: wire a
  reachable formation source into `compute_boarding` (the `NormalizedFormation` →
  coach-span lookup is already built in `core/boarding/formation_model.py`).
- 🟠 **"Spot between makeable & boarding buffer looks weird"** — geometry/render fix
  on the transfer visual (unrelated to the boarding module).

## 4. Live tracking & delays — `LiveView.swift`

- 🟢 **Next-transfer card is real now.** Verdict, platforms, walk time, the *real*
  spare (layover − routed walk), the current train name (from the journey's legs),
  and the step-off cue (`BoardingStepoffCue` over `/walk` `boarding`) all come from
  live data. The fabricated "9:12" countdown, "+3" delay, "ICE 271", and the fake
  62%-progress bar are gone; the route map is kept but **labelled "PREVIEW"** so it
  no longer implies a real GPS fix.
- 🔴 **Map + position are still illustrative** — the route map/pulsing "you" aren't
  real. No `CoreLocation`, no `MapKit`, no ActivityKit Live Activity / Dynamic
  Island; that's the remaining live-tracking work.
- 🔴 **No live re-assessment.** Nothing consumes the live-delay feed (`api/live.py`)
  to re-verdict against fresh delays. 🚧 needs the realtime feed + polling/APNs. (See
  "Is live reassessment actually working?" under Investigation.)
- 🔴 **Routing from current location** — location services as the trip origin.
- 🔴 **Progressive load** — return journeys first, stream walks in once the journey
  screen is showing; transition to the results screen with live updates. **Prereq:**
  API profiling (Investigation).

## 5. AR — `ARView.swift`

- 🔴 **Mocked camera.** A drawn receding grid + glowing path, not ARKit. Real AR is
  `RealityView` + `ARGeoAnchor`/`ARImageAnchor` off the georeferenced export, gated
  on indoor positioning — the explicit v2 frontier.
- 🟢 **Overlay text is real now.** The instruction banner reads the real step-off
  direction (`/walk` `boarding`), the destination pill shows the real departure
  platform + the actual boarded train name (the i+1-th named leg, not "ICE 1197"),
  and the distance badge is the real routed walk (no "78 m" fallback). Only the
  camera/grid stays mocked.

## 6. Settings — `SettingsView.swift` / `SettingsStore.swift`

Persisted and real, but several preferences are **not yet applied**:
- 🔴 **Rebuild the settings page correctly — drop the "bounce" hack.**
- 🔴 **Verify settings actually apply — walk each toggle end-to-end.**
- 🟢 **Theme** — fully wired to `.preferredColorScheme`.
- 🟢 **Step-free** — rides every `/walk` request: `WalkView` reads `settings.stepFree`
  into `WalkKey(transfer:stepFree:)` and re-keys its geometry fetch so the toggle
  refetches the elevator-free variant. Still **not** applied to the verdict/journey
  routing profile server-side (needs a step-free profile server-side).
- 🔴 **Add a "no elevators" toggle → feed into routing.** core already has
  `--no-elevators` / `avoid_elevators`; surface it as a setting and thread it into the
  routing profile (pairs with step-free above).
- 🟠 **Makeable %** — doesn't re-verdict. Could recompute client-side from
  `layover_s`/`walk_time_s`, but **deferred as not-a-quick-win:** a safe re-verdict
  must not override the server's honest `unknown`/`infeasible` (and the boarding
  buffer factors into feasibility too). Product-semantics pass, not mechanical wiring.
- 🟠 **Walking pace / boarding buffer** — display only; should scale walk time /
  feed the server's buffer check.
- 🟢 **Units** — `Fmt.distance(_:imperial:)` renders m or ft, and every live distance
  site threads `settings.units` (`WalkView`, `CarouselView`'s `TransferDetailCard`,
  the `ARView` badge). Remaining `"NN m"` strings on 🔴 stub screens stay literal
  until those screens go live.
- 🔴 **Live Activity / auto-AR lead** — toggles persist but nothing consumes them.

## 7. Advanced tools — Station walk / Nearest facility / Map health / Offline

- 🔴 **Full station walk** (`StationWalkView`) — static Berlin list; a live build runs
  one pathfind per platform from the source (needs a `/station-walk` style endpoint
  or N `/walk` calls). Rows navigate to the **static** lookup, not the real pair.
- 🔴 **Nearest facility** (`NearestFacilityView`) — static POIs; needs the OSM
  `amenity`/`shop` layer from `viz_export` details + routing to each. 🚧 endpoint.
- 🟠 **Map health** (`MapHealthView`) — EU/KR bars are the real `stitch_survey.py`
  numbers; JP + the representative-station lists are illustrative. Read-only by design.
  **Add:** let the user query a single station.
- 🔴 **Offline & regions** (`OfflineRegionsView`) — static cards; no real region
  download, prefetch, or storage accounting. 🚧 device-side + packaging work.
- **Placement:** move **Advanced** to a button next to Settings, or go tab-based.

## 8. Offline & caching (cross-cutting)

- 🔴 **No `CachingRepository`.** The `JourneyRepository` seam was designed for a
  caching decorator; none exists. Planned journeys and walk geometry aren't persisted,
  so "reopen a planned trip offline" doesn't work.
- 🔴 **Save searches (persistence)** — persist past searches for reuse.
- 🟡 **Batch walk prefetch is never called.** `TransfrClient.walks()` + `WalkKey(transfer:)`
  exist and are tested, but selecting a journey doesn't prefetch its transfers'
  geometry. Fire `walks([...])` on `select(_:)` and cache the results.
- 🔴 **No bundled `stations.csv`** — offline autocomplete corpus isn't in the bundle;
  `SampleRepository` uses a 9-station seed.

## 9. Data-source configuration & states

- 🟢 **Repository is live by default**, resolved by `Data/AppConfig.swift` from the
  environment (`TRANSFR_API_URL` / `TRANSFR_API_KEY`), injected by the Xcode scheme
  (`project.yml`). `TRANSFR_USE_SAMPLE=1` forces the offline tier; `TRANSFR_AUTOPLAN=1`
  jumps straight to live results on launch. Settings' "Bundled sample" label is stale
  (cosmetic).
- 🟠 **Minimal error/empty states** — `plan()` surfaces a message on the CTA, but no
  retry, no empty-results state, no per-screen loading skeletons.

---

## Already fully live-ready 🟢

The spine is done — this is a punch-list, not a teardown:

- **Plan flow** end-to-end via `LiveRepository` → `/journeys` (`time=` /
  `from_platform`/`to_platform` fixes).
- **Connections / timeline / carousel core data** — times, platforms, trains,
  durations, changes, layover, walk time & distance, delays.
- **Verdict system** — pills/nodes/rings + worst-wins rollup, honest `unknown(reason)`.
- **`/walk` fetch + Section/Levels/3D render**, **turn-by-turn**.
- **Theme, navigation (all 15 routes), Settings persistence, units (m/ft), step-free walk.**

---

## Infrastructure

- **[infra] 401 error → switch to a *named* tunnel.** Infra config; stops the
  intermittent 401s.

## UI polish

- ~~**Loading screen** — the `t` from the favicon expands; the red dot writes out the
  rest of "transfr".~~ — **done** (SwiftUI `LaunchView.swift`: camera dolly off the
  favicon `t` → wordmark, red pen writes "ransfr", end pose = the main-screen
  wordmark; plays once on cold launch then reveals InputView; respects reduced motion).
- **Main screen wordmark** — put a green dot on top of the `t` and a red dot at the
  end of the `r`.
- **"Recent" as its own section** (or surfaced per-section).
- **Advanced placement** — button next to Settings, or tab-based (also §7).

## Data & maps

- **Map of routes — a real map;** each route drawn where it actually goes.
- **[data] ICE trains have no names** — data gap or formatting bug? (see Investigation).
- **[data] Explore disconnections in Europe.**
- **[data] Explore disconnections in Korea** — "really nothing"? try another source.
- **[data] Explore downloading other country DBs / station lists.**

## Investigation (answer before building)

- **Profile the API** to find the slow part (prereq for progressive load, §4).
- **Why are the 3D views so slow?** They should be instant. Do they restart on tab
  switch? (render trim is a separate item, §2).
- **ICE trains have no names** — data gap or formatting bug?
- **"Where to sit" is stale** — find the source and why it isn't updating (§3).
- **Is live re-assessment actually working?** (§4).
- **Disconnections** — Europe and Korea (also under Data & maps).

## Compliance

- **Confirm all API use is legal** — ToS audit across the upstream sources.

---

## Suggested order

1. ~~Render fetched `viz_export` in Section/Levels/3D (§2)~~ — **done** (+live repo §9).
2. ~~Station autocomplete + editable departure time (§1)~~ — **done**.
3. ~~Apply Settings client-side: step-free on walk keys, units (m/ft)~~ — **done**
   (makeable-% re-verdict deferred, §6).
4. **Investigation wave** — profile the API, 3D slowness, ICE names, ToS audit,
   disconnections. Cheap, mostly read-only, and unblocks the rest.
5. **Client-side quick wins** — named tunnel [infra], UI polish (loading screen,
   wordmark dots), Settings rebuild + verify + no-elevators toggle, drop Travellers,
   real walk-only fetch (§1), Recent section, save searches.
6. **Batch prefetch + `CachingRepository`** (§8) — offline + speed.
7. **Real walk-lookup / station-walk / nearest-facility** off `viz_export` (§1, §7).
   🚧 some need endpoints.
8. **Map of routes** — real per-route map.
9. ~~**Boarding / step-off data**~~ (§3) — **done** (position via `/walk` `boarding`);
   only coach naming remains, 🚧 blocked on a reachable formation feed.
10. **Live tracking / re-assessment + routing-from-location + progressive load**
    (§4) — 🚧 server / profiling first.
11. **3D → AR** (§2/§5).
