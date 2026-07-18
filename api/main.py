"""
transfr HTTP API (FastAPI).

    .venv/bin/uvicorn api.main:app --port 5001

Endpoints:
  GET  /health                                   liveness
  GET  /stations?q=                              station autocomplete (CSV-backed)
  GET  /journeys?from=&to=&time=&max=&no_elevators=
                                                 journeys, each change of train
                                                 assessed for walkability (the product);
                                                 no_elevators routes every transfer's
                                                 VERDICT without lifts (core/'s
                                                 avoid_elevators), not just /walk geometry
  GET  /transfer?lat=&lon=&from_platform=&to_platform=
                                                 debug: platform-to-platform walk at
                                                 the station nearest a coordinate
  GET  /station-platforms?lat=&lon=              the platforms (+ relation_id) at the
                                                 station nearest a coordinate; powers
                                                 the walk-only door's platform pickers
  GET  /station-walk?lat=&lon=&from_platform=&step_free=
                                                 the 'full station walk' tool: from one
                                                 source platform, the walk to every other
                                                 platform at the nearest station,
                                                 in platform-ref order (one pathfind each)
  GET  /facilities?lat=&lon=&category=           facilities (POIs) of a category near a
                                                 station, nearest first; degrades to a
                                                 typed reason when the POI layer is absent
  GET  /station-health?lat=&lon=                 one station's platform-connectivity
                                                 breakdown (connected/stitchable/island
                                                 over every pair); the Map-health tool
  GET  /walk?relation_id=&from_platform=&to_platform=&step_free=
                                                 one transfer's drawable walk geometry
                                                 (viz_export); cacheable
  POST /walks  {keys:[{relation_id,from_platform,to_platform,step_free}]}
                                                 batch prefetch of a journey's walks
                                                 in one round trip
"""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from api import stations  # CSV autocomplete
from ground_truth import find_shortest_path
from search_context import list_platform_refs

from api import config, platform_labels, schemas
from api.bridge import resolve_station
from api.db import close_pool, connection, init_pool
from api.facilities import build_facilities, build_facility_map
from api.pipeline import assess_interchanges, plan_journeys
from api.security import limiter, require_api_key
from api.station_health import build_station_health
from api.station_walk import build_station_walk
from api.transfers import STATION_UNRESOLVED
from api.walks import build_walk, build_walks

# Walk geometry is deterministic given the DB, so it caches well. Not truly
# immutable (a core/etl.py rebuild changes it), so a day, not forever.
_WALK_CACHE_CONTROL = "public, max-age=86400"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()  # best-effort; deferred to first request if the DB is down
    if config.OSM_STATION_INDEX:
        _load_osm_station_index()
    yield
    close_pool()


def _load_osm_station_index() -> None:
    """Best-effort: fold the deployed OSM station names into the autocomplete
    index at startup. A DB that's down here just leaves the CSV index in place
    (same posture as init_pool) -- it must never stop the app from booting."""
    try:
        with connection() as conn:
            added = stations.load_osm_stations(conn)
        print(f"[api] OSM station index: +{added} stations", flush=True)
    except Exception as e:  # noqa: BLE001 -- degrade to CSV-only, never crash startup
        print(f"[api] OSM station index unavailable, using CSV only: "
              f"{type(e).__name__}: {e}", flush=True)


app = FastAPI(title="transfr", version="0.1.0", lifespan=lifespan)

# The API key gates every data route; /health stays open so the tunnel and any
# uptime check can probe liveness without the secret. Attached per-route below
# via this shared dependency list rather than app-wide, which would catch /health.
_PROTECTED = [Depends(require_api_key)]
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Rate limiting (no-op unless TRANSFR_RATE_LIMIT is set). The middleware applies
# the configured default limit to every route; the handler turns an exceeded
# limit into a 429 with Retry-After.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


def get_conn():
    """Request-scoped pooled DB connection (FastAPI dependency)."""
    with connection() as conn:
        yield conn


