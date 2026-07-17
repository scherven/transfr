"""
Nearest-facility lookup for one station -- the `/facilities` endpoint's builder.

WHAT IS AND ISN'T AVAILABLE.
  * The station and its platforms are in the transfr_eu DB (railway/pedestrian
    tag scope), so resolving the station and its platform centroids is always
    possible where the DB is up.
  * The FACILITIES themselves (toilets, cafes, ATMs, ...) are POIs tagged
    `amenity`/`shop`/`tourism`/`office`/`leisure`. Those are NOT in transfr_eu --
    they come from the optional `viz_export` "details" layer, which is an offline
    osmium extract of the full planet (core/viz/viz_export.py: PLANET_PBF,
    gather_details). On a host without that planet extract (or a cached bbox of
    it) the layer is simply unavailable.

So this module degrades honestly, exactly like api/boarding.py does for the
geo-blocked formation feed: when the POI layer can't be produced here it returns
`found=False` with a typed `reason` (`no_poi_layer`) rather than guessing. The
pure ranking/selection (category filter + nearest-first + the routed-walk anchor)
is unit-tested offline against synthetic POIs; it lights up automatically the day
a planet extract exists on the host.

The layer is real -- it was generated on the server-admin host (see the committed
`ios/.../Fixtures/viz_berlin_1_16_details.json`, 191 Berlin POIs); it just isn't
producible in every environment.
"""

import hashlib
import math
import os
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


def poi_layer_available(bbox_lonlat) -> bool:
    """Whether the details POI layer can be produced for this bbox on this host:
    a cached extract for exactly this bbox already exists, or the full planet is
    present so one can be extracted. False -> the endpoint degrades to
    `no_poi_layer`. Mirrors viz_export._extract_detail_pbf's cache key so the
    check matches what a subsequent gather() would actually use."""
    from viz_export import DETAIL_CACHE_DIR, PLANET_PBF  # lazy: pulls osmium
    key = hashlib.md5(",".join(f"{v:.5f}" for v in bbox_lonlat).encode()).hexdigest()[:12]
    cached = os.path.join(DETAIL_CACHE_DIR, f"{key}.osm.pbf")
    return os.path.exists(cached) or os.path.exists(PLANET_PBF)


def gather_pois(bbox_lonlat) -> List[Dict]:
    """The POIs (not buildings) in the bbox from the details layer, in lat/lon.
    Assumes poi_layer_available(bbox) is True (else gather_details raises)."""
    from viz_export import gather_details  # lazy: pulls osmium
    return [f for f in gather_details(bbox_lonlat) if f.get("kind") == "poi"]


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
    if not poi_layer_available(bbox):
        # The layer can't be produced here -- honest degradation, not a guess.
        return _resp(found=False, reason=NO_POI_LAYER,
                     relation_id=match.relation_id, station=match.name)

    facilities = rank_facilities(gather_pois(bbox), match.lat, match.lon, spec, limit)
    if not facilities:
        return _resp(found=False, reason=NONE_MAPPED,
                     relation_id=match.relation_id, station=match.name)

    if from_platform:
        with conn.cursor() as cur:
            coords = platform_centroids(cur, match.relation_id)
        attach_walks(facilities, from_platform, coords, _route_fn(conn, match.relation_id))

    return _resp(found=True, relation_id=match.relation_id, station=match.name,
                 facilities=facilities)


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
