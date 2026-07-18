"""
Nearest-facility lookup for one station -- the `/facilities` endpoint's builder.

WHAT IS AND ISN'T AVAILABLE.
  * The station and its platforms are in the transfr_eu DB (railway/pedestrian
    tag scope), so resolving the station and its platform centroids is always
    possible where the DB is up.
  * The FACILITIES themselves (toilets, cafes, ATMs, ...) are POIs tagged
    `amenity`/`shop`/`tourism`/`office`/`leisure`. Those are NOT in the core
    tag-scoped tables -- they live in a dedicated `pois` table, loaded once from a
    POI-tag-filtered planet extract (core/dbgen/extract_pois.sh +
    build_poi_index.py). A facility query is then a fast indexed bbox SELECT.

    This REPLACED an earlier design that forked `osmium extract` against the full
    planet per request and cached per bbox: that scan took minutes and timed out
    for every station whose bbox wasn't already cached. The `pois` table answers
    for every station in milliseconds and needs no planet file on the request path.

So this module degrades honestly, exactly like api/boarding.py does for the
geo-blocked formation feed: when the `pois` table was never loaded here it returns
`found=False` with a typed `reason` (`no_poi_layer`) rather than guessing. The pure
ranking/selection (category filter + nearest-first + the routed-walk anchor) is
unit-tested offline against synthetic POIs; it lights up the day the table is loaded.
"""

import math
from typing import Callable, Dict, List, Optional, Tuple

from graph import haversine_meters

from api import schemas
from api.bridge import resolve_station
from api.transfers import STATION_UNRESOLVED

# Reasons a lookup returns no facilities (found=False). station_unresolved is
# shared with the rest of the API; the rest are facility-specific.
NO_POI_LAYER = "no_poi_layer"                # the POI source isn't available here
UNSUPPORTED_CATEGORY = "unsupported_category"  # asked for a category we don't map
NONE_MAPPED = "none_mapped"                  # layer present, but this station tags none
TOO_SPARSE = "too_sparse"                    # < 2 platforms: nothing to draw a map on
MAP_BUILD_FAILED = "map_build_failed"        # the browse export itself failed to build

# How far out (metres) around the station centroid to gather POIs. Matches
# viz_export.DEFAULT_DETAIL_RADIUS_M -- a station-footprint-sized box.
DEFAULT_RADIUS_M = 250.0
# Cap on facilities returned; a station rarely has more of one kind and the UI
# shows a ranked short list.
DEFAULT_LIMIT = 25

_M_PER_DEG_LAT = 111_320.0


# ---------------------------------------------------------------------------
# Category taxonomy: a semantic key (what the UI chips send) -> the OSM
# (category, subtypes) filters that satisfy it. `None` subtypes means "any
# subtype in that OSM category". Kept server-side so the client and server agree
# on what "toilets" means, and so an unknown key degrades to a typed reason
# rather than silently matching nothing.
# ---------------------------------------------------------------------------

_Spec = List[Tuple[str, Optional[frozenset]]]

CATEGORY_FILTERS: Dict[str, _Spec] = {
    "toilets": [("amenity", frozenset({"toilets"}))],
    "coffee": [("amenity", frozenset({"cafe", "fast_food"})), ("shop", frozenset({"coffee"}))],
    "food": [("amenity", frozenset({"restaurant", "fast_food", "food_court", "cafe"}))],
    "atm": [("amenity", frozenset({"atm", "bank", "bureau_de_change"}))],
    "tickets": [("shop", frozenset({"ticket"}))],
    "shops": [("shop", None)],
    "pharmacy": [("amenity", frozenset({"pharmacy"})), ("shop", frozenset({"chemist"}))],
    "taxi": [("amenity", frozenset({"taxi"}))],
    "info": [("tourism", frozenset({"information"}))],
    "luggage": [("amenity", frozenset({"luggage_locker", "left_luggage"}))],
}