def _parse_when(time_str: Optional[str]) -> datetime:
    if not time_str:
        return datetime.now()
    try:
        # Z-tolerant: datetime.fromisoformat rejects a trailing 'Z' before 3.11.
        return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"invalid time: {time_str!r}")


@app.get("/health")
@limiter.exempt
def health():
    return {"status": "ok"}


@app.get("/stations", response_model=List[schemas.StationSuggestion], dependencies=_PROTECTED)
def get_stations(q: str = Query(min_length=2, description="station name prefix")):
    return stations.autocomplete_station(q, max_results=8)


@app.get("/journeys", response_model=schemas.JourneysResponse, dependencies=_PROTECTED)
def get_journeys(
    from_: str = Query(alias="from", min_length=1, description="origin station name"),
    to: str = Query(min_length=1, description="destination station name"),
    time: Optional[str] = Query(default=None, description="ISO 8601 departure time; defaults to now"),
    max: int = Query(default=config.DEFAULT_MAX_JOURNEYS, ge=1, le=config.MAX_JOURNEYS_LIMIT),
    assess: bool = Query(default=True, description="assess each transfer's walkability; "
                         "false returns pending transfers instantly to be streamed via /assess"),
    no_elevators: bool = Query(default=False, description="never route through a lift "
                               "(core/'s avoid_elevators, i.e. the --no-elevators profile): "
                               "affects each transfer's walkability VERDICT, not just the "
                               "drawn geometry. Selects the same core/ profile `/walk`'s "
                               "`step_free` does, but on the verdict path"),
    conn=Depends(get_conn),
):
    when = _parse_when(time)
    try:
        return plan_journeys(conn, from_, to, when, max_journeys=max,
                             buffer_s=config.BUFFER_S, assess=assess,
                             avoid_elevators=no_elevators)
    except ValueError as e:
        # unresolvable origin/destination name
        raise HTTPException(status_code=404, detail=str(e))
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"journey provider error: {e}")


@app.post("/assess", response_model=schemas.AssessResponse, dependencies=_PROTECTED)
def post_assess(req: schemas.AssessRequest, conn=Depends(get_conn)):
    """Assess a batch of changes of train, streaming the verdicts a fast
    `/journeys?assess=false` deferred. The client sends the interchange fields it
    already holds (from the journey's legs); each comes back as a full Transfer.
    Called with one interchange per request, fired concurrently, it fills a
    journey's verdicts in as fast as each pathfind returns."""
    if len(req.interchanges) > config.MAX_ASSESS_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"too many interchanges: {len(req.interchanges)} > {config.MAX_ASSESS_BATCH}",
        )
    return assess_interchanges(conn, req.interchanges, buffer_s=config.BUFFER_S,
                               avoid_elevators=req.no_elevators)


@app.get("/transfer", response_model=schemas.PlatformWalkResponse, dependencies=_PROTECTED)
def get_transfer(
    lat: float,
    lon: float,
    from_platform: str = Query(min_length=1),
    to_platform: str = Query(min_length=1),
    conn=Depends(get_conn),
):
    with conn.cursor() as cur:
        match = resolve_station(cur, lat, lon)
    if match is None:
        return schemas.PlatformWalkResponse(
            lat=lat, lon=lon, from_platform=from_platform, to_platform=to_platform,
            found=False, reason=STATION_UNRESOLVED,
        )
    result = find_shortest_path(conn, match.relation_id, from_platform, to_platform,
                                algorithm="astar", use_stitch_bridges=config.STITCH_BRIDGES)
    found = bool(result.get("found"))
    return schemas.PlatformWalkResponse(
        lat=lat, lon=lon,
        relation_id=match.relation_id, station=match.name,
        from_platform=from_platform, to_platform=to_platform,
        found=found,
        walk_time_s=result.get("walking_time_seconds"),
        walk_distance_m=result.get("walking_distance_meters"),
        reason=None if found else result.get("reason"),
    )


