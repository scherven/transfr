#!/bin/bash
# Build core/data/europe-pois.pbf from the full planet.pbf -- the facility POI
# layer (amenity / shop / tourism / office / leisure) behind /facilities and
# /facility-map. Loaded into the `pois` Postgres table by
# core/dbgen/build_poi_index.py, so the API answers a facility query with an
# indexed bbox SELECT instead of forking `osmium extract` against the planet on
# every request (which timed out for every uncached station).
#
# Two passes, cheapest reduction first (same shape as extract_europe.sh):
#   1. tags-filter: planet-wide, only the POI keys a traveller looks for.
#   2. extract:     clip pass 1's output to Europe (same Geofabrik boundary).
#
# Kept SEPARATE from extract_europe.sh (railway/pedestrian) on purpose: POIs are a
# large, orthogonal tag set, and their own file + their own table keeps the core
# pathfinding DB lean. Street-furniture noise (amenity=bench/parking/...) rides
# along in the pbf and is dropped at load time by build_poi_index.py's _keep_poi.
#
# Run from the repo root. Pass 1 scans the full planet (~10-15 min); pass 2 is
# fast. Buildings are intentionally NOT included -- the facility layer is points.

set -euo pipefail
cd "$(dirname "$0")/../.."

PLANET=server-admin/planet.pbf
OUT_DIR=core/data
POLY="$OUT_DIR/europe.poly"

mkdir -p "$OUT_DIR"

if [ ! -f "$POLY" ]; then
    echo "Fetching Europe boundary polygon from Geofabrik ..."
    curl -sL -o "$POLY" https://download.geofabrik.de/europe.poly
fi

echo "Pass 1/2: tag-filtering the full planet for POIs (this is the slow one) ..."
osmium tags-filter \
    -o "$OUT_DIR/_planet-pois.pbf" -O \
    "$PLANET" \
    "nwr/amenity" \
    "nwr/shop" \
    "nwr/tourism" \
    "nwr/office" \
    "nwr/leisure"

echo "Pass 2/2: clipping to Europe ..."
osmium extract \
    --polygon "$POLY" \
    --strategy complete_ways \
    -o "$OUT_DIR/europe-pois.pbf" -O \
    "$OUT_DIR/_planet-pois.pbf"

rm -f "$OUT_DIR/_planet-pois.pbf"

echo "Done: $OUT_DIR/europe-pois.pbf"
echo "Load it with:  .venv/bin/python core/dbgen/build_poi_index.py $OUT_DIR/europe-pois.pbf"
osmium fileinfo -e "$OUT_DIR/europe-pois.pbf"