# Friendly aliases the UI (or a caller) might send for a canonical key.
_ALIASES = {
    "toilet": "toilets", "wc": "toilets", "restroom": "toilets", "restrooms": "toilets",
    "cafe": "coffee", "coffees": "coffee",
    "restaurant": "food", "restaurants": "food", "eat": "food",
    "cash": "atm", "bank": "atm", "money": "atm",
    "ticket": "tickets",
    "shop": "shops", "store": "shops", "stores": "shops",
    "chemist": "pharmacy", "drugstore": "pharmacy",
    "taxis": "taxi", "cab": "taxi",
    "information": "info", "tourist_info": "info",
    "left_luggage": "luggage", "lockers": "luggage", "locker": "luggage",
}


def canonical_category(category: str) -> Optional[str]:
    """Normalise a requested category to a canonical key, or None if unknown."""
    key = (category or "").strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in CATEGORY_FILTERS else None


def resolve_category(category: str) -> Optional[_Spec]:
    """The OSM filter spec for a category, or None if it isn't one we map."""
    key = canonical_category(category)
    return CATEGORY_FILTERS[key] if key is not None else None


# ---------------------------------------------------------------------------
# Pure selection + ranking (no DB, no osmium) -- unit-tested offline
# ---------------------------------------------------------------------------

def poi_matches(poi: Dict, spec: _Spec) -> bool:
    """True iff `poi` (a gather_details feature: {category, subtype, ...}) is
    admitted by any (category, subtypes) clause of `spec`."""
    cat = poi.get("category")
    sub = poi.get("subtype")
    for want_cat, want_subs in spec:
        if cat == want_cat and (want_subs is None or sub in want_subs):
            return True
    return False


def rank_facilities(
    pois: List[Dict], station_lat: float, station_lon: float,
    spec: _Spec, limit: int = DEFAULT_LIMIT,
) -> List[schemas.Facility]:
    """Category-filter `pois`, measure each one's straight-line distance from the
    station centroid, and return them nearest-first as `Facility` rows (capped at
    `limit`). Pure: the distance is haversine, the input is already-gathered POIs.
    A POI without a usable coordinate is dropped (it can't be ranked)."""
    out: List[schemas.Facility] = []
    for p in pois:
        if not poi_matches(p, spec):
            continue
        lat, lon = p.get("lat"), p.get("lon")
        if lat is None or lon is None:
            continue
        dist = haversine_meters(station_lat, station_lon, lat, lon)
        out.append(schemas.Facility(
            name=p.get("name"),
            category=p.get("category"),
            subtype=p.get("subtype"),
            level=_level_str(p.get("level_raw")),
            distance_m=round(dist, 1),
            lat=lat, lon=lon,
        ))
    out.sort(key=lambda f: f.distance_m)
    return out[:limit]


def _level_str(level_raw: Optional[str]) -> Optional[str]:
    """The OSM `level` tag as a trimmed display string, or None when untagged."""
    if level_raw is None:
        return None
    s = str(level_raw).strip()
    return s or None


# ---------------------------------------------------------------------------
# Routed-walk anchor (optional) -- pure, unit-tested offline
#
# A POI isn't a node in the platform-to-platform graph, so `find_shortest_path`
# can't route straight to it. Where a `from_platform` is given we anchor each
# facility to its NEAREST platform and route platform->platform to that anchor --
# a real walk time to "the platform by the facility". Both the anchor choice and
# the enrichment are pure (the route function is injected), so they test without a
# DB and run only when platform coordinates are available.
# ---------------------------------------------------------------------------

def nearest_platform_ref(
    lat: float, lon: float, platform_coords: Dict[str, Tuple[float, float]],
) -> Optional[str]:
    """The platform ref whose centroid is closest to (lat, lon), or None if no
    platform coordinates are known."""
    best_ref, best_d = None, math.inf
    for ref, (plat, plon) in platform_coords.items():
        d = haversine_meters(lat, lon, plat, plon)
        if d < best_d:
            best_ref, best_d = ref, d
    return best_ref


# route_fn(from_ref, to_ref) -> {"found": bool, "walk_time_s": float?,
# "walk_distance_m": float?}; injected so this is DB-free and testable.
RouteFn = Callable[[str, str], Dict]


