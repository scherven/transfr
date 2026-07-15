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

    .venv/bin/python core/build_station_index.py     # station_points (~333k rows)
    .venv/bin/python core/build_platform_index.py    # station_stops + osm_nodes coord index (Tier 2)

## development

    .venv/bin/python -m pytest tests/ -q                       # offline (deterministic)
    TRANSFR_DB=1   .venv/bin/python -m pytest tests/ -q         # + transfr_eu DB tests
    TRANSFR_LIVE=1 .venv/bin/python -m pytest tests/ -q         # + real Transitous pulls

The journey tests run against real MOTIS responses captured under
`tests/fixtures/journeys/` (git-ignored — they're bulky). Regenerate them with:

    .venv/bin/python tests/capture_journey_fixtures.py           # fill in what's missing
    .venv/bin/python tests/capture_journey_fixtures.py --force   # re-capture all
