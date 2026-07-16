"""
Pydantic response models -- the typed HTTP contract for the API.

The journey shape mirrors what journeys.search_journeys already produces, plus a
`transfers` list per journey: one assessment per change of train, and a
journey-level `verdict` rolled up from them. `verdict`/`reason` values are the
constants in api/transfers.py.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


# ---------------------------------------------------------------------------
# Streaming assessment -- /assess
#
# `/journeys?assess=false` returns the itineraries instantly with `pending`
# transfers; the client then streams each real verdict in by POSTing the
# interchange fields it already holds (from the journey's legs) to /assess. The
# request mirrors assess_transfer's inputs exactly; the response is the same
# Transfer the enriched /journeys would have carried.
# ---------------------------------------------------------------------------


class AssessInterchange(BaseModel):
    """One change of train to assess: the arrival end (of the incoming train) and
    the departure end (of the onward train). `at_station` is a display fallback."""

    at_station: Optional[str] = None
    arr_lat: Optional[float] = None
    arr_lon: Optional[float] = None
    arr_platform: Optional[str] = None
    arr_time: Optional[str] = None
    dep_lat: Optional[float] = None
    dep_lon: Optional[float] = None
    dep_platform: Optional[str] = None
    dep_time: Optional[str] = None


class AssessRequest(BaseModel):
    interchanges: List[AssessInterchange] = Field(default_factory=list)


class AssessResponse(BaseModel):
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


class StationPlatformsResponse(BaseModel):
    """The platforms at the station nearest a coordinate. Powers the walk-only
    door: the platform pickers adapt to the entered station, and `relation_id`
    is what a subsequent /walk between two of these refs uses, so the two calls
    resolve the same station. `found=False` (with `reason`) when no station sits
    near the coordinate."""

    lat: float
    lon: float
    relation_id: Optional[int] = None
    station: Optional[str] = None
    found: bool
    platforms: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Walk geometry (viz_export) delivery -- /walk and /walks
#
# A journey's verdict spine comes from /journeys; the drawable per-transfer walk
# geometry (section / per-level / 3D / AR) is fetched separately so /journeys
# stays lean and each walk is independently cacheable. A walk is keyed by the
# exact triple assess_transfer already resolves on every Transfer -- relation_id
# + arrival_platform + departure_platform -- so the client just echoes those.
# ---------------------------------------------------------------------------


class WalkKey(BaseModel):
    """Identifies one platform-to-platform walk. These three fields are exactly
    what a Transfer already carries, so the client forwards them verbatim.
    `step_free` requests the elevator-free variant (a different route, hence a
    different walk time than the verdict's)."""

    relation_id: int
    from_platform: str
    to_platform: str
    step_free: bool = False


class BoardingGuidance(BaseModel):
    """Where to be on the arriving train so you step off nearest the onward walk
    (see api/boarding.py). `stepoff_fraction` is 0 at the platform end farthest
    from the departure side, 1 at the nearest -- a larger fraction means "board
    further toward your connection". `time_saved_s` is the extra platform-walking
    the far end would cost (an upper bound, hence "up to"). `significance` is
    high/some/low. `coach` is filled only when a live formation feed resolves it;
    `reason` explains what's missing (usually `no_formation_feed`)."""

    arrival_platform: str
    departure_platform: str
    platform_length_m: float = 0.0
    stepoff_offset_m: float = 0.0
    stepoff_fraction: float = 0.0
    time_saved_s: float = 0.0
    significance: str = "low"
    coach: Optional[str] = None
    formation_source: Optional[str] = None
    reason: Optional[str] = None


class WalkResult(BaseModel):
    """One walk's geometry, or a reason it couldn't be built. `export` is the
    full `core/viz_export.py` document (mirrored by `VizExport` on the Swift
    side); it is passed through untyped here because it is a large, already-
    tested geometry payload -- modelling it twice buys nothing.

    Two failure levels: `ok=False` means no export could be produced at all
    (bad relation / unresolvable platforms); `ok=True` with `export.path.found
    == false` means the export exists but the two platforms don't connect
    (a real, drawable 'no route' state).

    `boarding` is the step-off guidance derived from the same resolved path; it
    is present only on a found walk whose arrival platform is a measurable edge."""

    relation_id: int
    from_platform: str
    to_platform: str
    step_free: bool = False
    ok: bool
    reason: Optional[str] = None
    export: Optional[Dict[str, Any]] = None
    boarding: Optional[BoardingGuidance] = None


class WalksRequest(BaseModel):
    """Batch prefetch: one round trip for a selected journey's transfers."""

    keys: List[WalkKey] = Field(default_factory=list)


class WalksResponse(BaseModel):
    walks: List[WalkResult]
