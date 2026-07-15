#!/bin/bash
# Build core/data/europe-railway-pedestrian.pbf from the full planet.pbf.
#
# Two passes, cheapest reduction first:
#   1. tags-filter: planet-wide, only railway/pedestrian-relevant tags.
#   2. extract:     clip pass 1's output to Europe (Geofabrik boundary).
#
# Tag scope, and why each one is IN:
#   railway=platform,platform_edge   -- the actual platform infrastructure
#   railway=station,halt             -- station point markers
#   railway=subway_entrance,buffer_stop,level_crossing
#                                     -- README's own "buffer stop / crossing"
#                                        transfer concepts
#   public_transport=stop_area,stop_area_group,station
#                                     -- station identity + station-complex
#                                        grouping (stop_area_group lets a
#                                        rail+metro complex share one graph)
#   highway=footway,steps,corridor,pedestrian,elevator
#                                     -- pedestrian infrastructure
#   conveying=*                      -- escalators / moving walkways
#
# Tag scope, and why each one is OUT (measured, not guessed -- see below):
#   highway=service      -- 62.1M ways worldwide (driveways/parking, bigger
#                            than footway itself). Not pedestrian-specific.
#   highway=path         -- 15.7M ways worldwide (hiking trails, rural
#                            tracks). Legitimate tag, wrong context.
#   entrance=*            -- 4.9M nodes worldwide (any building entrance).
#                            Only useful to us via relation membership,
#                            which we already capture.
#   public_transport=platform,stop_position
#                          -- 3.5M + 1.5M elements worldwide, but this tag
#                             is mode-agnostic (bus stops overwhelmingly
#                             dominate); railway=platform already covers
#                             the rail-specific case.
#   nwr/railway (bare)     -- matches ANY railway=* value (signals,
#                             switches, plain track); we only want the
#                             pedestrian-relevant subset above.
#   route=train/tram/subway/light_rail relations
#                          -- pulls in whole-country track geometry via
#                             member expansion; not used by pathfinding,
#                             which only cares about stop_area membership.
#   disused/abandoned/razed/construction/proposed:railway
#                          -- historical/future track state, irrelevant to
#                             "can I walk this transfer today".
#
# Run from the repo root. Expect ~10-15 minutes total (pass 1 scans the
# full planet file; pass 2 is fast since pass 1's output is already small).

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

echo "Pass 1/2: tag-filtering the full planet file (this is the slow one) ..."
osmium tags-filter \
    -o "$OUT_DIR/_planet-railway-pedestrian.pbf" -O \
    "$PLANET" \
    "nwr/railway=platform,platform_edge,station,halt,subway_entrance,buffer_stop,level_crossing" \
    "nwr/public_transport=stop_area,stop_area_group,station" \
    "nwr/highway=footway,steps,corridor,pedestrian,elevator" \
    "nwr/conveying"

echo "Pass 2/2: clipping to Europe ..."
osmium extract \
    --polygon "$POLY" \
    --strategy complete_ways \
    -o "$OUT_DIR/europe-railway-pedestrian.pbf" -O \
    "$OUT_DIR/_planet-railway-pedestrian.pbf"

rm -f "$OUT_DIR/_planet-railway-pedestrian.pbf"

echo "Done: $OUT_DIR/europe-railway-pedestrian.pbf"
osmium fileinfo -e "$OUT_DIR/europe-railway-pedestrian.pbf"
