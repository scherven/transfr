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
    # Real platform sign when this leg boards/alights at a platform whose feed
    # label is an internal code OSM doesn't carry (Köln Hbf "89" -> "7"). Recovered
    # from the adjacent transfer's coordinate resolution; null when the feed's
    # label already is the real one. Same hint contract as Transfer.
    departure_platform_actual: Optional[str] = None
    arrival_platform_actual: Optional[str] = None
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
    # The real platform sign when the feed's label above is an internal code the
    # station map doesn't carry (e.g. Köln Hbf reports "89"/"88" for public tracks
    # 7/6). Recovered by coordinate; null when the feed's label already is the real
    # one. When present, show "platform <actual>" with <arrival_platform> as a "the
    # operator lists it as N" hint.
    arrival_platform_actual: Optional[str] = None
    departure_platform_actual: Optional[str] = None
    # The two platforms' real coordinates (the journey stops). Carried so the
    # client's /walk request can forward them (WalkKey), letting the drawn walk
    # snap to the real platform when the feed's code isn't in OSM (see WalkKey).
    arr_lat: Optional[float] = None
    arr_lon: Optional[float] = None
    dep_lat: Optional[float] = None
    dep_lon: Optional[float] = None
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
    # Route every walk without lifts (core/'s avoid_elevators). Batch-level, so
    # the client's per-interchange /assess calls carry the same "no elevators"
    # preference the journey was searched under -- the streamed verdicts then
    # match an assess=True search's. Mirrors the `/journeys` `no_elevators` query.
    # NB: the same core/ profile WalkKey.step_free below selects, but applied to
    # the verdict path rather than the drawn /walk geometry.
    no_elevators: bool = False


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


class PlatformMarker(BaseModel):
    """One platform's label at its real coordinate, from the feed harvest
    (core/dbgen/harvest_platform_labels.py) or a bulk GTFS ingest. `n` is how many
    stop observations backed it -- a confidence signal, higher = more sightings."""

    track: str
    lat: float
    lon: float
    n: int = 1


class StationPlatformsResponse(BaseModel):
    """The platforms at the station nearest a coordinate. Powers the walk-only
    door: the platform pickers adapt to the entered station, and `relation_id`
    is what a subsequent /walk between two of these refs uses, so the two calls
    resolve the same station. `found=False` (with `reason`) when no station sits
    near the coordinate.

    `platforms` are the OSM refs (what the map draws). `feed_platforms` are the
    harvested/ingested overlay tracks -- the labels OSM lacks -- WITH coordinates,
    so every picker reading this endpoint can offer the FULL platform set and still
    route a walk to one (the coord feeds the WalkKey / core Tier-3 fallback). Kept a
    separate field so the OSM-only `platforms` still drives the map's colour split."""

    lat: float
    lon: float
    relation_id: Optional[int] = None
    station: Optional[str] = None
    found: bool
    platforms: List[str] = Field(default_factory=list)
    feed_platforms: List[PlatformMarker] = Field(default_factory=list)
    reason: Optional[str] = None


class StationPlatformMarkersResponse(BaseModel):
    """The feed's platform labels for the station nearest a coordinate, as map
    markers -- the labels OSM lacks, placed at their real positions rather than
    matched to OSM polygons. `found=False` (with `reason`) when no harvested
    station is near, or `no_platform_labels` when the overlay isn't present on this
    host (nobody has run the harvest) -- honest degradation, never an error."""

    lat: float
    lon: float
    station: Optional[str] = None
    found: bool
    platforms: List[PlatformMarker] = Field(default_factory=list)
    reason: Optional[str] = None


class StationWalkRow(BaseModel):
    """One destination platform's walk FROM the chosen source platform (a row of
    the /station-walk 'full station walk' tool). `found=False` (with `reason`) is
    an honest 'these two don't connect' -- core/'s own reason
    (platform_not_found / disconnected / exceeded_plausibility_bound) -- not an
    error, so it renders as an unreachable row rather than failing the request."""

    to_platform: str
    found: bool
    walk_time_s: Optional[float] = None
    walk_distance_m: Optional[float] = None
    reason: Optional[str] = None


class StationWalkResponse(BaseModel):
    """Every OTHER platform's walk FROM one source platform at the station nearest
    a coordinate -- the 'full station walk' advanced tool (one change of station,
    not a journey). One find_shortest_path per platform, using the SAME settings a
    transfer verdict uses (astar + stitch bridges per config), so a row's walk time
    equals what a `/walk` between the same two refs would report. `results` is
    sorted nearest-first: reachable rows by ascending walk distance, then the
    unreachable ones. `found=False` at the top level (with `reason`) when no
    station sits near the coordinate; individual unreachable platforms are
    `found=False` rows inside a `found=True` response."""

    lat: float
    lon: float
    relation_id: Optional[int] = None
    station: Optional[str] = None
    from_platform: str
    step_free: bool = False
    found: bool
    results: List[StationWalkRow] = Field(default_factory=list)
    reason: Optional[str] = None


