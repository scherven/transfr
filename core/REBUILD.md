# Rebuilding the databases from scratch

This is the complete, runnable procedure for regenerating both databases the
app reads:

- **`transfr_eu`** — Europe (the production DB the API/`core/` use by default).
- **`transfr_kr`** — South Korea (the same pipeline, different source extract;
  proves the algorithm is region-agnostic).

Everything is OSM-tag-driven. A "region" is nothing but which OSM extract you
load — there is no region logic in the code, only the geographic clip applied
when the source `.pbf` is built. All commands are run **from the repo root**.

Target state these commands reproduce (approximate row counts):

| table | transfr_eu | transfr_kr |
|---|---|---|
| `osm_nodes` | ~73.1 M | ~1.10 M |
| `osm_ways` | ~16.1 M | ~0.21 M |
| `osm_relations` | ~0.37 M | ~3.5 k |
| `node_way_ids` | ~71.7 M | ~1.09 M |
| `station_points` | ~333 k | ~3.1 k |
| `station_stops` | ~112 k | ~1.6 k |
| `synthetic_bridges` | ~31 k | ~95 |

---

## 0. One-time prerequisites

```bash
# System tools (macOS / Homebrew shown; use your package manager otherwise)
brew install postgresql osmium-tool
brew services start postgresql        # or: pg_ctl -D <datadir> start

# Python env (repo root)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # psycopg2-binary, osmium (pyosmium), ...
```

Connection parameters come from standard `PG*` environment variables (see
`core/db.py`); the local-dev defaults are host `localhost`, port `5432`, user
`$USER`, empty password. Every Python command below sets `PGDATABASE` inline to
pick the target DB. If your Postgres needs a host/user/password, export them
once:

```bash
export PGHOST=localhost PGPORT=5432 PGUSER="$USER" PGPASSWORD=
```

The build scripts are all resumable/interrupt-safe: they commit in batches, so a
Ctrl-C never corrupts a table — you just re-run the same command to finish.

---

## 1. Europe — `transfr_eu`

### 1a. Build the scoped source `.pbf`

The DB is loaded from a `.pbf` that has already been reduced to
railway+pedestrian tags. Two ways to get it:

**Option A — from the full planet (canonical, what `extract_europe.sh` does).**
Requires `server-admin/planet.pbf` (the ~80 GB planet dump). Two passes:
tag-filter the planet, then clip to the Europe boundary.

```bash
bash core/extract_europe.sh
# -> core/data/europe-railway-pedestrian.pbf
```

**Option B — from a Geofabrik Europe extract (lighter, no planet needed).**
Download the pre-clipped Europe extract and run only the tag-filter pass (the
same tag set as `extract_europe.sh`, no clip):

```bash
mkdir -p core/data
curl -L -o core/data/europe-latest.osm.pbf \
  https://download.geofabrik.de/europe-latest.osm.pbf      # ~30 GB

osmium tags-filter \
  -o core/data/europe-railway-pedestrian.pbf -O \
  core/data/europe-latest.osm.pbf \
  "nwr/railway=platform,platform_edge,station,halt,subway_entrance,buffer_stop,level_crossing" \
  "nwr/public_transport=stop_area,stop_area_group,station" \
  "nwr/highway=footway,steps,corridor,pedestrian,elevator" \
  "nwr/conveying"
```

### 1b. Create the database and apply the schema

`core/schema.sql` creates the four raw tables, all query indexes (including the
compound-ref GIN token indexes), and the empty derived tables. It `DROP`s the
raw tables at the top, so applying it to a fresh DB is clean.

```bash
createdb transfr_eu
psql -d transfr_eu -v ON_ERROR_STOP=1 -f core/schema.sql
```

### 1c. Load the raw OSM data

```bash
PGDATABASE=transfr_eu .venv/bin/python core/etl.py core/data/europe-railway-pedestrian.pbf
```

### 1d. Build the derived tables (order matters)

Run these **in this order** — `build_stitch_bridges` depends on `node_way_ids`
and on the `osm_nodes(lat,lon)` coordinate index that `build_platform_index`
ensures:

```bash
PGDATABASE=transfr_eu .venv/bin/python core/build_node_way_ids.py      # node->way adjacency
PGDATABASE=transfr_eu .venv/bin/python core/build_station_index.py     # station_points (centroids)
PGDATABASE=transfr_eu .venv/bin/python core/build_platform_index.py    # station_stops + osm_nodes coord index (Tier 2)
PGDATABASE=transfr_eu .venv/bin/python core/build_stitch_bridges.py    # synthetic_bridges (opt-in stitching)
```

### 1e. Finalise

```bash
psql -d transfr_eu -c "ANALYZE;"
```

Expect the full Europe rebuild to take **a few hours**, dominated by: the planet
tag-filter (Option A), the `etl.py` load (~73 M nodes / ~16 M ways), the
coordinate index in `build_platform_index`, and the `build_stitch_bridges`
scan (~250 k platform areas). Everything after `etl.py` is resumable.

---

## 2. South Korea — `transfr_kr`

Identical pipeline; only the source extract differs (Geofabrik ships a small
South Korea extract, so no planet and no clip are needed). Fast — the whole
thing is a couple of minutes.

