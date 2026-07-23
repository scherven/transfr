"""
Build the drawable walk geometry for one transfer, on demand.

`/journeys` returns the verdict spine; this turns a transfer's already-resolved
`(relation_id, from_ref, to_ref)` into the `core/viz_export.py` document the
Swift client's four walk renderers (section / per-level / 3D / AR) draw from.

Kept as a thin, DB-taking wrapper around `viz_export.export` so it is:
  * consistent with the verdict -- it uses the SAME settings assess_transfer's
    pathfind used (astar, and synthetic stitch bridges per config.STITCH_BRIDGES),
    so a default walk's `walking_time_seconds` equals the Transfer's `walk_time_s`.
    `step_free=True` deliberately routes a different (elevator-free) path, so its
    time may differ;
  * never fatal -- `viz_export.export` raises `SystemExit` when a relation has no
    resolvable coordinates; we catch that (and anything else) and return a typed
    `WalkResult(ok=False, reason=...)` rather than 500 the request;
  * planet-details-free -- the gathered landmarks/POI layer (`details=True`) needs
    the full planet extract, so a transfer walk never asks for it. A walk CAN
    still carry ONE known facility via `key.poi`: its coordinate already came from
    `/facilities`, so we project it straight into the details layer as the focus
    (`attach_pois`) with no planet extract -- that's the 'walk to nearest' door.
"""

from __future__ import annotations

from typing import List

from viz_export import export  # resolved via api/__init__ sys.path setup

from typing import Callable, Optional

from api import config, platform_labels, schemas
from api.boarding import compute_boarding, stepoff_node_of
from api.bridge import relation_coord

# Match assess_transfer's pathfind so geometry and verdict never disagree.
_ALGORITHM = "astar"

WALK_BUILD_FAILED = "walk_build_failed"
NO_GEOMETRY = "no_geometry_for_platforms"


def _overlay_coords(conn, key: schemas.WalkKey):
    """(from_coord, to_coord) for this key's platforms from the harvested overlay,
    or (None, None).

    Why: OSM labels only a fraction of a big station's platforms (Zürich HB: 3 of
    ~40), but the overlay -- the same data the station map draws its track markers
    from -- has a coordinate for every track. A browse-mode caller knows only a
    track LABEL ("8"), so without this the ref matches no OSM platform and the walk
    dies as `platform_not_found` even though the map just offered that platform.
    Feeding the marker's own coordinate in as core/'s Tier-3 anchor keeps the map
    and the router in agreement: anything we draw, we can route.

    Best-effort by construction -- any failure yields (None, None) and the walk
    proceeds exactly as before. Costs one indexed lookup, and Tier-3 only consults
    these when the ref matches no OSM platform, so refs that DO resolve are
    byte-for-byte unchanged."""
    try:
        with conn.cursor() as cur:
            station = relation_coord(cur, key.relation_id)
        if station is None:
            return None, None
        lat, lon = station
        return (platform_labels.track_coord(lat, lon, key.from_platform),
                platform_labels.track_coord(lat, lon, key.to_platform))
    except Exception:  # noqa: BLE001 -- an overlay miss must never fail a walk
        return None, None


def _boarding_for(
    conn, key: schemas.WalkKey, doc: dict,
    formation_provider: Optional[Callable[[], object]] = None,
) -> schemas.BoardingGuidance | None:
    """Step-off guidance for a found walk, or None. Best-effort: a failure here
    (a coarse platform, a DB hiccup) must never fail the walk it enriches, so
    everything is caught and dropped -- the geometry still returns.

    `formation_provider` is the coach-enrichment seam: when the arriving train's
    identity is available, production can pass `boarding.db_formation_provider(...)`
    (or any zero-arg provider) to fill the coach. It is not in the WalkKey today,
    so this defaults to None -- position-only, unchanged behaviour."""
    stepoff = stepoff_node_of(doc)
    if stepoff is None:
        return None
    try:
        g = compute_boarding(conn, key.relation_id, key.from_platform, key.to_platform,
                             stepoff, formation_provider=formation_provider)
    except Exception:  # noqa: BLE001 -- boarding is progressive enhancement
        return None
    return schemas.BoardingGuidance(**g.as_dict())


def build_walk(
    conn, key: schemas.WalkKey,
    formation_provider: Optional[Callable[[], object]] = None,
) -> schemas.WalkResult:
    """Produce one walk's viz_export (plus step-off guidance), degrading to a
    typed reason on failure. `formation_provider` is threaded to boarding for
    optional coach enrichment (default None -- position-only)."""
    base = dict(
        relation_id=key.relation_id,
        from_platform=key.from_platform,
        to_platform=key.to_platform,
        step_free=key.step_free,
    )
    # A chosen facility (the 'walk to nearest' door) rides into the export's
    # details layer as the focus POI -- projected from its already-known
    # coordinate, so no planet extract is needed.
    attach_pois = None
    if key.poi is not None:
        attach_pois = [{
            "lat": key.poi.lat, "lon": key.poi.lon,
            "name": key.poi.name, "category": key.poi.category,
            "subtype": key.poi.subtype, "level_raw": key.poi.level,
        }]
    # Anchor coordinates: the caller's own (a journey stop forwards them on the
    # Transfer) win; otherwise fall back to the harvested overlay so a browse-mode
    # walk between two map-visible-but-OSM-unlabelled tracks still routes.
    from_coord, to_coord = key.from_coord, key.to_coord
    if from_coord is None or to_coord is None:
        overlay_from, overlay_to = _overlay_coords(conn, key)
        from_coord = from_coord or overlay_from
        to_coord = to_coord or overlay_to
    try:
        doc = export(
            conn,
            key.relation_id,
            key.from_platform,
            key.to_platform,
            algorithm=_ALGORITHM,
            details=False,
            stitch=config.STITCH_BRIDGES,
            avoid_elevators=key.step_free,
            all_platforms=key.all_platforms,
            # Draw the same walk the verdict resolved: when the feed's platform
            # code isn't in OSM (e.g. Köln Hbf "89"/"88"), these coordinates let
            # viz_export snap to the real platform instead of failing to geometry.
            from_coord=from_coord,
            to_coord=to_coord,
            attach_pois=attach_pois,
        )
    except SystemExit:
        # export() raises SystemExit("no coordinates resolved ...") when the
        # relation/refs don't yield geometry -- a data gap, not a server error.
        return schemas.WalkResult(**base, ok=False, reason=NO_GEOMETRY)
    except Exception:  # noqa: BLE001 -- one bad key must not fail a batch
        return schemas.WalkResult(**base, ok=False, reason=WALK_BUILD_FAILED)

    return schemas.WalkResult(**base, ok=True, export=doc,
                              boarding=_boarding_for(conn, key, doc, formation_provider))


def build_walks(conn, keys: List[schemas.WalkKey]) -> schemas.WalksResponse:
    """Batch: build every key in order. Isolated failures stay per-key."""
    return schemas.WalksResponse(walks=[build_walk(conn, k) for k in keys])
