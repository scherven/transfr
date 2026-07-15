# Transfr — iOS client

SwiftUI + ARKit client for the platform-transfer engine. This directory holds
the native app and its shared logic package.

## Architecture (the short version)

The phone is a **thin client** over the FastAPI service (`api/`). The 24 GB
routing database and the `core/` pathfinder stay server-side; the phone consumes
two stable JSON contracts and caches their outputs for offline use:

- **`api/schemas.py`** → journeys / verdicts / station suggestions.
- **`core/viz_export.py`** → one self-contained walk geometry per transfer, the
  single input to all four walk renderers (section / per-level / 3D / AR).

See `design/DESIGN.md` §13 for the full mapping and the data-tiering / offline
strategy.

## `TransfrCore` (Swift Package)

`TransfrCore/` is a platform-agnostic SwiftPM package with the value types,
decoding, verdict logic, and the async API client — no UI. Keeping it as a
package (not app-target files) means it tests in seconds without a simulator and
ports cleanly if the app shell is ever rebuilt.

| File | What |
|---|---|
| `Verdict.swift` | `enum Verdict` + worst-wins `rolledUp()` (port of `api/pipeline.py:rollup_verdict`) |
| `Contracts.swift` | `Journey`/`Leg`/`Transfer`/`Place`/… mirroring `api/schemas.py` |
| `VizExport.swift` | `VizExport` mirroring the `core/viz_export.py` JSON (keystone contract) |
| `TransfrJSON.swift` | the one configured `JSONDecoder`/`Encoder` (`.convertFromSnakeCase`) |
| `TransfrClient.swift` | async `URLSession` client over `/journeys`, `/stations`, `/transfer`, `/walk`, `/walks` |

### Fetching walk geometry

`/journeys` returns the lean verdict spine. The drawable per-transfer walk
geometry is fetched separately (so journeys stays small and each walk caches
independently):

```swift
let client = TransfrClient(baseURL: url)
let plan = try await client.journeys(from: "Hamburg", to: "Stuttgart")

// Prefetch a selected journey's walks in ONE round trip, then cache them:
let keys = plan.journeys[0].transfers.compactMap { WalkKey(transfer: $0) }
let walks = try await client.walks(keys)          // POST /walks
for w in walks.walks where w.ok {
    render(w.export!)                              // VizExport → 4 renderers
}

// …or one walk on demand (GET /walk, HTTP-cacheable):
let w = try await client.walk(relationId: 5688517, from: "1", to: "16")
```

`WalkKey(transfer:)` returns nil when a transfer never resolved a walk
(`no_platform_data` etc.) — those transfers simply have no geometry to show.

## Running the tests

The suite decodes the **Python engine's own outputs** as golden files, so any
server-side contract drift fails a Swift decode immediately.

```sh
# From ios/TransfrCore — reliable path, uses the iOS Simulator SDK:
xcodebuild test -scheme TransfrCore -destination 'platform=iOS Simulator,name=iPhone 16'
```

> **Note:** `swift test` (the macOS toolchain path) currently fails on this
> machine because stray Homebrew headers in `/usr/local/include` shadow the macOS
> SDK and break the clang module build. The iOS-Simulator `xcodebuild` path above
> avoids it. If you clear `/usr/local/include`, `swift test` works too.

## Regenerating the test goldens

When `api/schemas.py` or a `viz_export` shape changes, regenerate and re-run:

```sh
# From the repo root, with the project venv (pydantic lives in .venv):
.venv/bin/python ios/TransfrCore/Tests/generate_fixtures.py
```

The `viz_*` goldens are copied from `core/viz_out/`; regenerate those first with
`core/viz/viz_export.py --relation 5688517 --ref1 1 --ref2 16 [--details]`. The
walk-envelope goldens wrap `Fixtures/viz_small_found.json` (a committed Berlin
Hbf 1→2 `GET /walk` output); regenerate it from the running API if the
`viz_export` shape changes.

## `TransfrApp` / `TransfrUI` (the SwiftUI app)

