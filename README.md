# transfr
can you really make that transfer?

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