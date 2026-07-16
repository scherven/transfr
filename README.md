# transfr
can you really make that transfer?

Map data (c) [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors

## algorithm

from stop location:

1. if on same platform => easy

2. nearby buffer stop => walk to buffer stop, walk across, walk up

3. nearby connector => walk to connector, walk across

4. neither => assume stairs connect entrances, walk to stairs

  29 osmium tags-filter -o planet-filtered-4.pbf /Users/simonchervenak/Documents/GitHub/transfr/server-admin/planet.pbf  "nwr/railway"  "r/route=train"  "r/route=light_rail"  "r/route=tram"  "r/route=subway"  "r/public_transport=stop_area"  "r/public_transport=station"  "nwr/disused:railway"  "nwr/abandoned:railway"  "nwr/razed:railway"  "nwr/construction:railway"  "nwr/proposed:railway"  "w/highway=steps"  "w/highway=footway"

  30 osm2pgsql --create  --database openrailwaymap  --hstore  --slim  --merc  --style /Users/simonchervenak/Documents/GitHub/transfr/server-admin/ansible/roles/tileserver/files/scripts/OpenRailwayMap-CartoCSS/setup/openstreetmap-carto.style  --tag-transform-script /Users/simonchervenak/Documents/GitHub/transfr/server-admin/ansible/roles/tileserver/files/scripts/OpenRailwayMap-CartoCSS/setup/openstreetmap-carto.lua  --multi-geometry  /Users/simonchervenak/Documents/GitHub/transfr/planet-filtered-4.pbf

  psql -h localhost -d openrailwaymap -U simonchervenak -c "SELECT pid, now() - query_start AS duration, state, left(query, 120) AS query FROM pg_stat_activity WHERE state != 'idle' ORDER BY duration DESC;"

  psql -h localhost -d openrailwaymap -U simonchervenak -f views.sql

## backend API

`api/` is a FastAPI service that connects a user's goal (departure + arrival
station) to the platform-to-platform pathfinder in `core/`: it searches journeys
via Transitous (MOTIS 2) and, for each change of train, assesses whether the
platform transfer is walkable within the layover (`feasible` / `tight` /
`infeasible` / `unknown`+reason).

    .venv/bin/uvicorn api.main:app --port 5001
    
    # then, e.g.
    curl 'localhost:5001/journeys?from=Frankfurt&to=Z%C3%BCrich%20HB'
    curl 'localhost:5001/transfer?lat=48.0732&lon=7.3470&from_platform=A&to_platform=B'

It reads the `core/` `transfr_eu` database (PG* env vars, see `core/db.py`). The
coordinate-based station resolver and the platform matcher need two index builds:

    .venv/bin/python core/dbgen/build_station_index.py     # station_points (~333k rows)
    .venv/bin/python core/dbgen/build_platform_index.py    # station_stops + osm_nodes coord index (Tier 2)

## beta deployment (iOS client, single always-on host)

The API is exposed to the iOS app through a Cloudflare tunnel (no public host
needed) on an always-on Mac, kept alive by launchd. Full reproducible setup:

### access controls

Two **opt-in** controls guard the exposed API — both are **off unless configured**,
so local dev and the test suite (which set neither) are unaffected:

| Env var | Meaning |
|---------|---------|
| `TRANSFR_API_KEY` | shared secret; the iOS build sends it as the `X-API-Key` header |
| `TRANSFR_API_KEY_FILE` | path to read the secret from instead (keeps it out of the plist) |
| `TRANSFR_RATE_LIMIT` | per-client-IP ceiling in slowapi syntax, e.g. `60/minute` |

`/health` is always open (and rate-limit-exempt) so the tunnel and uptime checks
can probe liveness; every data route returns `401` without the key once one is set,
and `429` past the rate limit. Implemented in `api/security.py`; wired in `api/main.py`.

