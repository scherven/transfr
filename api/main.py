"""
transfr HTTP API (FastAPI).

    .venv/bin/uvicorn api.main:app --port 5001

Endpoints:
  GET  /health                                   liveness
  GET  /stations?q=                              station autocomplete (CSV-backed)
  GET  /journeys?from=&to=&time=&max=            journeys, each change of train
                                                 assessed for walkability (the product)
  GET  /transfer?lat=&lon=&from_platform=&to_platform=
                                                 debug: platform-to-platform walk at
                                                 the station nearest a coordinate
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

from api import config, schemas
from api.bridge import resolve_station
from api.db import close_pool, connection, init_pool
from api.pipeline import plan_journeys
from api.security import limiter, require_api_key
from api.transfers import STATION_UNRESOLVED
from api.walks import build_walk, build_walks

# Walk geometry is deterministic given the DB, so it caches well. Not truly
# immutable (a core/etl.py rebuild changes it), so a day, not forever.
_WALK_CACHE_CONTROL = "public, max-age=86400"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()  # best-effort; deferred to first request if the DB is down
    yield
    close_pool()


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
    conn=Depends(get_conn),
):
    when = _parse_when(time)
    try:
        return plan_journeys(conn, from_, to, when, max_journeys=max, buffer_s=config.BUFFER_S)
    except ValueError as e:
        # unresolvable origin/destination name
        raise HTTPException(status_code=404, detail=str(e))
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"journey provider error: {e}")


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
    result = find_shortest_path(conn, match.relation_id, from_platform, to_platform, algorithm="astar")
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


@app.get("/walk", response_model=schemas.WalkResult, dependencies=_PROTECTED)
def get_walk(
    response: Response,
    relation_id: int = Query(description="stop_area relation id (from a Transfer)"),
    from_platform: str = Query(min_length=1, description="arrival platform ref"),
    to_platform: str = Query(min_length=1, description="departure platform ref"),
    step_free: bool = Query(default=False, description="route without elevators"),
    conn=Depends(get_conn),
):
    """One transfer's drawable walk geometry (the `viz_export` document). Keyed by
    the triple a Transfer already carries, so the client just forwards them. The
    result is deterministic given the DB, hence cacheable."""
    key = schemas.WalkKey(relation_id=relation_id, from_platform=from_platform,
                          to_platform=to_platform, step_free=step_free)
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
