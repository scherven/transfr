"""
transfr HTTP API (FastAPI).

    .venv/bin/uvicorn api.main:app --port 5001

Endpoints:
  GET /health                                    liveness
  GET /stations?q=                               station autocomplete (CSV-backed)
  GET /journeys?from=&to=&time=&max=             journeys, each change of train
                                                 assessed for walkability (the product)
  GET /transfer?lat=&lon=&from_platform=&to_platform=
                                                 debug: platform-to-platform walk at
                                                 the station nearest a coordinate
"""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import stations  # root-level CSV autocomplete
from ground_truth import find_shortest_path

from api import config, schemas
from api.bridge import resolve_station
from api.db import close_pool, connection, init_pool
from api.pipeline import plan_journeys
from api.transfers import STATION_UNRESOLVED


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()  # best-effort; deferred to first request if the DB is down
    yield
    close_pool()


app = FastAPI(title="transfr", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)


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
def health():
    return {"status": "ok"}


@app.get("/stations", response_model=List[schemas.StationSuggestion])
def get_stations(q: str = Query(min_length=2, description="station name prefix")):
    return stations.autocomplete_station(q, max_results=8)


@app.get("/journeys", response_model=schemas.JourneysResponse)
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


@app.get("/transfer", response_model=schemas.PlatformWalkResponse)
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