### 1. one-time host setup

    .venv/bin/pip install -r requirements.txt                 # pinned versions
    .venv/bin/python core/dbgen/build_station_index.py        # station_points (~333k rows)
    .venv/bin/python core/dbgen/build_platform_index.py       # station_stops + coord index
    brew install cloudflared                                  # macOS; Linux: Cloudflare's apt/rpm repo

Make sure Postgres (`transfr_eu`) starts on boot too — macOS `brew services start
postgresql@<v>`, Linux `sudo systemctl enable --now postgresql`. The API tolerates
it coming up late (lazy pool) but the data routes need it.

### 2. install the auto-restart services

Pick the folder for the host OS — both do the same thing (two auto-restarting
services + generate the shared key), differing only in the init system:

    deploy/launchd/install.sh          # macOS (launchd)      -> ~/Library/LaunchAgents/
    sudo deploy/systemd/install.sh     # Linux  (systemd)     -> /etc/systemd/system/

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

### 3. named tunnel — one-time setup (stable URL)

The deployment serves `https://api.trans-fr.com`, a Cloudflare **named tunnel** so
the URL survives restarts/reboots (unlike a quick tunnel). Set up once on the host:

    cloudflared tunnel login                              # browser: authorize the domain
    cloudflared tunnel create transfr                     # creates the tunnel + <uuid>.json creds
    cloudflared tunnel route dns transfr api.trans-fr.com # auto-creates the DNS CNAME

Then write `~/.cloudflared/config.yml` (outside the repo; the creds are secret):

    tunnel: <uuid from create>
    credentials-file: /Users/<you>/.cloudflared/<uuid>.json
    ingress:
      - hostname: api.trans-fr.com
        service: http://localhost:5001
      - service: http_status:404

The tunnel plist runs `cloudflared tunnel run transfr`, which reads that config.
Re-running `deploy/launchd/install.sh` keeps it wired.

### 4. point the app at it

    cat deploy/secrets/api_key    # the X-API-Key value

The iOS app defaults to `https://api.trans-fr.com` (`AppConfig.defaultBaseURL`) and
reads the key from the `TRANSFR_API_KEY` env var (injected by the Xcode scheme, so
it's never committed). Set it from the file above; override the URL with
`TRANSFR_API_URL` for a local server, or `TRANSFR_USE_SAMPLE=1` for the offline tier.

### running by hand (dev, no service manager)

    TRANSFR_API_KEY="$(openssl rand -hex 24)" TRANSFR_RATE_LIMIT=60/minute \
        .venv/bin/uvicorn api.main:app --port 5001
    cloudflared tunnel run transfr        # named tunnel, separate terminal
    # or, no domain: cloudflared tunnel --url http://localhost:5001  (random URL)

### caveats

> **No domain?** A **quick tunnel** (`cloudflared tunnel --url http://localhost:5001`)
> needs no account/domain but its URL is random and changes every restart — read it
> from the log (`~/Library/Logs/transfr/tunnel.err.log`). The named tunnel above is
> what makes the URL stable enough to ship in a build.

> **Web surface — deferred, not free.** The controls above suit a single-tenant
> native client. CORS is still `*` (harmless for iOS, which doesn't do CORS) and
> the API key is one shared secret, not per-user auth. Before any browser/web
> client ships, tighten `TRANSFR_CORS_ORIGINS` and move to real per-user auth —
> a shared key embedded in web JS is public.

## development

    .venv/bin/python -m pytest tests/ -q                       # offline (deterministic)
    TRANSFR_DB=1   .venv/bin/python -m pytest tests/ -q         # + transfr_eu DB tests
    TRANSFR_LIVE=1 .venv/bin/python -m pytest tests/ -q         # + real Transitous pulls

The journey tests run against real MOTIS responses captured under
`tests/fixtures/journeys/` (git-ignored — they're bulky). Regenerate them with:

    .venv/bin/python tests/capture_journey_fixtures.py           # fill in what's missing
    .venv/bin/python tests/capture_journey_fixtures.py --force   # re-capture all
