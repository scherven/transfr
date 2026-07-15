# SwiftUI app — live-data TODO

Everything in `ios/TransfrApp` (`TransfrUI`) that is **not yet fully wired to live
data**. All **15 prototype screens** (DESIGN.md §3) now exist and are navigable on
`SampleRepository`; this list is what stands between that and a build completely
driven by the real `api/` service (and the live-delay feed).

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
| 1 | Plan / Input | `InputView.swift` | 🟠 | Type mode plans live; **paste-link has no parser**; walk-only jumps to the static lookup. No autocomplete. |
| 2 | Connections | `ResultsView.swift` | 🟢 | Fully from `/journeys`. |
| 3 | The connection | `JourneyView.swift` | 🟢 | Legs/transfers/delays from live data. |
| 4 | Transfers (carousel) | `CarouselView.swift` | 🟠 | Core stats live; **boarding module is illustrative** (§3 below). |
| 5 | Walk views | `WalkView.swift` | 🟡 | Section/Levels are schematic; real `viz_export` is fetched but **not drawn**. |
| 6 | AR | `ARView.swift` | 🔴 | Mocked camera. Real ARKit/RealityKit is v2 (§7.7). |
| 7 | Live | `LiveView.swift` | 🔴 | Map + countdown are illustrative; no CoreLocation/live feed. |
| 8 | Settings | `SettingsView.swift` | 🟠 | Persisted & real, but **preferences don't affect routing yet** (§6). Theme 🟢. |
| 9 | Walk lookup | `WalkLookupView.swift` | 🔴 | Static Berlin 1→16; doesn't resolve the typed station/platforms. |
| 10 | Advanced (hub) | `AdvancedView.swift` | 🟢 | Pure navigation; nothing to wire. |
| 11 | Full station walk | `StationWalkView.swift` | 🔴 | Static Berlin platform list; no per-platform pathfind. |
| 12 | Nearest facility | `NearestFacilityView.swift` | 🔴 | Static POIs; no OSM POI query. |
| 13 | Map health | `MapHealthView.swift` | 🟠 | EU/KR figures are the real survey numbers; JP + station lists illustrative. Read-only by design. |
| 14 | Offline & regions | `OfflineRegionsView.swift` | 🔴 | Static region cards; no real download/storage management. |
| 15 | Attributions | `AttributionsView.swift` | 🟢 | Static **by design** — a licence page, no data to wire. |

---

## 1. Trip input — `InputView.swift`

- 🟡 **Station autocomplete is not in the UI.** `repo.stations()` exists at every
  layer but the From/To (and walk-only station) fields are plain `TextField`s — no
  suggestion list, no debounce, no select-to-fill. Origin/destination go to the
  server as raw strings. **Needed:** a suggestions dropdown, ideally over a bundled
  `stations.csv` for instant offline hits (§13.2), resolving to a station id.
- 🔴 **Departure time is not editable.** The "Depart" chip is display-only; no
  picker. `TripModel.departure` is hard-defaulted to today 08:34.
- 🔴 **"Travellers" chip is decorative.**
- 🔴 **Paste-link mode has no parser.** The UI/field exist, but nothing turns a
  Google/Apple Maps / DB Navigator link into an itinerary. 🚧 also needs the
  name→stop-id normalisation gap resolved (§9).
- 🔴 **Walk-only mode doesn't use its inputs.** Tapping "Show walk" navigates to the
  **static** Berlin 1→16 lookup regardless of the station/platforms typed. **Needed:**
  resolve the two refs and fetch that walk's `viz_export` (the `/transfer` +
  `/walk` path exists in `TransfrClient`).

## 2. Walk renderers — `WalkView.swift`

- 🟡 **Real `viz_export` geometry is fetched but never drawn.** `loadGeometry()`
  stores `@State geometry`, but `SectionCanvas`/`LevelCanvas` ignore it and draw
  schematically. **The headline item:** the "one contract → four renderers" payoff
  only lands once these project `export.path.points` / `ways` / `transitions`.
  🚧 also needs `/walk` to return exports.
- 🟠 **Section & Levels are schematic**, inferred from `hasLevelChange`
  (`verdict != feasible`) — a proxy, not real level data.
- 🟠 **Turn-by-turn steps are synthesized copy** ("sector C", "underpass", "21 steps"),
  not derived from `transitions`.
- 🔴 **3D tab is a placeholder** — no `WKWebView` (stage-0 `viz_render`) or SceneKit
  port (§13.4).

## 3. Boarding & step-off — `CarouselView.swift`, `LiveView.swift`, `WalkView.swift`

- 🟠 **All boarding guidance is illustrative** and hard-coded per verdict: the
  "Where to sit" box, the A–E sector strip with **C** lit, "walk toward the front",
  "coach 3", "saves ~30 s" / "barely matters", and the level note. The same synthetic
  copy feeds the Live step-off cue and the AR banner. 🚧 Needs the server to deliver
  coach/sector + step-off in the plan payload (`boarding` / `formation_model`, §13.6);
  no Swift model exists for it yet.