`TransfrApp/` is a second SwiftPM package whose `TransfrUI` library holds the app —
screens, theme, the observable trip model, and the **agnostic data layer** — and
depends only on `TransfrCore` for the wire contracts. Keeping the UI in a package
(not app-target files) means the whole surface builds and previews via
`xcodebuild -scheme TransfrApp` on the Simulator SDK, exactly like `TransfrCore`.

```sh
# From ios/TransfrApp
xcodebuild build -scheme TransfrApp -destination 'platform=iOS Simulator,name=iPhone 16'
```

The shipping device app is a thin Xcode app target that imports `TransfrUI` and
hosts `TransfrApp` (the `@main App` lives in `App/TransfrApp.swift`).

### API-agnostic by construction

Every screen talks to a `JourneyRepository`, never to `TransfrClient` directly:

- `SampleRepository` serves the bundled `Resources/sample_journeys.json` (the exact
  `api/schemas.py` shape, decoded through the same `TransfrJSON` coder the live path
  uses) — so **the app runs fully with no server**, which is the point while `api/`
  is in progress.
- `LiveRepository` wraps `TransfrClient` against the FastAPI service.

Switching is one line in `App/TransfrApp.swift` (`.sample` → `.live(url)`); no view
changes. A `CachingRepository` decorator is the natural next layer for the offline
unit-of-work (DESIGN.md §13.9).

| File / dir | What |
|---|---|
| `Theme/Theme.swift` | design tokens → dynamic light/dark `Color`s + `Verdict` colours |
| `Data/JourneyRepository.swift` | the agnostic seam; `LiveRepository` |
| `Data/SampleRepository.swift` | bundled offline tier |
| `Data/TripModel.swift` | `@Observable` session state + value `Route`s |
| `Screens/` | `RootView` (NavigationStack) → Input → Results → Journey → Carousel → Walk |
| `Components/` | verdict badge, platform chip, panel, walk ring, button styles |

### Screen coverage

**All 15 prototype screens** (DESIGN.md §3) are scaffolded and navigable: Plan
(Type / Paste / Walk-only) → Connections → The connection → Transfers → Walk
(Section / Levels / 3D) ⇄ AR; Live; Walk-lookup; and Settings → Advanced (Full
station walk · Nearest facility · Map health · Offline & regions) / Attributions.

What is **live-driven** vs **illustrative/stub** per screen is tracked in
[`SUI_TODO.md`](SUI_TODO.md). In short: the journey spine (Connections → timeline →
carousel) reads real `/journeys` data; the walk renderers are schematic pending
`viz_export` projection; AR/Live/the Advanced tools are faithful **visual** builds
on illustrative content; Settings persists for real (theme fully wired). The hook to
project real geometry is `WalkView.loadGeometry()`.

## Points that need the API's attention

Where the client is ahead of, or depends on, `api/`:

1. **`/journeys` time param.** The client sends `time=` (ISO-8601), matching
   `api/main.py:get_journeys` — previously it sent `when=`. Fixed in this pass.
2. **`/transfer` param names.** Now sends `from_platform` / `to_platform` (was
   `from`/`to`). Fixed in this pass.
3. **Inlined-`viz_export` on `/journeys` (DESIGN.md §13.9).** So a selected plan
   prefetches every transfer's geometry in one round trip instead of N. The client
   is structured for it (`walks()` batch, `WalkKey(transfer:)`) but the server
   endpoint doesn't exist yet. Not a blocker — the app is usable without geometry.
4. **Boarding / step-off data.** The carousel's coach/sector guidance is currently
   illustrative. It should arrive in the plan payload (server-side `boarding` /
   `formation_model`, §13.6) rather than be synthesised client-side.
5. **Verdict `reason` strings.** The UI renders honest "why we can't say" copy from
   `unknown(reason)` (e.g. `no_platform_data`); keeping those strings stable keeps
   that copy correct.

## Next

- Project real `viz_export` geometry in `WalkView` (Section/Levels), then the 3D/AR
  renderers off the same decode (DESIGN.md §13.3).
- Wire a disk cache around `TransfrClient` (keyed by `WalkKey`) so prefetched
  walks render offline (§13.9).