@app.get("/station-platforms", response_model=schemas.StationPlatformsResponse, dependencies=_PROTECTED)
def get_station_platforms(lat: float, lon: float, conn=Depends(get_conn)):
    """The platforms at the station nearest (lat, lon), plus the relation_id a
    subsequent /walk between two of them uses. Powers the walk-only door: the
    platform pickers adapt to the entered station, and both calls resolve the
    same station because they key off the same coordinate -> relation."""
    with conn.cursor() as cur:
        match = resolve_station(cur, lat, lon)
        if match is None:
            return schemas.StationPlatformsResponse(
                lat=lat, lon=lon, found=False, reason=STATION_UNRESOLVED,
            )
        refs = list_platform_refs(cur, match.relation_id)
    # Fold in the harvested/ingested overlay tracks (the labels OSM lacks) so every
    # picker that reads this endpoint offers the full platform set -- with coords, so
    # a walk to one still routes. Empty when no overlay is present for this station.
    feed = platform_labels.platform_markers(lat, lon)
    feed_platforms = [schemas.PlatformMarker(**m) for m in feed[1]] if feed else []
    return schemas.StationPlatformsResponse(
        lat=lat, lon=lon, relation_id=match.relation_id, station=match.name,
        found=True, platforms=refs, feed_platforms=feed_platforms,
    )


# `found=False` reason when the harvested platform-label overlay isn't on this host.
NO_PLATFORM_LABELS = "no_platform_labels"


@app.get("/station-platform-markers", response_model=schemas.StationPlatformMarkersResponse,
         dependencies=_PROTECTED)
def get_station_platform_markers(lat: float, lon: float):
    """The feed's platform labels for the station nearest (lat, lon), as map markers
    -- every platform's track number at its real coordinate, the labels OSM lacks
    (see api/platform_labels.py, harvested by core/dbgen/harvest_platform_labels.py).
    No DB: served from the harvested overlay file, so it works even with the DB
    down. `found=False` with `no_platform_labels` when that overlay isn't present
    on this host (nobody has run the harvest), or `station_unresolved` when no
    harvested station sits near the coordinate."""
    if not platform_labels.available():
        return schemas.StationPlatformMarkersResponse(
            lat=lat, lon=lon, found=False, reason=NO_PLATFORM_LABELS,
        )
    match = platform_labels.platform_markers(lat, lon)
    if match is None:
        return schemas.StationPlatformMarkersResponse(
            lat=lat, lon=lon, found=False, reason=STATION_UNRESOLVED,
        )
    name, markers = match
    return schemas.StationPlatformMarkersResponse(
        lat=lat, lon=lon, station=name, found=True,
        platforms=[schemas.PlatformMarker(**m) for m in markers],
    )


@app.get("/station-walk", response_model=schemas.StationWalkResponse, dependencies=_PROTECTED)
def get_station_walk(
    lat: float,
    lon: float,
    from_platform: str = Query(min_length=1, description="source platform ref to walk from"),
    step_free: bool = Query(default=False, description="route without elevators"),
    conn=Depends(get_conn),
):
    """The 'full station walk' advanced tool: from one source platform, the real
    walk to every OTHER platform at the station nearest (lat, lon), one pathfind
    each, in platform-ref order. Honest degradation: an unreachable platform is a
    `found=False` row with core/'s reason; a coordinate with no station near it
    returns a top-level `found=False`. `step_free` routes elevator-free."""
    return build_station_walk(conn, lat, lon, from_platform, step_free)


@app.get("/facilities", response_model=schemas.FacilitiesResponse, dependencies=_PROTECTED)
def get_facilities(
    lat: float,
    lon: float,
    category: str = Query(min_length=1, description="facility category, e.g. toilets/coffee/atm"),
    from_platform: Optional[str] = Query(default=None, min_length=1,
                                         description="optional platform anchor for a routed walk"),
    conn=Depends(get_conn),
):
    """Facilities of `category` near the station nearest (lat, lon), nearest first.

    The POI layer is the optional `viz_export` details extract (amenity/shop/...);
    where it isn't producible on this host the response degrades to `found=False`
    with `reason="no_poi_layer"` rather than guessing. With `from_platform`, each
    facility also carries a routed walk to its nearest platform."""
    return build_facilities(conn, lat, lon, category, from_platform=from_platform)