## 4. Live tracking & delays — `LiveView.swift`

- 🔴 **Everything is illustrative** — the route map, the pulsing "you", the "9:12"
  countdown, the "+3" delay, the progress bar. No `CoreLocation`, no `MapKit`, no
  ActivityKit Live Activity / Dynamic Island (§13.7).
- 🔴 **No live re-assessment.** Nothing consumes the live-delay feed (`api/live.py`)
  to re-verdict against fresh delays. 🚧 needs the realtime feed + polling/APNs.

## 5. AR — `ARView.swift`

- 🔴 **Mocked camera.** A drawn receding grid + glowing path, not ARKit. Real AR is
  `RealityView` + `ARGeoAnchor`/`ARImageAnchor` off the georeferenced export, gated
  on indoor positioning (§7.7, §13.5) — the explicit v2 frontier.

## 6. Settings — `SettingsView.swift` / `SettingsStore.swift`

Persisted and real, but the preferences are **not yet applied**:
- 🟢 **Theme** — fully wired to `.preferredColorScheme`.
- 🟠 **Step-free** — not sent on walk requests. Should set `WalkKey.stepFree` (and a
  routing profile server-side) on every walk/verdict.
- 🟠 **Makeable %** — doesn't re-verdict. Could recompute verdicts client-side from
  `layover_s`/`walk_time_s` against the threshold (the one setting that can act
  offline).
- 🟠 **Walking pace / boarding buffer** — display only; should scale walk time /
  feed the server's buffer check.
- 🟠 **Units** — always metric; no metric↔imperial conversion in `Fmt`.
- 🔴 **Live Activity / auto-AR lead** — toggles persist but nothing consumes them.

## 7. Advanced tools — Station walk / Nearest facility / Map health / Offline

- 🔴 **Full station walk** (`StationWalkView`) — static Berlin list; a live build runs
  one pathfind per platform from the source (needs a `/station-walk` style endpoint
  or N `/walk` calls). Rows navigate to the **static** lookup, not the real pair.
- 🔴 **Nearest facility** (`NearestFacilityView`) — static POIs; needs the OSM
  `amenity`/`shop` layer from `viz_export` details + routing to each. 🚧 endpoint.
- 🟠 **Map health** (`MapHealthView`) — EU/KR bars are the real `stitch_survey.py`
  numbers; JP + the representative-station lists are illustrative. Read-only by design.
- 🔴 **Offline & regions** (`OfflineRegionsView`) — static cards; no real region
  download, prefetch, or storage accounting. 🚧 device-side + packaging work.

## 8. Offline & caching (cross-cutting)

- 🔴 **No `CachingRepository`.** The `JourneyRepository` seam was designed for a
  caching decorator (§13.9); none exists. Planned journeys and walk geometry aren't
  persisted, so "reopen a planned trip offline" doesn't work.
- 🟡 **Batch walk prefetch is never called.** `TransfrClient.walks()` + `WalkKey(transfer:)`
  exist and are tested, but selecting a journey doesn't prefetch its transfers'
  geometry. Fire `walks([...])` on `select(_:)` and cache the results.
- 🔴 **No bundled `stations.csv`** — offline autocomplete corpus (§13.2) isn't in the
  bundle; `SampleRepository` uses a 9-station seed.

## 9. Data-source configuration & states

- 🔴 **Repository hard-coded to `.sample`** in `App/TransfrApp.swift`; the `.live(url)`
  line is commented out. No runtime toggle / base-URL setting. Settings shows a
  read-only "Bundled sample".
- 🟠 **Minimal error/empty states** — `plan()` surfaces a message on the CTA, but no
  retry, no empty-results state, no per-screen loading skeletons.

---

## Already fully live-ready 🟢

The spine is done — this is a punch-list, not a teardown:

- **Plan flow** end-to-end via `LiveRepository` → `/journeys` (with the `time=` /
  `from_platform`/`to_platform` fixes).
- **Connections / timeline / carousel core data** — times, platforms, trains,
  durations, changes, layover, walk time & distance, delays — all from live contract
  fields.
- **Verdict system** — pills/nodes/rings + worst-wins rollup, honest `unknown(reason)`.
- **`/walk` fetch call** — wired via `model.walk(for:)`; only *rendering* the result
  is outstanding (§2).
- **Theme, navigation (all 15 routes), and Settings persistence.**

## Suggested order

1. Render fetched `viz_export` in Section/Levels (§2) — unlocks the core value.
2. Station autocomplete + editable departure time (§1).
3. Apply Settings that can act client-side: step-free on walk keys, makeable-% re-verdict, units (§6).
4. Batch prefetch + `CachingRepository` (§8) — offline + speed.
5. Real walk-lookup / station-walk / nearest-facility off `viz_export` (§1, §7). 🚧 some need endpoints.
6. Boarding data from the plan payload (§3) — 🚧 server first.
7. Live tracking / re-assessment (§4) — 🚧 server first.
8. 3D → AR (§2/§5).
