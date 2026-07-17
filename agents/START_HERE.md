# START HERE

Orientation for someone (human or agent) landing in this repo cold. Skim this,
then go to [`README.md`](README.md) for the real setup/deploy detail.

**transfr** answers one question: *can you really make that transfer?* Given a
journey with a change of train, it decides whether the platform-to-platform walk
fits in the layover — `feasible` / `tight` / `infeasible` / `unknown`+reason.

## The three-way split

| Dir | What it is |
|-----|------------|
| **`core/`** | The engine. OSM-derived walk pathfinding between platforms, on the `transfr_eu` Postgres DB. Talks to no network. |
| **`api/`** | A FastAPI service over `core/`. Searches journeys via Transitous (MOTIS 2) and assesses each change of train. This is what ships. |
| **`ios/`** | The SwiftUI client (`TransfrApp`) + a shared Swift package (`TransfrCore`). Reads the live `api/`. |

Everything else: `tests/` (pytest, one suite for `core/`+`api/`), `deploy/`
(launchd/systemd + Cloudflare tunnel), `scripts/dev.py` (one-command local dev
loop), `agents/` (see below).

Inside `core/`: `pathfinding/` (graph, Dijkstra/A*, bidirectional search,
ground truth), `dbgen/` (OSM ETL + the station/platform index builds), `viz/`
(`viz_export.py` — the walk-geometry JSON the app and all renderers read — and
`viz_render.py`), `boarding/` (train formation → seat/platform position),
`tooling/`, `db.py`, `data/`.

## Run the tests

From the repo root, with the repo's `.venv`:

    .venv/bin/python -m pytest tests/ -q                 # offline, deterministic — the gate
    TRANSFR_DB=1   .venv/bin/python -m pytest tests/ -q   # + transfr_eu DB tests
    TRANSFR_LIVE=1 .venv/bin/python -m pytest tests/ -q   # + real Transitous pulls

The default (no env vars) needs no DB and no network — that is the one that must
stay green. The DB/live variants are opt-in and skip themselves otherwise.

## Run the API

    .venv/bin/uvicorn api.main:app --port 5001
    curl 'localhost:5001/journeys?from=Frankfurt&to=Z%C3%BCrich%20HB'

It reads the `core/` `transfr_eu` DB (`PG*` env vars — see `core/db.py`), which
needs two index builds first (`core/dbgen/build_station_index.py`,
`core/dbgen/build_platform_index.py`). Full instructions in `README.md`.

## `agents/` — context, not product

Nothing under `agents/` is imported by `core/`, `api/`, or the test suite. It is
the written-down context and the scratchpad:

- **`agents/md/`** — the deep-dive notes. Start with
  [`HANDOFF.md`](agents/md/HANDOFF.md) (the pathfinding rewrite),
  [`PLATFORM-RESOLUTION.md`](agents/md/PLATFORM-RESOLUTION.md) (how a MOTIS
  track becomes routable geometry), [`REBUILD.md`](agents/md/REBUILD.md)
  (rebuilding the DBs from scratch), plus `VIZ.md`, `PASTE-LINK.md`,
  `SEAT-PLATFORM-POSITION.md` and two `ISSUE-*.md` write-ups.
- **`agents/design/`** — [`DESIGN.md`](agents/design/DESIGN.md), the design doc
  and decision log, next to the HTML prototypes it describes
  (`prototype.html`, `route-maps.html`, `loading-animation.html`) and the app
  icon / favicon. The iOS theme and launch animation are ported 1:1 from these.
- **`agents/research/`** — standalone experiments (`tight_connections/`: does
  MOTIS drop makeable tight connections?). Run from the repo root; they
  bootstrap `sys.path` to import `core/` and `api/`. Has its own README.
- **`agents/legacy/`** — the dormant pre-rewrite Flask server and pathfinder.
  Not used by anything live; kept for reference only.

## Also worth reading

- [`TODO.md`](TODO.md) — current state per area, with 🔴/🟡/🟢 status and issue links.
- [`IMPROVEMENTS.md`](IMPROVEMENTS.md) — the product/UX backlog.
- [`ios/README.md`](ios/README.md) — the app's structure and its JSON contracts.

## Gotchas

- **Bare imports in tests.** Test modules import the engine by bare module name
  (`from graph import ...`). `tests/conftest.py` puts `core/` and its submodule
  dirs on `sys.path` before collection — don't "fix" those imports to be
  package-qualified without reading that file first.
- **`stations.csv` (repo root, ~15 MB)** — a vendored third-party dataset
  (trainline-eu/stations), not generated here. `api/stations.py` loads it at
  import time via a path relative to itself, so moving it means updating that
  file (and `agents/research/tight_connections/iris.py`).
- **Generated/secret things are gitignored** and must stay that way:
  `.venv/`, `secrets/`, `deploy/secrets/`, `*.pbf`, `core/viz_out/`,
  `tests/fixtures/journeys/`, `ios/TransfrApp.xcodeproj/` (regenerate with
  `xcodegen`; the scheme bakes in the dev API key).
