"""
Export one platform-to-platform path (plus the ways the search touched) to a
flat JSON geometry file that a renderer -- core/viz_render.py today, a Swift/AR
client later -- can draw without touching the database or knowing anything
about OSM.

Why a separate export step at all: resolving the path and gathering context
ways is the slow, database-bound part (cold-cache way lookups; see HANDOFF.md).
Rendering should be instant and re-runnable while you fiddle with the view, so
the two are split and this JSON is the contract between them.

The "3D" is honest about what it is. OSM nodes carry only lat/lon, so the
vertical axis is NOT real elevation -- it is the OSM `level` tag (Simple Indoor
Tagging: 0 = ground, negative = below, positive = above) multiplied by a
nominal per-floor height. A way's `level` is the source of truth; a node's
height is therefore a property of *which way you are walking*, not of the node
-- the same node legitimately sits at two heights where you change floors
(e.g. Berlin Hbf, where the path steps from a level=-1 way straight onto a
level=2 way at a shared elevator node). Connector ways with a multi-value
level (`-1;0`, `0;1;2`) are the stairs/escalators/ramps; their height is
interpolated end-to-end along the way so they slope between floors.

Usage:
    .venv/bin/python core/viz_export.py --relation 5688517 --ref1 1 --ref2 16
    .venv/bin/python core/viz_export.py --relation 5347313 --ref1 1 --ref2 4 --radius 200

Run from the repo root (the module adds core/ to sys.path itself).
"""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter
from typing import Dict, List, Optional, Tuple

import osmium

