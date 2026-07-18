# Claude wrote this next part (maybe you could tell)

**The pieces:**

| Component | What it is                                                   |
| --------- | ------------------------------------------------------------ |
| `core/`   | the platform-to-platform pathfinder over an OSM-derived Postgres database (`transfr_eu`; a parallel `transfr_kr` proves the algorithm is region-agnostic). Produces the walk, the level changes, and the `viz_export` geometry. |
| `api/`    | a FastAPI service tying journey search (Transitous/MOTIS) to per-transfer verdicts — the single JSON contract the client consumes. |
| `ios/`    | a SwiftUI + ARKit **thin** client over `api/`; the routing database and the pathfinder stay server-side. |
| `deploy/` | the always-on host: uvicorn behind a Cloudflare tunnel, kept alive by launchd (macOS) / systemd (Linux). |
| `legacy/` | a dormant Flask server, kept for reference; not used by the live API. |

Everything is OSM-tag-driven: a "region" is nothing but which OSM extract you
load — there is no region logic in the code, only the geographic clip applied
when the source `.pbf` is built.

The design source of truth is [`design/DESIGN.md`](design/DESIGN.md); the iOS
client has its own [`ios/README.md`](ios/README.md).

## Installation

A complete, runnable setup from a fresh clone: system tools → Python env →
database → API server → iOS app. All commands run **from the repo root** unless
noted. For the exhaustive database procedure (the full-planet build, South Korea,
row-count targets, partial rebuilds, and verification) see
[`md/REBUILD.md`](md/REBUILD.md).

### Prerequisites

```bash
# System tools (macOS / Homebrew shown; use your package manager otherwise)
brew install postgresql osmium-tool
brew services start postgresql          # or: pg_ctl -D <datadir> start

# For the iOS app (macOS only): Xcode from the App Store, plus:
brew install xcodegen
```

Postgres connection parameters come from the standard `PG*` environment
variables (see `core/db.py`); the local-dev defaults are host `localhost`, port
`5432`, user `$USER`, empty password. If your Postgres needs explicit values,
export them once:

```bash
export PGHOST=localhost PGPORT=5432 PGUSER="$USER" PGPASSWORD=
```

### 1. Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # pinned: fastapi, uvicorn, psycopg2, pyosmium, ...
```

### 2. Database — `transfr_eu`

The API and `core/` read the `transfr_eu` database. Build it in four steps. Every
build script is resumable and interrupt-safe — it commits in batches, so a Ctrl-C
never corrupts a table; just re-run the same command to finish.

**2a. Build the scoped source `.pbf`.** Reduce a Geofabrik Europe extract to just
the railway + pedestrian tags (this is the lighter path — no planet dump needed;
the canonical full-planet path is `core/dbgen/extract_europe.sh`, see REBUILD.md):

```bash
mkdir -p core/data
curl -L -o core/data/europe-latest.osm.pbf \
  https://download.geofabrik.de/europe-latest.osm.pbf          # ~30 GB

osmium tags-filter \
  -o core/data/europe-railway-pedestrian.pbf -O \
  core/data/europe-latest.osm.pbf \
  "nwr/railway=platform,platform_edge,station,halt,subway_entrance,buffer_stop,level_crossing" \
  "nwr/public_transport=stop_area,stop_area_group,station" \
  "nwr/highway=footway,steps,corridor,pedestrian,elevator" \
  "nwr/conveying"
```

**2b. Create the database and apply the schema** (creates the raw tables, all
query indexes, and the empty derived tables):

```bash
createdb transfr_eu
psql -d transfr_eu -v ON_ERROR_STOP=1 -f core/dbgen/schema.sql
```

**2c. Load the raw OSM data:**

```bash
PGDATABASE=transfr_eu .venv/bin/python core/dbgen/etl.py core/data/europe-railway-pedestrian.pbf
```

**2d. Build the derived tables — in this order** (`build_stitch_bridges` depends
on `node_way_ids` and on the coordinate index that `build_platform_index`
creates), then finalise:

```bash
PGDATABASE=transfr_eu .venv/bin/python core/dbgen/build_node_way_ids.py     # node->way adjacency
PGDATABASE=transfr_eu .venv/bin/python core/dbgen/build_station_index.py    # station_points (centroids, ~333k rows)
PGDATABASE=transfr_eu .venv/bin/python core/dbgen/build_platform_index.py   # station_stops + osm_nodes coord index
PGDATABASE=transfr_eu .venv/bin/python core/dbgen/build_stitch_bridges.py   # synthetic_bridges (opt-in stitching)

