"""
Build the 'full station walk': from one source platform, the real walk to every
OTHER platform at the same station -- one find_shortest_path per platform.

Powers the /station-walk advanced tool (a *station* question, not a journey one):
pick a station + a source platform, see distance/time to every platform, in
platform-ref order. Kept a thin, DB-taking wrapper around the same primitives
the verdict path uses so it never disagrees with a transfer walk:
  * consistent with the verdict -- each row runs find_shortest_path with the SAME
    settings assess_transfer / build_walk use (astar, stitch bridges per
    config.STITCH_BRIDGES), so a row's walk time equals what a `/walk` between
    those two refs would report. `step_free` routes elevator-free (avoid_elevators),
    a deliberately different route, exactly like the step-free `/walk` variant;
  * honest -- a pair that doesn't connect is a `found=False` row carrying core/'s
    own reason, never a raised error; a coordinate that resolves to no station is
    a top-level `found=False` (station_unresolved), mirroring /transfer and
    /station-platforms.
"""

from __future__ import annotations

from typing import List

from ground_truth import find_shortest_path  # resolved via api/__init__ sys.path
from search_context import _natural_key, list_platform_refs

from api import config, platform_labels, schemas
from api.bridge import resolve_station
from api.transfers import STATION_UNRESOLVED

# Match assess_transfer's / build_walk's pathfind so a row's walk never disagrees
# with the transfer verdict or the drawn `/walk` for the same two platforms.
_ALGORITHM = "astar"


def _row_sort_key(row: schemas.StationWalkRow):
    """Platform-ref order: purely-numeric refs first in numeric order ("2"
    before "10"), then letter-leading refs ("A", "D06") alphabetically, with a
    mixed ref like "3a" landing just after "3". Reuses search_context's
    `_natural_key` -- the very key `list_platform_refs` sorts by -- so a row's
    position matches the ref list the walk was built from."""
    return _natural_key(row.to_platform)


def build_station_walk(conn, lat: float, lon: float, from_platform: str,
                       step_free: bool = False) -> schemas.StationWalkResponse:
    """Resolve the station nearest (lat, lon), then pathfind from `from_platform`
    to every other platform ref it lists. Returns rows in platform-ref order
    (numeric before alphabetic). Degrades honestly: no station near the point ->
    top-level `found=False`; a platform that doesn't connect -> a `found=False`
    row with core/'s reason."""
    with conn.cursor() as cur:
        match = resolve_station(cur, lat, lon)
        if match is None:
            return schemas.StationWalkResponse(
                lat=lat, lon=lon, from_platform=from_platform, step_free=step_free,
                found=False, reason=STATION_UNRESOLVED,
            )
        refs = list_platform_refs(cur, match.relation_id)

    # Fold in the harvested overlay tracks (the labels OSM lacks) so this tool
    # covers the SAME platform set the station map and /station-platforms offer --
    # at Zürich HB OSM labels only 5 of ~25 tracks, so without this the tool
    # silently omits most of the station. Each row is then anchored on its overlay
    # coordinate, so a track OSM doesn't label still routes (core/ Tier-3) instead
    # of returning platform_not_found.
    feed = platform_labels.platform_markers(lat, lon)
    if feed:
        refs = sorted(
            {*refs, *(str(p["track"]) for p in feed[1] if p.get("track") is not None)},
            key=_natural_key,
        )
    from_coord = platform_labels.track_coord(lat, lon, from_platform)

    rows: List[schemas.StationWalkRow] = []
    for ref in refs:
        if ref == from_platform:
            continue  # a platform never walks to itself
        result = find_shortest_path(
            conn, match.relation_id, from_platform, ref,
            algorithm=_ALGORITHM, use_stitch_bridges=config.STITCH_BRIDGES,
            avoid_elevators=step_free,
            from_coord=from_coord,
            to_coord=platform_labels.track_coord(lat, lon, ref),
        )
        found = bool(result.get("found"))
        rows.append(schemas.StationWalkRow(
            to_platform=ref,
            found=found,
            walk_time_s=result.get("walking_time_seconds"),
            walk_distance_m=result.get("walking_distance_meters"),
            reason=None if found else result.get("reason"),
        ))

    rows.sort(key=_row_sort_key)
    return schemas.StationWalkResponse(
        lat=lat, lon=lon, relation_id=match.relation_id, station=match.name,
        from_platform=from_platform, step_free=step_free, found=True, results=rows,
    )
