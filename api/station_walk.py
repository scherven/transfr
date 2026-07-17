"""
Build the 'full station walk': from one source platform, the real walk to every
OTHER platform at the same station -- one find_shortest_path per platform.

Powers the /station-walk advanced tool (a *station* question, not a journey one):
pick a station + a source platform, see distance/time to every platform,
nearest-first. Kept a thin, DB-taking wrapper around the same primitives the
verdict path uses so it never disagrees with a transfer walk:
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
from search_context import list_platform_refs

from api import config, schemas
from api.bridge import resolve_station
from api.transfers import STATION_UNRESOLVED

# Match assess_transfer's / build_walk's pathfind so a row's walk never disagrees
# with the transfer verdict or the drawn `/walk` for the same two platforms.
_ALGORITHM = "astar"


def _row_sort_key(row: schemas.StationWalkRow):
    """Nearest-first: reachable rows first, ordered by ascending walk distance;
    then every unreachable row. Stable, so unreachable rows keep the natural ref
    order `list_platform_refs` returned them in."""
    # if row.found and row.walk_distance_m is not None:
    #     return (0, row.walk_distance_m)
    # return (1, 0.0)
    p = row.to_platform
    try:
        p = int(p)
        return (p, 0 if row.found else 1, row.walk_distance_m or 0.0)
    except ValueError:
        x = (ord(i) for i in p)
        return (sum(x), 0 if row.found else 1, row.walk_distance_m or 0.0)
        # return (...x, 0 if row.found else 1, row.walk_distance_m or 0.0)
    # return (row.to_platform, 0 if row.found else 1, row.walk_distance_m or 0.0)


def build_station_walk(conn, lat: float, lon: float, from_platform: str,
                       step_free: bool = False) -> schemas.StationWalkResponse:
    """Resolve the station nearest (lat, lon), then pathfind from `from_platform`
    to every other platform ref it lists. Returns rows sorted nearest-first.
    Degrades honestly: no station near the point -> top-level `found=False`; a
    platform that doesn't connect -> a `found=False` row with core/'s reason."""
    with conn.cursor() as cur:
        match = resolve_station(cur, lat, lon)
        if match is None:
            return schemas.StationWalkResponse(
                lat=lat, lon=lon, from_platform=from_platform, step_free=step_free,
                found=False, reason=STATION_UNRESOLVED,
            )
        refs = list_platform_refs(cur, match.relation_id)

    rows: List[schemas.StationWalkRow] = []
    for ref in refs:
        if ref == from_platform:
            continue  # a platform never walks to itself
        result = find_shortest_path(
            conn, match.relation_id, from_platform, ref,
            algorithm=_ALGORITHM, use_stitch_bridges=config.STITCH_BRIDGES,
            avoid_elevators=step_free,
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