psql -d transfr_eu -c "ANALYZE;"
```

A full Europe rebuild takes a few hours, dominated by the `etl.py` load
(~73 M nodes / ~16 M ways) and the coordinate index. Everything after `etl.py`
is resumable.

**2e. Facility POI layer — optional.** Powers the "nearest facility" surface
(`/facilities` and the `/facility-map` 3D station map). The journey, transfer and
walk features do **not** need it — without it those two endpoints return
`found=false` with `reason="no_poi_layer"` and the app shows an honest empty state.
POIs (amenity / shop / tourism / office / leisure) are a large, orthogonal tag set,
so they live in their own `pois` table rather than the tag-scoped core tables. A
facility query is then a fast indexed lat/lon bbox `SELECT` (same no-PostGIS pattern
as `station_points`) — never an `osmium` fork on the request path. `schema.sql`
(step 2b) already created the empty table; fill it in two steps.

Filter the Europe extract from 2a down to POI tags (the lighter path; the canonical
full-planet path is `core/dbgen/extract_pois.sh`), then load it:

```bash
osmium tags-filter \
  -o core/data/europe-pois.pbf -O \
  core/data/europe-latest.osm.pbf \
  "nwr/amenity" "nwr/shop" "nwr/tourism" "nwr/office" "nwr/leisure"

PGDATABASE=transfr_eu .venv/bin/python core/dbgen/build_poi_index.py core/data/europe-pois.pbf
```

The loader is idempotent and interrupt-safe like the others (`--rebuild` to
`TRUNCATE` first); it drops street-furniture noise (benches, parking, …) and stores
one point per POI (a node's own coordinate, a POI-area way's centroid). It's
independent of the core tables, so rebuild it on its own whenever the POI data
refreshes — no need to touch `osm_nodes`/`osm_ways`.

### 3. Run the API server

```bash
PYTHONPATH=. .venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 5001 --reload
```

Smoke-test it:

```bash
curl 'localhost:5001/health'
curl 'localhost:5001/journeys?from=Frankfurt&to=Z%C3%BCrich%20HB'
curl 'localhost:5001/transfer?lat=48.0732&lon=7.3470&from_platform=A&to_platform=B'
curl 'localhost:5001/facility-map?lat=52.525&lon=13.369&category=toilets'   # needs the POI layer (2e)
```

`/journeys` connects a departure + arrival station to per-transfer verdicts;
`/transfer` computes one platform-to-platform walk; `/facility-map` returns a
station drawn in 3D with every facility of a category pinned (`found=false`,
`reason="no_poi_layer"` until step 2e is loaded). The access controls (API key,
rate limit) are **off** unless configured, so local dev needs no key — see
[Deployment](#deployment) to turn them on.

### 4. Generate and run the iOS app

The Xcode project is generated by [XcodeGen](https://github.com/yonaskolb/XcodeGen)
from `ios/project.yml`. The generated `.xcodeproj` is gitignored — its run scheme
bakes in the dev API key, so it must never be committed; regenerate it locally:

```bash
cd ios && TRANSFR_API_KEY=$(cat ../deploy/secrets/api_key) xcodegen generate && open .
```

Then open `TransfrApp.xcodeproj`, pick the **TransfrApp** scheme, and Run on a
simulator. The scheme injects `TRANSFR_API_URL` (default `https://api.trans-fr.com`)
and `TRANSFR_API_KEY` (expanded from the gitignored secret above) at generation
time, so no secret lands in source.