class StationHealthPair(BaseModel):
    """One platform pair that does not plainly connect. `kind` is 'stitchable'
    (a route exists only once synthetic stitch bridges are enabled) or 'island'
    (no route found either way). Surfaced as a few worked examples of a station's
    disconnects."""

    from_platform: str
    to_platform: str
    kind: str


class StationHealthResponse(BaseModel):
    """A single station's platform-connectivity breakdown -- the Map-health tool's
    per-station query (/station-health). Every unordered platform pair is bucketed
    connected / stitchable / island by two `find_shortest_path` passes (plain, then
    with stitch bridges); `connected`/`stitchable`/`island` are pair counts and the
    matching `*_pct` are their share of the pairs evaluated. `sampled` is true when a
    pathologically large station was down-sampled to bound the pair count (see
    api/station_health.py). `examples` lists a few of the non-connected pairs.
    `found=False` (with `reason`) when no station sits near the coordinate."""

    lat: float
    lon: float
    relation_id: Optional[int] = None
    station: Optional[str] = None
    found: bool
    platform_count: int = 0
    connected: int = 0
    stitchable: int = 0
    island: int = 0
    connected_pct: float = 0.0
    stitchable_pct: float = 0.0
    island_pct: float = 0.0
    sampled: bool = False
    examples: List[StationHealthPair] = Field(default_factory=list)
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Nearest facility -- /facilities
#
# The POI layer (amenity/shop/... near a station) is NOT in the tag-scoped
# transfr_eu DB; it comes from the optional `viz_export` details layer, which
# needs a local planet extract (see api/facilities.py). So this endpoint follows
# the same honest-degradation pattern as boarding guidance: when the layer isn't
# producible on this host, `found=False` with a typed `reason` (`no_poi_layer`)
# rather than a guess. The pure ranking (nearest-first, category-filtered) is
# tested offline against a synthetic POI list.
# ---------------------------------------------------------------------------


class Facility(BaseModel):
    """One mapped facility (a POI) near the station, ranked by straight-line
    distance from the station centroid. `category` is the OSM bucket
    (amenity/shop/tourism/office/leisure) and `subtype` the specific tag
    (`toilets`, `cafe`, ...), mirroring the `viz_export` details shape. The
    `nearest_platform`/`walk_*` fields are filled only when a `from_platform`
    anchor was given and a routed walk to the facility's nearest platform
    resolved -- otherwise `distance_m` (straight-line) is the only measure."""

    name: Optional[str] = None
    category: str
    subtype: Optional[str] = None
    level: Optional[str] = None
    distance_m: float
    lat: Optional[float] = None
    lon: Optional[float] = None
    nearest_platform: Optional[str] = None
    walk_time_s: Optional[float] = None
    walk_distance_m: Optional[float] = None


class FacilitiesResponse(BaseModel):
    """Facilities of one `category` near the station nearest a coordinate, nearest
    first. `found` is True iff at least one was returned; otherwise `reason` says
    why (`station_unresolved`, `unsupported_category`, `no_poi_layer` when the POI
    source isn't available on this host, or `none_mapped` when the layer is present
    but this station tags none of that category)."""

    lat: float
    lon: float
    relation_id: Optional[int] = None
    station: Optional[str] = None
    category: str
    found: bool
    reason: Optional[str] = None
    facilities: List[Facility] = Field(default_factory=list)


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
    # Station-map (browse) mode: include every platform at the station, not just
    # the ones the walked corridor touched.
    all_platforms: bool = False
    # The platforms' real coordinates (from the journey stop), forwarded from the
    # Transfer. Only used when from_platform/to_platform match no OSM platform at
    # the station -- then viz_export snaps them to the real platform (Tier 3), so
    # the drawn walk matches the verdict. Null for browse mode / normal stations.
    from_lat: Optional[float] = None
    from_lon: Optional[float] = None
    to_lat: Optional[float] = None
    to_lon: Optional[float] = None

    @property
    def from_coord(self) -> Optional[tuple]:
        return (self.from_lat, self.from_lon) if self.from_lat is not None and self.from_lon is not None else None

    @property
    def to_coord(self) -> Optional[tuple]:
        return (self.to_lat, self.to_lon) if self.to_lat is not None and self.to_lon is not None else None


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