@app.get("/facility-map", response_model=schemas.FacilityMapResponse, dependencies=_PROTECTED)
def get_facility_map(
    lat: float,
    lon: float,
    category: str = Query(min_length=1, description="facility category, e.g. toilets/coffee/atm"),
    conn=Depends(get_conn),
):
    """The whole station in 3D with EVERY facility of `category` pinned on it -- the
    map-first "walk to nearest" surface. One round trip: a browse `viz_export` (all
    platforms, no single route) with each facility attached as a focus POI, plus the
    matching ranked `facilities` list in the same order, so a tapped pin maps back to
    its row. Degrades to `found=False` with a typed `reason` (`no_poi_layer`,
    `none_mapped`, ...) exactly like `/facilities`."""
    return build_facility_map(conn, lat, lon, category)


@app.get("/station-health", response_model=schemas.StationHealthResponse, dependencies=_PROTECTED)
def get_station_health(lat: float, lon: float, conn=Depends(get_conn)):
    """One station's platform-connectivity breakdown for the Map-health tool: the
    station nearest (lat, lon), with every unordered platform pair bucketed
    connected / stitchable / island (two find_shortest_path passes each -- plain,
    then with stitch bridges). A very large station is sampled to bound the pair
    count (see api/station_health.py); `found=False` when nothing resolves near
    the coordinate."""
    return build_station_health(conn, lat, lon)


@app.get("/walk", response_model=schemas.WalkResult, dependencies=_PROTECTED)
def get_walk(
    response: Response,
    relation_id: int = Query(description="stop_area relation id (from a Transfer)"),
    from_platform: str = Query(min_length=1, description="arrival platform ref"),
    to_platform: str = Query(min_length=1, description="departure platform ref"),
    step_free: bool = Query(default=False, description="route without elevators"),
    all_platforms: bool = Query(default=False, description="station-map mode: include every platform"),
    poi_lat: Optional[float] = Query(default=None, description="'walk to nearest' focus facility latitude"),
    poi_lon: Optional[float] = Query(default=None, description="'walk to nearest' focus facility longitude"),
    poi_category: Optional[str] = Query(default=None, description="focus facility OSM category (amenity/shop/...)"),
    poi_subtype: Optional[str] = Query(default=None, description="focus facility OSM subtype (toilets/cafe/...)"),
    poi_name: Optional[str] = Query(default=None, description="focus facility display name"),
    poi_level: Optional[str] = Query(default=None, description="focus facility OSM level tag"),
    conn=Depends(get_conn),
):
    """One transfer's drawable walk geometry (the `viz_export` document). Keyed by
    the triple a Transfer already carries, so the client just forwards them. The
    result is deterministic given the DB, hence cacheable.

    The optional `poi_*` params carry a chosen facility (the 'walk to nearest'
    door): its already-known coordinate is projected into the export's details
    layer as the focus, so the 3D model draws the facility beside the platform."""
    poi = None
    if poi_lat is not None and poi_lon is not None and poi_category:
        poi = schemas.WalkPOI(lat=poi_lat, lon=poi_lon, name=poi_name,
                              category=poi_category, subtype=poi_subtype, level=poi_level)
    key = schemas.WalkKey(relation_id=relation_id, from_platform=from_platform,
                          to_platform=to_platform, step_free=step_free,
                          all_platforms=all_platforms, poi=poi)
    result = build_walk(conn, key)
    if result.ok:
        response.headers["Cache-Control"] = _WALK_CACHE_CONTROL
    return result


@app.post("/walks", response_model=schemas.WalksResponse, dependencies=_PROTECTED)
def post_walks(req: schemas.WalksRequest, conn=Depends(get_conn)):
    """Batch prefetch: build every requested walk in one round trip so a selected
    journey's transfers cache to the device together. One bad key fails only
    itself (its `ok` is False); the batch still returns the rest."""
    if len(req.keys) > config.MAX_WALKS_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"too many walk keys: {len(req.keys)} > {config.MAX_WALKS_BATCH}",
        )
    return build_walks(conn, req.keys)