def attach_walks(
    facilities: List[schemas.Facility], from_platform: str,
    platform_coords: Dict[str, Tuple[float, float]], route_fn: RouteFn,
) -> List[schemas.Facility]:
    """Fill each facility's `nearest_platform` + routed `walk_*` from `from_platform`
    to that nearest platform, in place. A facility with no coordinate, no reachable
    platform, or an unfound route keeps its straight-line `distance_m` only."""
    if not platform_coords:
        return facilities
    for f in facilities:
        if f.lat is None or f.lon is None:
            continue
        ref = nearest_platform_ref(f.lat, f.lon, platform_coords)
        if ref is None:
            continue
        f.nearest_platform = ref
        if ref == from_platform:
            f.walk_time_s = 0.0
            f.walk_distance_m = 0.0
            continue
        r = route_fn(from_platform, ref)
        if r.get("found"):
            f.walk_time_s = r.get("walk_time_s")
            f.walk_distance_m = r.get("walk_distance_m")
    return facilities


# ---------------------------------------------------------------------------
# POI layer availability + gathering (osmium/planet) -- lazy so the pure path
# above never imports osmium, and so a host without the layer degrades cleanly.
# ---------------------------------------------------------------------------

def station_bbox(lat: float, lon: float, radius_m: float = DEFAULT_RADIUS_M):
    """(min_lon, min_lat, max_lon, max_lat) box of half-size `radius_m` around a
    point -- the lon/lat order gather_details / osmium extract expect."""
    dlat = radius_m / _M_PER_DEG_LAT
    dlon = radius_m / (_M_PER_DEG_LAT * max(math.cos(math.radians(lat)), 0.01))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def poi_layer_available(cur) -> bool:
    """Whether the facility POI layer is loaded on this host: the `pois` table
    exists and holds at least one row (built by core/dbgen/build_poi_index.py from
    a POI-tag-filtered planet extract). A bbox query is then a fast indexed SELECT
    -- no osmium fork, no planet file on the request path.

    False -> the endpoint degrades to `no_poi_layer` exactly as before; the meaning
    is now 'the `pois` table was never loaded here' rather than 'no planet extract'.
    `to_regclass` returns NULL (no error) when the table doesn't exist, so a host
    that never ran the loader degrades cleanly instead of raising."""
    cur.execute("SELECT to_regclass('public.pois') AS reg")
    if cur.fetchone()["reg"] is None:
        return False
    cur.execute("SELECT EXISTS(SELECT 1 FROM pois) AS present")
    return bool(cur.fetchone()["present"])