- **`deploy/secrets/api_key`** is created by the deploy installer (see
  [Deployment](#deployment)). To run against your **local** server instead,
  generate with `TRANSFR_API_URL=http://localhost:5001` and no key (local dev
  requires none); or set `TRANSFR_USE_SAMPLE=1` to run fully offline on the
  bundled sample data.

- The Swift packages build and test without the app shell:

  ```bash
  cd ios/TransfrCore && xcodebuild test  -scheme TransfrCore -destination 'platform=iOS Simulator,name=iPhone 16'
  cd ios/TransfrApp  && xcodebuild build -scheme TransfrApp  -destination 'platform=iOS Simulator,name=iPhone 16'
  ```

  More detail (the walk-geometry contracts, regenerating the test goldens) is in
  [`ios/README.md`](ios/README.md).

## Testing

```bash
.venv/bin/python -m pytest tests/ -q                                      # offline (deterministic)
TRANSFR_DB=1   PGDATABASE=transfr_eu .venv/bin/python -m pytest tests/ -q  # + transfr_eu DB tests
TRANSFR_LIVE=1 .venv/bin/python -m pytest tests/ -q                       # + real Transitous pulls
```

The journey tests run against real MOTIS responses captured under
`tests/fixtures/journeys/` (git-ignored — they're bulky). Regenerate them with:

```bash
.venv/bin/python tests/capture_journey_fixtures.py           # fill in what's missing
.venv/bin/python tests/capture_journey_fixtures.py --force   # re-capture all
```

## Deployment

The API is exposed to the iOS app through a Cloudflare tunnel (no public host
needed) on an always-on Mac, kept alive by launchd. Full reproducible setup below.

### Access controls

Two **opt-in** controls guard the exposed API — both are **off unless configured**,
so local dev and the test suite (which set neither) are unaffected:

| Env var                | Meaning                                                      |
| ---------------------- | ------------------------------------------------------------ |
| `TRANSFR_API_KEY`      | shared secret; the iOS build sends it as the `X-API-Key` header |
| `TRANSFR_API_KEY_FILE` | path to read the secret from instead (keeps it out of the plist) |
| `TRANSFR_RATE_LIMIT`   | per-client-IP ceiling in slowapi syntax, e.g. `60/minute`    |

`/health` is always open (and rate-limit-exempt) so the tunnel and uptime checks
can probe liveness; every data route returns `401` without the key once one is set,
and `429` past the rate limit. Implemented in `api/security.py`; wired in `api/main.py`.

### 1. One-time host setup

Do the [Installation](#installation) steps (Python env + the `transfr_eu`
database) on the host, then install cloudflared:

```bash
brew install cloudflared          # macOS; Linux: Cloudflare's apt/rpm repo
```

Make sure Postgres starts on boot too — macOS `brew services start
postgresql@<v>`, Linux `sudo systemctl enable --now postgresql`. The API tolerates
it coming up late (lazy pool) but the data routes need it.

### 2. Install the auto-restart services

Pick the folder for the host OS — both do the same thing (two auto-restarting
services + generate the shared key), differing only in the init system:

```bash
deploy/launchd/install.sh          # macOS (launchd)      -> ~/Library/LaunchAgents/
sudo deploy/systemd/install.sh     # Linux  (systemd)     -> /etc/systemd/system/
```

Both are idempotent (re-run after any code change or reboot). They:

  * generate the shared API key **once** into `deploy/secrets/api_key`
    (gitignored) and reuse it forever, so the shipped iOS build keeps working;
  * install two services — the API (uvicorn on `127.0.0.1:5001`) and the
    cloudflared tunnel — that restart on crash / DB blip / reboot. The API
    binds to loopback only; cloudflared is the only thing that reaches it;
  * print the API key and how to read the current tunnel URL.

The deployed tunnel is a **named tunnel** serving the stable hostname
**`https://api.trans-fr.com`** (set up once — see below). The quick-tunnel
variant (random URL, no domain) is the documented fallback in the tunnel plist.

The key never enters the service definition: it passes only `TRANSFR_API_KEY_FILE`
(a path), and the app reads the secret from that gitignored file at startup. (On
macOS, uvicorn is invoked directly rather than via a wrapper because TCC blocks
launchd from running a shell script under `~/Documents`; Linux has no such limit.)
See `deploy/launchd/README.md` / `deploy/systemd/README.md` for day-to-day ops.

### 3. Named tunnel — one-time setup (stable URL)

The deployment serves `https://api.trans-fr.com`, a Cloudflare **named tunnel** so
the URL survives restarts/reboots (unlike a quick tunnel). Set up once on the host:

```bash
cloudflared tunnel login                              # browser: authorize the domain
cloudflared tunnel create transfr                     # creates the tunnel + <uuid>.json creds
cloudflared tunnel route dns transfr api.trans-fr.com # auto-creates the DNS CNAME
```

Then write `~/.cloudflared/config.yml` (outside the repo; the creds are secret):

```yaml
tunnel: <uuid from create>
credentials-file: /Users/<you>/.cloudflared/<uuid>.json
ingress:
  - hostname: api.trans-fr.com
    service: http://localhost:5001
  - service: http_status:404
```

The tunnel plist runs `cloudflared tunnel run transfr`, which reads that config.
Re-running `deploy/launchd/install.sh` keeps it wired.

### 4. Point the app at it

```bash
cat deploy/secrets/api_key    # the X-API-Key value
```

The iOS app defaults to `https://api.trans-fr.com` (`AppConfig.defaultBaseURL`) and
reads the key from the `TRANSFR_API_KEY` env var (injected by the Xcode scheme, so
it's never committed — see [Installation §4](#4-generate-and-run-the-ios-app)).
Override the URL with `TRANSFR_API_URL` for a local server, or set
`TRANSFR_USE_SAMPLE=1` for the offline tier.

### Running by hand (dev, no service manager)

```bash
TRANSFR_API_KEY="$(openssl rand -hex 24)" TRANSFR_RATE_LIMIT=60/minute \
    .venv/bin/uvicorn api.main:app --port 5001
cloudflared tunnel run transfr        # named tunnel, separate terminal
# or, no domain: cloudflared tunnel --url http://localhost:5001  (random URL)
```

### Caveats

> **No domain?** A **quick tunnel** (`cloudflared tunnel --url http://localhost:5001`)
> needs no account/domain but its URL is random and changes every restart — read it
> from the log (`~/Library/Logs/transfr/tunnel.err.log`). The named tunnel above is
> what makes the URL stable enough to ship in a build.

> **Web surface — deferred, not free.** The controls above suit a single-tenant
> native client. CORS is still `*` (harmless for iOS, which doesn't do CORS) and
> the API key is one shared secret, not per-user auth. Before any browser/web
> client ships, tighten `TRANSFR_CORS_ORIGINS` and move to real per-user auth —
> a shared key embedded in web JS is public.