```bash
# 2a. Source extract + tag-filter (same tag set as Europe, no clip)
mkdir -p core/data
curl -L -o core/data/south-korea-latest.osm.pbf \
  https://download.geofabrik.de/asia/south-korea-latest.osm.pbf        # ~270 MB

osmium tags-filter \
  -o core/data/south-korea-railway-pedestrian.pbf -O \
  core/data/south-korea-latest.osm.pbf \
  "nwr/railway=platform,platform_edge,station,halt,subway_entrance,buffer_stop,level_crossing" \
  "nwr/public_transport=stop_area,stop_area_group,station" \
  "nwr/highway=footway,steps,corridor,pedestrian,elevator" \
  "nwr/conveying"

# 2b. Create DB + schema
createdb transfr_kr
psql -d transfr_kr -v ON_ERROR_STOP=1 -f core/schema.sql

# 2c. Load
PGDATABASE=transfr_kr .venv/bin/python core/etl.py core/data/south-korea-railway-pedestrian.pbf

# 2d. Derived tables (same order as Europe)
PGDATABASE=transfr_kr .venv/bin/python core/build_node_way_ids.py
PGDATABASE=transfr_kr .venv/bin/python core/build_station_index.py
PGDATABASE=transfr_kr .venv/bin/python core/build_platform_index.py
PGDATABASE=transfr_kr .venv/bin/python core/build_stitch_bridges.py

# 2e. Finalise
psql -d transfr_kr -c "ANALYZE;"
```

---

## 3. Verify

```bash
# Row counts (compare against the table at the top)
for db in transfr_eu transfr_kr; do
  echo "== $db =="
  psql -d "$db" -tc "
    SELECT 'osm_nodes',         count(*) FROM osm_nodes
    UNION ALL SELECT 'osm_ways',           count(*) FROM osm_ways
    UNION ALL SELECT 'node_way_ids',       count(*) FROM node_way_ids
    UNION ALL SELECT 'station_points',     count(*) FROM station_points
    UNION ALL SELECT 'station_stops',      count(*) FROM station_stops
    UNION ALL SELECT 'synthetic_bridges',  count(*) FROM synthetic_bridges;"
done

# A real transfer end-to-end (Europe): Colmar A->E needs the stitch to route.
PGDATABASE=transfr_eu .venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "core")
from db import connect
from ground_truth import find_shortest_path
c = connect()
print("Colmar A->E plain :", find_shortest_path(c, 6365739, "A", "E", algorithm="astar").get("reason"))
r = find_shortest_path(c, 6365739, "A", "E", algorithm="astar", use_stitch_bridges=True)
print("Colmar A->E stitch:", r.get("walking_time_seconds"), "s /", r.get("walking_distance_meters"), "m")
PY

# A real transfer end-to-end (Korea): Gangnam Line 2 <-> Shinbundang.
PGDATABASE=transfr_kr .venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "core")
from db import connect
from ground_truth import find_shortest_path
r = find_shortest_path(connect(), 8486718, "925", "D06", algorithm="astar")
print("Gangnam 925->D06:", r.get("walking_time_seconds"), "s /", r.get("walking_distance_meters"), "m")
PY

# Test suites
.venv/bin/python -m pytest tests/ -q                                   # offline (deterministic)
TRANSFR_DB=1 PGDATABASE=transfr_eu .venv/bin/python -m pytest tests/ -q # + DB-backed tests
```

---

## 4. Partial rebuilds / in-place refresh

You rarely need the whole thing. After **re-loading `osm_ways`/`osm_nodes`**
(a fresh `etl.py`), rebuild the derived tables that depend on them — each build
script is idempotent and takes `--rebuild` to `TRUNCATE` first:

```bash
PGDATABASE=transfr_eu .venv/bin/python core/build_node_way_ids.py            # always TRUNCATE+rebuild
PGDATABASE=transfr_eu .venv/bin/python core/build_station_index.py --rebuild
PGDATABASE=transfr_eu .venv/bin/python core/build_platform_index.py --rebuild
PGDATABASE=transfr_eu .venv/bin/python core/build_stitch_bridges.py --rebuild
```

Just the stitch bridges (e.g. after tuning them), scoped to one area for a quick
test instead of the full continent:

```bash
PGDATABASE=transfr_eu .venv/bin/python core/build_stitch_bridges.py \
  --bbox 48.070,7.343,48.077,7.350        # minlat,minlon,maxlat,maxlon (Colmar)
```

If you changed only **`schema.sql` index definitions** (e.g. added the GIN token
indexes on an existing DB), you don't need to reload — just create the new
indexes. Re-applying the whole `schema.sql` would `DROP` the raw tables, so
apply only the new `CREATE INDEX` statements by hand in that case.

---

## What each artifact is (quick reference)

| built by | produces | consumed by |
|---|---|---|
| `extract_europe.sh` / `osmium tags-filter` | scoped `.pbf` | `etl.py` |
| `schema.sql` | raw tables + query indexes (incl. compound-ref GIN token indexes) + empty derived tables | everything |
| `etl.py` | `osm_nodes/ways/relations/relation_members` | all builds + search |
| `build_node_way_ids.py` | `node_way_ids` (node→way adjacency) | `SearchContext.expand`, stitch build |
| `build_station_index.py` | `station_points` (stop_area centroids) | `api/bridge` (coordinate→station) |
| `build_platform_index.py` | `station_stops` + `osm_nodes(lat,lon)` index | Tier-2 platform resolution, stitch build |
| `build_stitch_bridges.py` | `synthetic_bridges` | `SearchContext` when `use_stitch_bridges=True` (opt-in) |