def gather_pois(cur, bbox_lonlat) -> List[Dict]:
    """The POIs inside a (min_lon, min_lat, max_lon, max_lat) box, read from the
    `pois` table in the gather_details feature shape rank_facilities / detail_entry
    expect. A btree lat/lon bbox scan (idx_pois_lat / idx_pois_lon) -- milliseconds,
    no osmium, no planet -- so it answers for every station, not just cached ones."""
    min_lon, min_lat, max_lon, max_lat = bbox_lonlat
    cur.execute(
        "SELECT category, subtype, name, level, lat, lon FROM pois "
        "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
        (min_lat, max_lat, min_lon, max_lon),
    )
    return [{"kind": "poi", "category": r["category"], "subtype": r["subtype"],
             "name": r["name"], "level_raw": r["level"], "lat": r["lat"], "lon": r["lon"]}
            for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Platform centroids (DB) -- the anchor set for the optional routed walk
# ---------------------------------------------------------------------------

def platform_centroids(cur, relation_id: int) -> Dict[str, Tuple[float, float]]:
    """{platform_ref: (lat, lon) centroid} for the station, resolved from the same
    footprint list_platform_refs uses. Only the base transfr_eu DB is needed (no
    POI layer), so this is exercised by the DB-gated test independently of whether
    the details layer exists."""
    from search_context import (  # bare engine imports (see api/__init__.py)
        PLATFORM_EDGE_SEARCH_RADIUS_M, _ANY_PLATFORM_SQL, _ref_tokens,
        resolve_relation_ways_and_nodes,
    )
    from graph import bbox_from_coords

    seed_way_ids, seed_node_ids = resolve_relation_ways_and_nodes(cur, relation_id)
    seed_nodes = set(seed_node_ids)
    if seed_way_ids:
        cur.execute("SELECT nodes FROM osm_ways WHERE id = ANY(%s)", (list(seed_way_ids),))
        for row in cur.fetchall():
            seed_nodes.update(row["nodes"])
    if not seed_nodes:
        return {}
    cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (list(seed_nodes),))
    coords = {r["id"]: (r["lat"], r["lon"]) for r in cur.fetchall()}
    if not coords:
        return {}
    min_lat, max_lat, min_lon, max_lon = bbox_from_coords(coords, PLATFORM_EDGE_SEARCH_RADIUS_M)
    cur.execute(
        "SELECT id FROM osm_nodes WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s",
        (min_lat, max_lat, min_lon, max_lon),
    )
    node_ids = [r["id"] for r in cur.fetchall()]
    if not node_ids:
        return {}
    cur.execute(
        "SELECT DISTINCT unnest(way_ids) AS w FROM node_way_ids WHERE node_id = ANY(%s)",
        (node_ids,),
    )
    way_ids = [r["w"] for r in cur.fetchall()]
    if not way_ids:
        return {}
    cur.execute(
        f"SELECT nodes, tags FROM osm_ways WHERE id = ANY(%s) AND {_ANY_PLATFORM_SQL}",
        (way_ids,),
    )
    plat_rows = cur.fetchall()
    # Coordinates for every node these platform ways reference (some sit outside
    # the seed set), so a way's centroid averages its own vertices.
    need = {n for row in plat_rows for n in (row["nodes"] or [])}
    missing = [n for n in need if n not in coords]
    if missing:
        cur.execute("SELECT id, lat, lon FROM osm_nodes WHERE id = ANY(%s)", (missing,))
        for r in cur.fetchall():
            coords[r["id"]] = (r["lat"], r["lon"])
    # Accumulate a centroid per ref token (a ref can appear on more than one way).
    sums: Dict[str, Tuple[float, float, int]] = {}
    for row in plat_rows:
        pts = [coords[n] for n in (row["nodes"] or []) if n in coords]
        if not pts:
            continue
        clat = sum(p[0] for p in pts) / len(pts)
        clon = sum(p[1] for p in pts) / len(pts)
        tags = row["tags"] or {}
        refs = set()
        for tag_key in ("ref", "local_ref"):
            refs.update(_ref_tokens(tags.get(tag_key)))
        for ref in refs:
            slat, slon, n = sums.get(ref, (0.0, 0.0, 0))
            sums[ref] = (slat + clat, slon + clon, n + 1)
    return {ref: (slat / n, slon / n) for ref, (slat, slon, n) in sums.items()}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_facilities(
    conn, lat: float, lon: float, category: str,
    from_platform: Optional[str] = None, limit: int = DEFAULT_LIMIT,
) -> schemas.FacilitiesResponse:
    """Resolve the station nearest (lat, lon), gather POIs of `category` from the
    details layer (or degrade with a typed reason), and rank them nearest-first
    from the station centroid. When `from_platform` is given, attach a routed walk
    to each facility's nearest platform."""
    def _resp(**kw):
        return schemas.FacilitiesResponse(lat=lat, lon=lon, category=category, **kw)

    spec = resolve_category(category)
    if spec is None:
        return _resp(found=False, reason=UNSUPPORTED_CATEGORY)

    # Each cursor is closed before the next DB op / the pathfind, mirroring
    # api/main.py:get_transfer (resolve, then route on the bare connection).
    with conn.cursor() as cur:
        match = resolve_station(cur, lat, lon)
    if match is None:
        return _resp(found=False, reason=STATION_UNRESOLVED)

    bbox = station_bbox(match.lat, match.lon)
    with conn.cursor() as cur:
        if not poi_layer_available(cur):
            # The layer was never loaded here -- honest degradation, not a guess.
            return _resp(found=False, reason=NO_POI_LAYER,
                         relation_id=match.relation_id, station=match.name)
        pois = gather_pois(cur, bbox)

    facilities = rank_facilities(pois, match.lat, match.lon, spec, limit)
    if not facilities:
        return _resp(found=False, reason=NONE_MAPPED,
                     relation_id=match.relation_id, station=match.name)

    if from_platform:
        with conn.cursor() as cur:
            coords = platform_centroids(cur, match.relation_id)
        attach_walks(facilities, from_platform, coords, _route_fn(conn, match.relation_id))

    return _resp(found=True, relation_id=match.relation_id, station=match.name,
                 facilities=facilities)