# This module now lives in core/viz/, but still imports the engine by bare name
# (algorithms/db/graph/...). Put core/ and its sibling submodule dirs on the path
# so it runs both as `python -m core.viz.viz_export` and directly.
_VIZ_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.dirname(_VIZ_DIR)
for _p in (_VIZ_DIR, _CORE_DIR, os.path.join(_CORE_DIR, "pathfinding")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from algorithms import ALGORITHMS  # noqa: E402
from db import connect  # noqa: E402
from graph import is_walkable_way, load_station_ways, way_direction  # noqa: E402
from search_context import SearchContext, list_platform_refs  # noqa: E402

DEFAULT_FLOOR_HEIGHT_M = 4.0
MAX_RADIUS_M = 350.0  # user-set ceiling for the optional context-widening load

# A connector is "walk-relevant" when the resolved path passes within this many
# metres of it -- i.e. the walk actually uses that stair/escalator/lift. Lets a
# walk view show only the vertical circulation on the route; a full station map
# shows them all.
CONNECTOR_KINDS = {"stairs", "escalator", "elevator", "ramp"}
WALK_RELEVANT_M = 3.5

# --- "details" layer: landmarks/stores/buildings around the station ----------
# The transfr_eu DB is tag-scoped to railway/pedestrian (no shops/buildings), so
# the optional details layer comes from a local osmium bbox extract of the full
# planet -- offline, same tool as extract_europe.sh, no external API. Slow once
# (~2-3 min for the 84 GB planet), then cached per bbox.
# viz_out and the planet extract live at the core/ root, not inside core/viz/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE_ROOT = os.path.dirname(_HERE)
PLANET_PBF = os.path.join(_CORE_ROOT, "..", "server-admin", "planet.pbf")
DETAIL_CACHE_DIR = os.path.join(_CORE_ROOT, "viz_out", "_detail_cache")
POI_CATEGORIES = ("shop", "amenity", "tourism", "office", "leisure")
# amenity=* is mostly street furniture; keep only things a traveller would
# recognise as a landmark/place, not benches and waste baskets.
AMENITY_NOISE = {
    "bench", "waste_basket", "bicycle_parking", "vending_machine", "recycling",
    "drinking_water", "clock", "fountain", "shelter", "telephone", "post_box",
    "parking", "parking_space", "parking_entrance", "motorcycle_parking",
    "bicycle_repair_station", "grit_bin", "bbq", "smoking_area", "charging_station",
}
DEFAULT_DETAIL_RADIUS_M = 250.0
MAX_DETAIL_RADIUS_M = 400.0


def _keep_poi(category: str, subtype: Optional[str]) -> bool:
    return not (category == "amenity" and subtype in AMENITY_NOISE)


# ---------------------------------------------------------------------------
# level tag -> vertical position
# ---------------------------------------------------------------------------

def parse_levels(raw: Optional[str]) -> List[float]:
    """OSM `level` string -> ordered list of floor numbers.

    Handles the forms that actually occur in the data (verified against
    transfr_eu): single "1"; semicolon list "-1;0", "-2;-1;0"; dash range
    "-3-0"; half-levels "0.5"/"-0.5"; and untagged (-> [0.0], the ground-level
    default that the ~45% of un-levelled outdoor approach ways rely on).
    A single returned value means "flat at that level"; two or more means
    "connector", interpolated between the first and last.
    """
    if raw is None or raw == "":
        return [0.0]
    raw = raw.strip()
    try:
        if ";" in raw:
            return [float(p) for p in raw.split(";") if p != ""]
        # dash range like "-3-0": a '-' that is not the leading sign.
        inner = raw.find("-", 1)
        if inner != -1:
            return [float(raw[:inner]), float(raw[inner + 1:])]
        return [float(raw)]
    except ValueError:
        return [0.0]  # anything unparseable falls back to ground, never crashes


def way_kind(tags: Dict[str, str], levels: List[float]) -> str:
    """Semantic class driving the renderer's colour/legend. Vertical
    circulation is split (stairs / escalator / elevator / ramp) because those
    are exactly the elements a transfer visualisation needs to distinguish --
    which one you take is the whole decision at a level change."""
    if tags.get("conveying") in ("yes", "forward", "backward"):
        return "escalator"
    highway, railway = tags.get("highway"), tags.get("railway")
    if highway == "elevator" or railway == "elevator":
        return "elevator"
    if highway == "steps":
        return "stairs"
    if railway in ("platform", "platform_edge"):
        return "platform"
    if highway in ("footway", "corridor", "pedestrian"):
        # a walkway that spans levels is a ramp; one that stays on its level
        # is just floor.
        return "ramp" if len(levels) > 1 else "walkway"
    return highway or railway or "other"


def is_connector(tags: Dict[str, str], levels: List[float]) -> bool:
    return (
        len(levels) > 1
        or tags.get("highway") in ("steps", "elevator")
        or tags.get("railway") == "elevator"
        or tags.get("conveying") in ("yes", "forward", "backward")
    )


def is_area_way(tags: Dict[str, str], node_ids: List[int]) -> bool:
    """True iff a way encloses an AREA (a polygon) rather than tracing a line --
    a footway plaza, an indoor room, a building part. Its node ring has no
    'first floor -> last floor' direction, so interpolating a height along its
    perimeter is meaningless: for a multi-level area it fabricates a sloping
    ramp and a train of phantom up/down transitions (Karlsruhe Hbf's
    room=elevator / building:part=elevator polygon 270880470, level=0;1, whose
    13 boundary nodes interpolated end-to-end produced a non-monotonic z that
    read as six spurious 'other' transitions). Detected from an explicit
    area/indoor/building tag or a closed node ring; such an area is rendered
    flat at a single representative level instead (see way_node_heights)."""
    if tags.get("area") == "yes":
        return True
    if tags.get("indoor") in ("room", "area"):
        return True
    if "building" in tags or "building:part" in tags:
        return True
    return len(node_ids) >= 4 and node_ids[0] == node_ids[-1]


def node_kind(tags: Dict[str, str]) -> str:
    """Vertical-circulation class of a *node* the path passes through. Real
    stations frequently map an elevator as a single `highway=elevator` node
    shared between the footways of the floors it serves (e.g. Berlin Hbf's OTIS
    node 742238019, `level=-2;-1;0;1;2`), rather than as a way -- so a path can
    change floors 'at a point'. Classifying that node is what lets the render
    show *how* you go up, instead of a bare vertical jump. `vertical` = a level
    change at a shared node with nothing on it saying which mechanism it is."""
    if tags.get("highway") == "elevator" or tags.get("railway") == "elevator":
        return "elevator"
    if tags.get("conveying") in ("yes", "forward", "backward"):
        return "escalator"
    if tags.get("highway") == "steps":
        return "stairs"
    return "vertical"


# ---------------------------------------------------------------------------
# platform labelling: ref (from tags) + the floor it sits on (from the graph)
# ---------------------------------------------------------------------------

def platform_ref(tags: Dict[str, str]) -> Optional[str]:
    """The human platform number for a platform way: `ref`, else `local_ref`,
    else the composite `railway:track_ref`. None for a bare platform area that
    carries no ref (rare)."""
    return tags.get("ref") or tags.get("local_ref") or tags.get("railway:track_ref")


def platform_level_from_graph(cur, node_ids: List[int]) -> Optional[float]:
    """The floor a platform sits on, from the leveled WALKABLE ways that share a
    node with it -- its real graph connection, not 2D proximity (which is
    meaningless in a multi-level station, where a deep platform and a viaduct
    overlap in plan). Prefer flat single-level ways (the floor under the
    platform); fall back to a connector's endpoint levels only when nothing flat
    touches it. Returns None when no leveled way connects -- an honest 'unknown'
    beats a confident wrong guess. OSM node ids are globally unique, so sharing a
    node already localises the match to this platform (no station bound needed)."""
    if not node_ids:
        return None
    cur.execute(
        "SELECT tags->>'level' AS lvl FROM osm_ways "
        "WHERE tags ? 'level' AND nodes && %s::bigint[] "
        "AND tags->>'railway' IS DISTINCT FROM 'platform' "
        "AND tags->>'railway' IS DISTINCT FROM 'platform_edge'",
        (list(node_ids),),
    )
    flat, multi = [], []
    for row in cur.fetchall():
        levels = parse_levels(row["lvl"])
        (flat if len(levels) == 1 else multi).append(levels)
    if flat:
        return Counter(l[0] for l in flat).most_common(1)[0][0]
    if multi:  # only connectors touch it -- vote over their endpoint floors
        return Counter(v for l in multi for v in (l[0], l[-1])).most_common(1)[0][0]
    return None


def _seg_dist2(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Squared distance from point (px,py) to segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2


# ---------------------------------------------------------------------------
# projection: WGS-84 lat/lon -> local ENU metres about a station origin
# ---------------------------------------------------------------------------

class Projector:
    def __init__(self, lat0: float, lon0: float, floor_height_m: float):
        self.lat0, self.lon0 = lat0, lon0
        self.m_per_deg_lat = 111_320.0
        self.m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
        self.floor_height_m = floor_height_m

    def xy(self, lat: float, lon: float) -> Tuple[float, float]:
        return ((lon - self.lon0) * self.m_per_deg_lon,
                (lat - self.lat0) * self.m_per_deg_lat)

    def z(self, level: float) -> float:
        return level * self.floor_height_m


def way_node_heights(
    node_ids: List[int], coords: Dict[int, Tuple[float, float]],
    levels: List[float], proj: Projector,
    node_levels: Optional[Dict[int, float]] = None,
    is_area: bool = False,
) -> Dict[int, float]:
    """Per-node Z (metres) for one way. Flat if single-level; otherwise
    interpolated first->last along cumulative horizontal distance, so an
    uneven-spaced staircase still slopes evenly between its two floors.

    A ';'-joined `level` tag (e.g. '1;2') is a SET of the levels the way spans,
    NOT a sequence guaranteed to run in the way's node order -- OSM mappers
    routinely tag '1;2' on a way whose nodes go high->low. Taken literally that
    flips the endpoints' heights, so a connector renders sloping the wrong way
    (the "N" a step-free Berlin Hbf 1->16 showed: escalator 269400497 is tagged
    level=1;2 but its nodes run L2->L1). When the endpoint nodes carry their own
    single `level` tag (node_levels), we orient the interpolation to THOSE --
    the reliable per-node signal -- so the slope always matches the floors the
    way actually connects.

    is_area (see is_area_way) suppresses the interpolation entirely: a polygon's
    boundary has no along-the-slope order, so a multi-level area is rendered flat
    at its lowest level rather than fake-sloped around its perimeter -- the level
    change then surfaces once, at the door where the path enters/leaves it,
    instead of as a burst of phantom transitions."""
    present = [(n, coords[n]) for n in node_ids if n in coords]
    if not present:
        return {}
    multi_level_area = is_area and len(set(levels)) > 1
    if len(levels) == 1 or len(present) == 1 or multi_level_area:
        z = proj.z(min(levels) if multi_level_area else levels[0])
        return {n: z for n, _ in present}

    xy = [proj.xy(lat, lon) for _, (lat, lon) in present]
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    total = cum[-1] or 1.0
    # Endpoint levels default to the way's level-list order, but a node's own
    # `level` tag overrides it -- so a level list ordered opposite to the node
    # order (level=1;2 on nodes running L2->L1) no longer reverses the slope.
    l0, l1 = levels[0], levels[-1]
    if node_levels:
        l0 = node_levels.get(present[0][0], l0)
        l1 = node_levels.get(present[-1][0], l1)
    z0, z1 = proj.z(l0), proj.z(l1)
    return {present[i][0]: z0 + (z1 - z0) * (cum[i] / total) for i in range(len(present))}


# ---------------------------------------------------------------------------
# path hop -> way reconstruction (build_result only hands back a *deduped*
# way list; for per-hop heights we need the specific way each hop walked)
# ---------------------------------------------------------------------------

def _hop_way_rank(tags: Dict[str, str]) -> Tuple[int, int]:
    """Preference among ways that both place a hop's two nodes adjacent. A hop
    takes its height from the way way_for_hop returns, so when a real,
    level-tagged path overlaps a tag-less geometry stub tracing the very same
    nodes -- Stuttgart Hbf's level=1 concourse AREA (way 230821002) vs. the
    empty-tagged 3-node ways laid over stretches of its boundary -- the tagged
    one must win. Otherwise the stub, whose absent `level` defaults to ground
    (parse_levels(None) -> [0.0]), pins those hops to z=0 and the path yo-yos
    between floors, firing a phantom 'vertical' transition at every node where
    the chosen way flips. Ordered: an explicit `level` tag first, then any
    highway/railway tag (a mapped path over bare geometry). Higher wins."""
    has_level = 1 if tags.get("level") not in (None, "") else 0
    has_path = 1 if (tags.get("highway") or tags.get("railway")) else 0
    return (has_level, has_path)


def way_for_hop(a: int, b: int, way_cache, node_to_ways) -> Optional[int]:
    """The way whose node order places a and b adjacent (in a legal direction).
    Used to pick which way's height model a path vertex takes. When several ways
    qualify -- overlapping/duplicate geometry is common indoors -- the one with
    the most reliable level information wins (see _hop_way_rank) rather than
    whichever the set happens to yield first."""
    best: Optional[int] = None
    best_rank: Optional[Tuple[int, int]] = None
    for wid in node_to_ways.get(a, ()):
        info = way_cache[wid]
        nodes = info["nodes"]
        direction = way_direction(info["tags"])
        adjacent = False
        for i, n in enumerate(nodes):
            if n != a:
                continue
            if i + 1 < len(nodes) and nodes[i + 1] == b and direction >= 0:
                adjacent = True
                break
            if i - 1 >= 0 and nodes[i - 1] == b and direction <= 0:
                adjacent = True
                break
        if not adjacent:
            continue
        rank = _hop_way_rank(info["tags"] or {})
        if best_rank is None or rank > best_rank:
            best, best_rank = wid, rank
    return best


# ---------------------------------------------------------------------------
# details layer (landmarks / stores / buildings), from a local planet extract
# ---------------------------------------------------------------------------

def _extract_detail_pbf(bbox_lonlat: Tuple[float, float, float, float]) -> str:
    """osmium-extract the bbox from the full planet once, cache by bbox. Writes
    to a temp file and renames on success so an interrupted extract never leaves
    a corrupt file cached (per the repo's long-process-safety convention)."""
    key = hashlib.md5(",".join(f"{v:.5f}" for v in bbox_lonlat).encode()).hexdigest()[:12]
    out = os.path.join(DETAIL_CACHE_DIR, f"{key}.osm.pbf")
    if os.path.exists(out):
        return out
    if not os.path.exists(PLANET_PBF):
        raise SystemExit(f"details need the full planet at {PLANET_PBF} (not found)")
    os.makedirs(DETAIL_CACHE_DIR, exist_ok=True)
    tmp = out + ".partial"
    print(f"  extracting local detail from planet (one-time, ~2-3 min)...", file=sys.stderr)
    try:
        subprocess.run(
            ["osmium", "extract", "--bbox", ",".join(f"{v:.6f}" for v in bbox_lonlat),
             PLANET_PBF, "-o", tmp, "-f", "pbf", "--overwrite"],  # -f: temp name has no .pbf ext
            check=True,
        )
    except (KeyboardInterrupt, subprocess.CalledProcessError):
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    os.replace(tmp, out)
    return out


def detail_entry(proj: "Projector", feat: Dict, path_xy: List[Tuple[float, float]]) -> Dict:
    """Project one gathered feature (a building or a POI) into a `details` entry:
    its geometry in local-ENU metres plus its distance from the walked path.

    Pure -- no DB, no osmium -- so the planet-gathered `details` layer and an
    explicitly-attached facility POI (the 'walk to nearest' focus) share exactly
    one projection, and it is unit-testable offline. A feature carrying a truthy
    `focus` flag emits `"focus": true`, so the renderer can tell the one facility
    the user chose from the surrounding context."""
    z = proj.z(parse_levels(feat.get("level_raw"))[0])
    if feat["kind"] == "building":
        pts = [[*proj.xy(la, lo), z] for la, lo in feat["outline"]]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        geom = {"points": [[round(v, 2) for v in p] for p in pts]}
    else:  # poi: a point, plus its real footprint when mapped as an area
        x, y = proj.xy(feat["lat"], feat["lon"])
        cx, cy = x, y
        geom = {"xyz": [round(x, 2), round(y, 2), round(z, 2)]}
        if feat.get("outline"):
            opts = [[*proj.xy(la, lo), z] for la, lo in feat["outline"]]
            geom["outline"] = [[round(v, 2) for v in p] for p in opts]
    dist = min(math.hypot(cx - px, cy - py) for px, py in path_xy)
    entry = {
        "kind": feat["kind"], "category": feat["category"], "subtype": feat.get("subtype"),
        "name": feat.get("name"), "dist": round(dist, 1), **geom,
    }
    if feat.get("focus"):
        entry["focus"] = True
    return entry


def gather_details(bbox_lonlat: Tuple[float, float, float, float]) -> List[Dict]:
    """Buildings + POIs (shops/amenities/tourism/offices/leisure) in the bbox,
    in lat/lon, from the local planet extract. Buildings and way-POIs carry
    their outline/centroid via node locations; relation (multipolygon) buildings
    are skipped for now (rare in a station footprint)."""
    pbf = _extract_detail_pbf(bbox_lonlat)
    feats: List[Dict] = []

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            cat = next((c for c in POI_CATEGORIES if c in n.tags), None)
            if cat and _keep_poi(cat, n.tags.get(cat)) and n.location.valid():
                feats.append({
                    "id": n.id, "kind": "poi", "category": cat, "subtype": n.tags.get(cat),
                    "name": n.tags.get("name"), "level_raw": n.tags.get("level"),
                    "lat": n.location.lat, "lon": n.location.lon,
                })

        def way(self, w):
            tags = w.tags
            pts = [(nd.location.lat, nd.location.lon) for nd in w.nodes if nd.location.valid()]
            if len(pts) < 2:
                return
            if "building" in tags:
                feats.append({
                    "id": w.id, "kind": "building", "category": "building",
                    "subtype": tags.get("building"), "name": tags.get("name"),
                    "level_raw": tags.get("level"), "outline": pts,
                })
                return
            cat = next((c for c in POI_CATEGORIES if c in tags), None)
            if cat and _keep_poi(cat, tags.get(cat)):  # a POI mapped as an area
                clat = sum(p[0] for p in pts) / len(pts)
                clon = sum(p[1] for p in pts) / len(pts)
                feats.append({
                    "id": w.id, "kind": "poi", "category": cat, "subtype": tags.get(cat),
                    "name": tags.get("name"), "level_raw": tags.get("level"),
                    "lat": clat, "lon": clon, "outline": pts,  # keep the real footprint
                })

    Handler().apply_file(pbf, locations=True)
    return feats


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def _station_name(cur, relation_id: int) -> Optional[str]:
    cur.execute("SELECT tags->>'name' AS name FROM osm_relations WHERE id = %s", (relation_id,))
    row = cur.fetchone()
    return row["name"] if row else None


def export(
    conn, relation_id: int, ref_1: str, ref_2: str,
    algorithm: str = "astar", radius_m: float = 0.0,
    floor_height_m: float = DEFAULT_FLOOR_HEIGHT_M,
    details: bool = False, detail_radius_m: float = DEFAULT_DETAIL_RADIUS_M,
    stitch: bool = False, avoid_elevators: bool = False,
    all_platforms: bool = False,
    from_coord: Optional[Tuple[float, float]] = None,
    to_coord: Optional[Tuple[float, float]] = None,
    attach_pois: Optional[List[Dict]] = None,
) -> Dict:
    """Resolve the path, gather context ways, project to metres, return the
    renderer JSON as a dict. `all_platforms` (station-map / browse mode) also
    pulls in every platform at the station, not just the ones the walk touched.

    from_coord / to_coord are the platforms' real (lat, lon), used only as the
    last-resort anchor when a ref resolves to no OSM platform here (a feed whose
    label OSM doesn't carry -- see SearchContext Tier 3). Passing them keeps the
    drawn geometry in step with the verdict, which resolves the same way; omitting
    them (the default) is byte-for-byte unchanged.

    `attach_pois` is the 'walk to nearest' seam: a list of already-known POIs
    (each `{lat, lon, category, subtype?, name?, level_raw?}` -- their coordinates
    came from `/facilities`) projected straight into the `details` layer and
    flagged `focus`, so the specific facility the user tapped is drawn in the 3D
    model. Unlike the `details=True` layer it needs NO planet extract, so it works
    on any host; and it's always kept (never radius-clipped), because the user
    chose it."""
    with conn.cursor() as cur:
        name = _station_name(cur, relation_id)
        ctx = SearchContext(cur, relation_id, ref_1, ref_2, use_stitch_bridges=stitch,
                            avoid_elevators=avoid_elevators,
                            from_coord=from_coord, to_coord=to_coord)
        result = ctx.error if ctx.error is not None else ALGORITHMS[algorithm](ctx)
        # Browse mode: add EVERY platform at the station (resolved by ref -- the
        # same indexed lookup the search uses for its endpoints, so it's fast),
        # not just the ones on the walked corridor. The whole-station radius
        # closure would also do this but is far too slow for a live request.
        if all_platforms:
            for ref in list_platform_refs(cur, relation_id):
                for way_id, nodes, tags in ctx._find_platform_edges_near(ref):
                    if way_id not in ctx.way_cache:
                        ctx.way_cache[way_id] = {"nodes": nodes, "tags": tags}
        # Combined geometry pool: everything the search touched, in lat/lon.
        ways: Dict[int, Dict] = {wid: dict(info) for wid, info in ctx.way_cache.items()}
        coords: Dict[int, Tuple[float, float]] = dict(ctx.coord_cache)
        context_mode = "touched"

        # Optional widening: add the bounded walkable closure so corridors and
        # stairs that branch off the route are visible too. Capped at 350m.
        if radius_m and radius_m > 0:
            r = min(radius_m, MAX_RADIUS_M)
            closure_ways, closure_coords = load_station_ways(conn, relation_id, search_radius_m=r)
            for wid, info in closure_ways.items():
                if is_walkable_way(info["tags"]):
                    ways.setdefault(wid, {"nodes": list(info["nodes"]), "tags": info["tags"]})
            for n, ll in closure_coords.items():
                coords.setdefault(n, ll)
            context_mode = f"touched+radius:{int(r)}"

        # Tags of the nodes the path itself visits -- needed to classify the
        # vertical circulation (elevator/stairs) that OSM maps on a node rather
        # than a way, which the path changes floors through.
        path_node_tags: Dict[int, Dict[str, str]] = {}
        if result.get("found"):
            cur.execute("SELECT id, tags FROM osm_nodes WHERE id = ANY(%s)", (result["node_path"],))
            path_node_tags = {row["id"]: row["tags"] or {} for row in cur.fetchall()}

        # Orientation hint for rendering multi-level connectors: each endpoint's
        # own single `level` tag. A way tagged level='1;2' spans two floors but
        # its nodes may run high->low, so way_node_heights uses these per-node
        # levels to orient the slope correctly (see its docstring). Only the
        # endpoints of multi-level ways can be mis-oriented, so that's all we ask.
        endpoint_ids: set = set()
        for info in ways.values():
            if len(parse_levels((info["tags"] or {}).get("level"))) > 1:
                present = [n for n in info["nodes"] if n in coords]
                if present:
                    endpoint_ids.update((present[0], present[-1]))
        node_levels: Dict[int, float] = {}
        if endpoint_ids:
            cur.execute("SELECT id, tags->>'level' AS lvl FROM osm_nodes WHERE id = ANY(%s)", (list(endpoint_ids),))
            for row in cur.fetchall():
                # Only a node that ACTUALLY carries a single `level` tag is a
                # reliable per-node orientation signal. An untagged node has no
                # level of its own: parse_levels(None) returns [0.0], the ground
                # default meant for whole (outdoor) ways, NOT a claim that this
                # node sits at L0. Trusting it here pins an untagged connector
                # endpoint to the ground plane and makes the interpolation yo-yo
                # to L0 -- e.g. Berlin Hbf 2->4, whose -1/-1.3/-1.7/-2 stepped
                # staircase rendered spurious "vertical" jumps up to L0 and back.
                # A node explicitly tagged level=0 (lvl == "0") is truthy and kept.
                if not row["lvl"]:
                    continue
                lv = parse_levels(row["lvl"])
                if len(lv) == 1:
                    node_levels[row["id"]] = lv[0]

    if not coords:
        raise SystemExit("no coordinates resolved -- is relation/ref correct?")

    # Origin = centroid of nodes actually in the emitted ways, NOT of the raw
    # coord cache: SearchContext resolves platform edges by ref across all of
    # Europe and caches coords for every same-ref candidate before filtering
    # by proximity, so ctx.coord_cache holds far-away platform nodes that would
    # otherwise drag the centroid hundreds of km off-station.
    local = [coords[n] for info in ways.values() for n in info["nodes"] if n in coords]
    lat0 = sum(la for la, _ in local) / len(local)
    lon0 = sum(lo for _, lo in local) / len(local)
    proj = Projector(lat0, lon0, floor_height_m)

    # Per-way node heights, reused for both context geometry and path vertices.
    way_heights: Dict[int, Dict[int, float]] = {}
    levels_seen = set()
    ways_json = []
    for wid, info in ways.items():
        tags = info["tags"] or {}
        levels = parse_levels(tags.get("level"))
        levels_seen.update(levels)
        heights = way_node_heights(info["nodes"], coords, levels, proj, node_levels,
                                   is_area=is_area_way(tags, info["nodes"]))
        way_heights[wid] = heights
        pts = [[*proj.xy(*coords[n]), heights[n]] for n in info["nodes"] if n in coords]
        if len(pts) < 2:
            continue
        ways_json.append({
            "id": wid,
            "kind": way_kind(tags, levels),
            "is_connector": is_connector(tags, levels),
            "level_raw": tags.get("level"),
            "points": [[round(v, 2) for v in p] for p in pts],
        })

    # Path geometry: one segment per hop, each vertex taking the height of the
    # way that hop walks -- so a floor change shows as a real vertical/sloped
    # move rather than being flattened onto a single per-node height.
    path_json = {"found": bool(result.get("found"))}
    if result.get("found"):
        node_path = result["node_path"]

        def hop_way(a, b):
            return way_for_hop(a, b, ctx.way_cache, ctx.node_to_ways)

        def best_z(node):
            """The node's height from any real way it belongs to -- used when a
            hop has no way of its own (a stitch bridge) so the node still sits on
            its true level instead of being flattened to the ground plane."""
            for wid in ctx.node_to_ways.get(node, ()):
                heights = way_heights.get(wid)
                if heights and node in heights:
                    return heights[node]
            return proj.z(0.0)

        def node_z(node, wid):
            if wid is not None and node in way_heights.get(wid, {}):
                return way_heights[wid][node]
            return best_z(node)

        pts = []
        transitions = []
        # A "stitch" hop walks a synthetic bridge (core/build_stitch_bridges.py):
        # it has NO OSM way of its own (way None) between two distinct nodes. That
        # is the one segment of the route we INFERRED rather than read off a mapped
        # footpath, so it's exported as its own category for the renderer to flag.
        stitch_segments = []
        prev_node = prev_z = None
        for a, b in zip(node_path, node_path[1:]):
            wid = hop_way(a, b)
            xa, ya = proj.xy(*coords[a])
            xb, yb = proj.xy(*coords[b])
            za, zb = node_z(a, wid), node_z(b, wid)
            pa = [round(xa, 2), round(ya, 2), round(za, 2)]
            pb = [round(xb, 2), round(yb, 2), round(zb, 2)]

            # Between-hop level change: node a is the same node the previous hop
            # ended on, but at a different height -- a vertical link mapped ON
            # the node (e.g. an elevator node). Classify it from the node's tags.
            if prev_node == a and abs(prev_z - za) > 0.01:
                transitions.append({
                    "kind": node_kind(path_node_tags.get(a, {})),
                    "node_id": a,
                    "from": [pa[0], pa[1], round(prev_z, 2)],
                    "to": pa,
                })
            pts.append(pa)
            if wid is None and a != b:
                stitch_segments.append({
                    "from": pa, "to": pb,
                    "length_m": round(math.hypot(xa - xb, ya - yb), 1),
                })
            # Within-hop level change: the hop walks a connector *way* that
            # slopes between floors (stairs/escalator/ramp). Classify from it.
            elif abs(za - zb) > 0.01:
                wtags = ways.get(wid, {}).get("tags", {}) or {}
                transitions.append({
                    "kind": way_kind(wtags, parse_levels(wtags.get("level"))),
                    "way_id": wid,
                    "from": pa,
                    "to": pb,
                })
            pts.append(pb)
            prev_node, prev_z = b, zb

        path_json.update({
            "node_ids": node_path,
            "way_ids": result["way_path"],
            "points": pts,
            "transitions": transitions,
            "stitch_segments": stitch_segments,
            "walking_time_seconds": result["walking_time_seconds"],
            "walking_distance_meters": result["walking_distance_meters"],
            "endpoints": {
                "start": {"ref": ref_1, "xyz": pts[0]},
                "end": {"ref": ref_2, "xyz": pts[-1]},
            },
        })
    else:
        path_json["reason"] = result.get("reason")

    # Platform labels: every platform way gets its `ref` (from tags) and the
    # `level` it sits on, then its geometry is lifted onto that floor. OSM
    # platform_edge ways carry a ref but rarely a `level`, and are flattened to
    # z=0 without this. Level priority: the way's own single `level` tag (exact),
    # then the walk's two endpoints (exact -- the search already resolved which
    # floor they board from), then the shared-walkway vote (correct where the
    # station is well mapped), else null (honest unknown, left un-lifted).
    endpoint_levels: Dict[str, float] = {}
    if path_json.get("found") and path_json.get("points"):
        fh = floor_height_m or 1.0
        endpoint_levels[str(ref_1)] = round(path_json["points"][0][2] / fh)
        endpoint_levels[str(ref_2)] = round(path_json["points"][-1][2] / fh)
    with conn.cursor() as lvl_cur:
        for w in ways_json:
            if w["kind"] != "platform":
                continue
            tags = (ways.get(w["id"], {}).get("tags")) or {}
            ref = platform_ref(tags)
            w["ref"] = ref
            own = parse_levels(tags["level"]) if tags.get("level") else None
            if own and len(own) == 1:
                level: Optional[float] = own[0]
            elif ref is not None and ref in endpoint_levels:
                level = endpoint_levels[ref]
            else:
                level = platform_level_from_graph(lvl_cur, ways.get(w["id"], {}).get("nodes", []))
            if level is None:
                w["level"] = None
                continue
            lvl_i = int(round(level))
            w["level"] = lvl_i
            z = round(proj.z(lvl_i), 2)
            w["points"] = [[p[0], p[1], z] for p in w["points"]]
            levels_seen.add(float(lvl_i))

    # Connector walk-relevance: flag each stairs/escalator/elevator/ramp the walk
    # actually passes through (a path point within WALK_RELEVANT_M of its line).
    # A walk view can then show only the vertical circulation on the route; a full
    # station map ignores the flag and shows them all. Only set when a path exists.
    if path_json.get("found") and path_json.get("points"):
        ppts = [(p[0], p[1]) for p in path_json["points"]]
        thr2 = WALK_RELEVANT_M ** 2
        for w in ways_json:
            if w["kind"] not in CONNECTOR_KINDS:
                continue
            pts = w["points"]
            w["walk_relevant"] = any(
                _seg_dist2(px, py, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) <= thr2
                for i in range(len(pts) - 1) for (px, py) in ppts
            )

    # Horizontal extent of everything drawn -- the renderer sizes the level
    # reference planes to it, and an AR client can use it as the anchor footprint.
    all_x = [p[0] for w in ways_json for p in w["points"]]
    all_y = [p[1] for w in ways_json for p in w["points"]]
    bbox = {"min_x": min(all_x), "max_x": max(all_x),
            "min_y": min(all_y), "max_y": max(all_y)} if all_x else None

    # Optional details layer: buildings + POIs (landmarks/stores) around the
    # station, each tagged with its distance from the path so the renderer's
    # slider can reveal them progressively. Placed at ground unless level-tagged.
    #
    # Two sources feed it: the `details=True` planet-extract sweep (radius-clipped
    # context), and `attach_pois` -- specific facilities the caller already located
    # (the 'walk to nearest' focus), projected the same way but always kept and
    # flagged `focus`. Both share `detail_entry`.
    details_json = []
    if details or attach_pois:
        r = min(detail_radius_m, MAX_DETAIL_RADIUS_M)
        path_xy = [(p[0], p[1]) for p in path_json.get("points", [])] or [(0.0, 0.0)]
        if details:
            lats = [la for la, _ in local]
            lons = [lo for _, lo in local]
            dlat = r / 111_320.0
            dlon = r / (111_320.0 * max(0.1, math.cos(math.radians(lat0))))
            gathered = [detail_entry(proj, f, path_xy)
                        for f in gather_details((min(lons) - dlon, min(lats) - dlat,
                                                 max(lons) + dlon, max(lats) + dlat))]
            # The gathered context layer is revealed nearest-first by the slider.
            details_json.extend(sorted((e for e in gathered if e["dist"] <= r),
                                       key=lambda d: d["dist"]))
        # Attached facilities keep their INPUT order (never sorted): the client
        # renders them as pins and maps a tapped pin back to the facility at the
        # same index, so `details[i]` must stay aligned with `attach_pois[i]`.
        for poi in (attach_pois or []):
            details_json.append(detail_entry(proj, {**poi, "kind": "poi", "focus": True}, path_xy))
            levels_seen.add(parse_levels(poi.get("level_raw"))[0])

    return {
        "meta": {
            "relation_id": relation_id,
            "station_name": name,
            "ref_1": ref_1,
            "ref_2": ref_2,
            "algorithm": algorithm,
            "context_mode": context_mode,
            "stitched": stitch,
            "n_stitches": len(path_json.get("stitch_segments", [])),
            "floor_height_m": floor_height_m,
            "z_is_level_not_elevation": True,
            "origin_lat": lat0,
            "origin_lon": lon0,
            "levels_present": sorted(levels_seen),
            "bbox": bbox,
            "n_context_ways": len(ways_json),
            "has_details": bool(details_json),
            "detail_radius_m": min(detail_radius_m, MAX_DETAIL_RADIUS_M) if details else 0,
            "n_details": len(details_json),
        },
        "ways": ways_json,
        "path": path_json,
        "details": details_json,
    }


def main():
    ap = argparse.ArgumentParser(description="Export a station transfer path to viz JSON.")
    ap.add_argument("--relation", type=int, required=True, help="stop_area relation id")
    ap.add_argument("--ref1", required=True, help="from platform ref")
    ap.add_argument("--ref2", required=True, help="to platform ref")
    ap.add_argument("--algorithm", default="astar", choices=sorted(ALGORITHMS))
    ap.add_argument("--radius", type=float, default=0.0,
                    help=f"also include walkable closure within this many metres (<= {int(MAX_RADIUS_M)}); 0 = touched ways only")
    ap.add_argument("--floor-height", type=float, default=DEFAULT_FLOOR_HEIGHT_M)
    ap.add_argument("--details", action="store_true",
                    help="also gather nearby buildings + POIs (landmarks/stores) from the local "
                         "planet extract, for the render's detail slider (slow once, then cached)")
    ap.add_argument("--stitch", action="store_true",
                    help="enable synthetic stitch bridges (core/build_stitch_bridges.py): join a "
                         "connector that ends inside a platform polygon without a shared node")
    ap.add_argument("--no-elevators", dest="avoid_elevators", action="store_true",
                    help="step-free routing: never use an elevator (way- or node-mapped); "
                         "route over stairs/escalators/ramps only")
    ap.add_argument("--detail-radius", type=float, default=DEFAULT_DETAIL_RADIUS_M,
                    help=f"how far out (metres) to gather details (<= {int(MAX_DETAIL_RADIUS_M)})")
    ap.add_argument("--out", default=None, help="output json path (default core/viz_out/<rel>_<ref1>_<ref2>.json)")
    args = ap.parse_args()

    out = args.out or os.path.join(
        _CORE_ROOT, "viz_out",
        f"{args.relation}_{args.ref1}_{args.ref2}.json",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)

    conn = connect()
    try:
        data = export(conn, args.relation, args.ref1, args.ref2,
                      algorithm=args.algorithm, radius_m=args.radius,
                      floor_height_m=args.floor_height,
                      details=args.details, detail_radius_m=args.detail_radius,
                      stitch=args.stitch, avoid_elevators=args.avoid_elevators)
    except KeyboardInterrupt:
        print("\ninterrupted before export completed; nothing written.", file=sys.stderr)
        return
    finally:
        conn.close()

    with open(out, "w") as f:
        json.dump(data, f)

    m, p = data["meta"], data["path"]
    if p["found"]:
        print(f"{m['station_name']} {m['ref_1']}->{m['ref_2']}: "
              f"{p['walking_time_seconds']}s / {p['walking_distance_meters']}m, "
              f"{len(p['node_ids'])} nodes, levels {m['levels_present']}")
    else:
        print(f"{m['station_name']} {m['ref_1']}->{m['ref_2']}: NOT FOUND ({p['reason']})")
    detail_note = f", {m['n_details']} details within {int(m['detail_radius_m'])}m" if m.get("has_details") else ""
    print(f"context: {m['n_context_ways']} ways ({m['context_mode']}){detail_note} -> {out}")


if __name__ == "__main__":
    main()
