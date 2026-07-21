#!/usr/bin/env bash
# Fetch the input data for the timetable-only MOTIS spike into ./data/.
#
# Default target is Switzerland: platform/track-rich (so transfr's platform
# assertions are meaningful) and small enough to import on a laptop. Downloads
# are idempotent and resumable (curl -C -), so a Ctrl-C mid-pull loses nothing --
# just re-run.
#
# Usage:
#   GTFS_URL='https://…/ch_gtfs.zip' ./fetch-data.sh        # static timetable only
#   FETCH_OSM=1 GTFS_URL='https://…' ./fetch-data.sh        # also grab the OSM pbf
#
# NOTE: the Swiss static-GTFS permalink changes each timetable year and may need a
# free opentransportdata.swiss account, so it is NOT hardcoded here (a stale URL
# would be worse than an explicit one). Grab the current "GTFS2020" permalink from
#   https://opentransportdata.swiss/en/dataset/timetable-2025-gtfs2020
# and pass it as GTFS_URL. The Geofabrik OSM extract below IS a stable URL.

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data

: "${GTFS_URL:?Set GTFS_URL to the current GTFS zip permalink (see header note)}"
OSM_URL="${OSM_URL:-https://download.geofabrik.de/europe/switzerland-latest.osm.pbf}"

echo "==> GTFS  -> data/ch.gtfs.zip"
curl -L --fail --retry 3 -C - -o data/ch.gtfs.zip "$GTFS_URL"

if [ "${FETCH_OSM:-0}" != "0" ]; then
  echo "==> OSM   -> data/ch.osm.pbf   (only needed if you enable street_routing)"
  curl -L --fail --retry 3 -C - -o data/ch.osm.pbf "$OSM_URL"
fi

echo "==> done. Inputs in ./data/:"
ls -lh data/
echo "Next: docker compose --profile import run --rm import   &&   docker compose up motis"