def build_facility_map(
    conn, lat: float, lon: float, category: str, limit: int = DEFAULT_LIMIT,
) -> schemas.FacilityMapResponse:
    """The whole station in 3D with every facility of `category` pinned -- the
    map-first surface. Resolves the station, gathers + ranks the category's POIs
    (or degrades with a typed reason exactly like `build_facilities`), gives each a
    cheap nearest-platform anchor (nearest centroid, NO pathfind, so the map load
    stays fast), and builds ONE browse `viz_export` (all platforms, no single route)
    with every facility attached as a focus POI. `export.details` and `facilities`
    come back in the same order, so a tapped pin maps straight back to its row."""
    def _resp(**kw):
        return schemas.FacilityMapResponse(lat=lat, lon=lon, category=category, **kw)

    spec = resolve_category(category)
    if spec is None:
        return _resp(found=False, reason=UNSUPPORTED_CATEGORY)

    with conn.cursor() as cur:
        match = resolve_station(cur, lat, lon)
    if match is None:
        return _resp(found=False, reason=STATION_UNRESOLVED)

    bbox = station_bbox(match.lat, match.lon)
    with conn.cursor() as cur:
        if not poi_layer_available(cur):
            return _resp(found=False, reason=NO_POI_LAYER,
                         relation_id=match.relation_id, station=match.name)
        pois = gather_pois(cur, bbox)

    facilities = rank_facilities(pois, match.lat, match.lon, spec, limit)
    if not facilities:
        return _resp(found=False, reason=NONE_MAPPED,
                     relation_id=match.relation_id, station=match.name)

    # Cheap nearest-platform anchor: nearest centroid only, no pathfind. Enough for
    # a tapped pin to open a walk to its platform later, without the map paying for
    # one route per facility up front.
    with conn.cursor() as cur:
        coords = platform_centroids(cur, match.relation_id)
    for f in facilities:
        if f.lat is not None and f.lon is not None and coords:
            f.nearest_platform = nearest_platform_ref(f.lat, f.lon, coords)

    # A browse export needs two distinct platforms to frame the station.
    refs = list(coords.keys())
    if len(refs) < 2:
        from search_context import list_platform_refs  # bare engine import
        with conn.cursor() as cur:
            refs = [r for r in list_platform_refs(cur, match.relation_id) if r]
    if len(refs) < 2:
        return _resp(found=False, reason=TOO_SPARSE,
                     relation_id=match.relation_id, station=match.name)

    # Attach every facility (all carry coords -- rank_facilities drops any that
    # don't) IN ORDER, so details[i] stays aligned with facilities[i].
    attach = [{"lat": f.lat, "lon": f.lon, "level_raw": f.level, "name": f.name,
               "category": f.category, "subtype": f.subtype} for f in facilities]
    from viz_export import export  # resolved via api/__init__ sys.path setup
    from api import config
    try:
        doc = export(conn, match.relation_id, refs[0], refs[-1],
                     algorithm="astar", details=False, stitch=config.STITCH_BRIDGES,
                     all_platforms=True, attach_pois=attach)
    except Exception:  # noqa: BLE001 -- a bad export must not 500 the map
        return _resp(found=False, reason=MAP_BUILD_FAILED,
                     relation_id=match.relation_id, station=match.name, facilities=facilities)

    return _resp(found=True, relation_id=match.relation_id, station=match.name,
                 export=doc, facilities=facilities)


def _route_fn(conn, relation_id: int) -> RouteFn:
    """A RouteFn backed by the real platform-to-platform pathfinder, normalising
    find_shortest_path's keys to the {found, walk_time_s, walk_distance_m} shape
    attach_walks consumes."""
    from ground_truth import find_shortest_path  # bare engine import
    from api import config

    def route(from_ref: str, to_ref: str) -> Dict:
        r = find_shortest_path(conn, relation_id, from_ref, to_ref,
                               algorithm="astar", use_stitch_bridges=config.STITCH_BRIDGES)
        return {
            "found": bool(r.get("found")),
            "walk_time_s": r.get("walking_time_seconds"),
            "walk_distance_m": r.get("walking_distance_meters"),
        }

    return route
