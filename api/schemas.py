"""
Pydantic response models -- the typed HTTP contract for the API.

The journey shape mirrors what journeys.search_journeys already produces, plus a
`transfers` list per journey: one assessment per change of train, and a
journey-level `verdict` rolled up from them. `verdict`/`reason` values are the
constants in api/transfers.py.
"""

from typing import List, Optional

from pydantic import BaseModel


class Place(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class Leg(BaseModel):
    mode: str
    train_name: Optional[str] = None
    origin: Place
    destination: Place
    departure: Optional[str] = None
    arrival: Optional[str] = None
    planned_departure: Optional[str] = None
    planned_arrival: Optional[str] = None
    departure_platform: Optional[str] = None
    arrival_platform: Optional[str] = None
    departure_delay_s: Optional[int] = None
    arrival_delay_s: Optional[int] = None
    cancelled: bool = False
    distance_m: Optional[int] = None


class Transfer(BaseModel):
    """A single change-of-train, assessed against the layover."""

    at_station: Optional[str] = None
    relation_id: Optional[int] = None
    arrival_platform: Optional[str] = None
    departure_platform: Optional[str] = None
    layover_s: Optional[float] = None
    walk_time_s: Optional[float] = None
    walk_distance_m: Optional[float] = None
    verdict: str
    reason: Optional[str] = None


class Journey(BaseModel):
    id: Optional[str] = None
    date: Optional[str] = None
    duration_s: Optional[int] = None
    num_changes: int
    verdict: str  # rolled up from `transfers` (worst wins); feasible when direct
    legs: List[Leg]
    transfers: List[Transfer]


class JourneysResponse(BaseModel):
    origin: Place
    destination: Place
    departure_time: Optional[str] = None
    journeys: List[Journey]


class StationSuggestion(BaseModel):
    id: Optional[str] = None
    name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country: Optional[str] = None


class PlatformWalkResponse(BaseModel):
    """The /transfer debug endpoint: the walk between two platforms at the one
    station nearest a coordinate (no journey timing, so no verdict)."""

    lat: float
    lon: float
    relation_id: Optional[int] = None
    station: Optional[str] = None
    from_platform: str
    to_platform: str
    found: bool
    walk_time_s: Optional[float] = None
    walk_distance_m: Optional[float] = None
    reason: Optional[str] = None
