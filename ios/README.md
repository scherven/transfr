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
| `TransfrClient.swift` | async `URLSession` client over `/journeys`, `/stations`, `/transfer` |

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
`core/viz_export.py --relation 5688517 --ref1 1 --ref2 16 [--details]`.

## Next

- The SwiftUI app target (screens per `design/prototype.html`, `NavigationStack`,
  the four walk renderers) — not yet scaffolded.
- A `/journeys`-with-inlined-`viz_export` endpoint so a plan prefetches in one
  round trip (see DESIGN.md §13.9).
