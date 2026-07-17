#!/usr/bin/env python3
"""Build the vendored Europe map outline (`design/europe-geo.json`) from Natural Earth.

The route map (`design/route-maps.html`, `ios/…/Map/RouteMap.swift`) used to draw a
hand-typed silhouette of *Germany only*, so any route outside it — Paris, Wien,
Milano — projected off-canvas onto a map of the wrong country (#18). This turns a
Natural Earth `admin_0_countries` GeoJSON into a small vendored asset covering the
whole area `stations.csv` can autocomplete.

    ./scripts/build_map_geo.py --check          # verify the committed asset is current
    ./scripts/build_map_geo.py                  # download to a cache and rebuild
    ./scripts/build_map_geo.py --source ne.geojson --out design/europe-geo.json

Why the topology step (the interesting part)
--------------------------------------------
Douglas-Peucker is a *path-global* operation: run it on France's ring and on
Germany's ring independently and the shared border simplifies two different ways,
opening slivers of sea between two countries that are supposed to touch. Natural
Earth stores borders as *exactly shared vertices* (Germany ∩ Austria = 91 identical
vertices at 50m), so this script instead:

  1. cuts every ring into **arcs** at the points where edge ownership changes,
  2. simplifies each unique arc **once**,
  3. rebuilds the rings from those shared, already-simplified arcs.

Both neighbours therefore get byte-identical border geometry — zero slivers — and
it falls out for free that an arc owned by one ring is **coastline** while an arc
owned by two is an **internal border**, so the map can stroke them differently and
each exactly once (stroking every country ring would double-darken every border).

Coordinate rounding is a per-vertex operation and so is topology-safe by
construction; it happens before the topology build and doubles as a cheap reducer.

Output format (compact on purpose — this file is inlined into route-maps.html)
-----------------------------------------------------------------------------
    {"bbox": [minLon, minLat, maxLon, maxLat],
     "land":    [[lon,lat, lon,lat, …], …],   # closed rings, fill with nonzero
     "coast":   [[lon,lat, …], …],            # open polylines, stroke strong
     "borders": [[lon,lat, …], …],            # open polylines, stroke soft
     "cities":  [["Berlin", lon, lat, rank], …]}

Flat coordinate arrays, `lon,lat` interleaved (GeoJSON axis order), so the payload
is mostly digits and commas. Rings repeat their first point at the end.

Determinism / re-runs: the script is idempotent — same source + same knobs give a
byte-identical file. The download is cached, output is written to a temp file and
atomically renamed, and Ctrl-C leaves the previous asset untouched.

Natural Earth is public domain (CC0); no attribution is required, but see
design/DESIGN.md §6.11 and the app's Attributions screen.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import tempfile
import urllib.request
from pathlib import Path

# ── Knobs ────────────────────────────────────────────────────────────────────
# The clip window. `stations.csv` (70,837 stations / 43 countries) spans lat
# 31.63…68.44, lon -16.92…44.75 — Marrakech is the southern outlier and Nordkapp
# the northern one. The window covers all of it with margin, so a route can never
# be drawn onto blank sea. Land is clipped, not whole countries, so the cost of
# the margin is only the vertices inside it.
BBOX = (-26.0, 27.0, 46.0, 72.0)  # minLon, minLat, maxLon, maxLat

# Rendering happens in an equirectangular space scaled by cos(lat0) (see
# MapProjection in RouteMap.swift); simplify in that same space so the tolerance
# is uniform in *rendered* units rather than in longitude degrees.
K = math.cos(51.0 * math.pi / 180.0)

# Douglas-Peucker tolerance in rendered units (degrees of latitude). A typical
# route (Hamburg→Stuttgart, ~4.8° of latitude) puts ~112 viewBox units across
# that span, so 1 viewBox unit ≈ 0.043° and this tolerance stays sub-unit. Even at
# the tightest zoom the projection allows (MIN_SPAN_DEG, ~1 unit ≈ 0.011°) it costs
# under two units. Measured against the size budget: 0.01 → 252 KB, 0.02 → 175 KB,
# 0.03 → 141 KB; 0.02 is the knee where the coastline still reads as itself.
TOLERANCE = 0.02
# Drop islands smaller than this (square degrees, shoelace on raw lon/lat). Keeps
# Sicily/Sardinia/Corsica/Mallorca/Gotland; drops the Norwegian and Aegean islet
# confetti that costs vertices and reads as noise at this scale.
MIN_ISLAND_AREA = 0.06
# Output precision. 3dp ≈ 110 m of latitude — far finer than the tolerance, but
# cheap, and it keeps the shared-vertex identity that the topology build needs.
PRECISION = 3

SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_50m_admin_0_countries.geojson"
)

# Reference cities: hand-curated, not derived from stations.csv (70k rows — way
# past what a backdrop should carry, and station density is not prominence).
# These exist only to orient the eye, so the list is capitals + the large rail
# hubs a traveller would recognise, ranked so a zoomed-out map can thin them:
#   rank 1 — you would know it from the shape of the country alone
#   rank 2 — major hub, worth showing once there is room
# The renderer filters to the fitted viewBox and de-clutters against the route.
CITIES: list[tuple[str, float, float, int]] = [
    # name, lat, lon, rank
    ("London", 51.51, -0.13, 1), ("Paris", 48.86, 2.35, 1), ("Madrid", 40.42, -3.70, 1),
    ("Barcelona", 41.39, 2.17, 2), ("Lisboa", 38.72, -9.14, 1), ("Roma", 41.90, 12.50, 1),
    ("Milano", 45.46, 9.19, 2), ("Napoli", 40.85, 14.27, 2), ("Berlin", 52.52, 13.40, 1),
    ("München", 48.14, 11.58, 2), ("Köln", 50.94, 6.96, 2), ("Frankfurt", 50.11, 8.68, 2),
    ("Hamburg", 53.55, 10.00, 2), ("Stuttgart", 48.78, 9.18, 2), ("Leipzig", 51.34, 12.37, 2),
    ("Amsterdam", 52.37, 4.90, 1), ("Bruxelles", 50.85, 4.35, 1), ("Wien", 48.21, 16.37, 1),
    ("Zürich", 47.38, 8.54, 2), ("Bern", 46.95, 7.45, 1), ("Genève", 46.20, 6.14, 2),
    ("Praha", 50.08, 14.44, 1), ("Warszawa", 52.23, 21.01, 1), ("Budapest", 47.50, 19.04, 1),
    ("København", 55.68, 12.57, 1), ("Oslo", 59.91, 10.75, 1), ("Stockholm", 59.33, 18.07, 1),
    ("Helsinki", 60.17, 24.94, 1), ("Dublin", 53.35, -6.26, 1), ("Edinburgh", 55.95, -3.19, 2),
    ("Manchester", 53.48, -2.24, 2), ("Lyon", 45.76, 4.84, 2), ("Marseille", 43.30, 5.37, 2),
    ("Bordeaux", 44.84, -0.58, 2), ("Torino", 45.07, 7.69, 2), ("Venezia", 45.44, 12.32, 2),
    ("Firenze", 43.77, 11.26, 2), ("Beograd", 44.79, 20.45, 1), ("Zagreb", 45.81, 15.98, 1),
    ("Bucureşti", 44.43, 26.10, 1), ("Sofia", 42.70, 23.32, 1), ("Athina", 37.98, 23.73, 1),
    ("Kyiv", 50.45, 30.52, 1), ("Minsk", 53.90, 27.57, 1), ("Bratislava", 48.15, 17.11, 1),
    ("Ljubljana", 46.06, 14.51, 1), ("Riga", 56.95, 24.11, 1), ("Vilnius", 54.69, 25.28, 1),
    ("Tallinn", 59.44, 24.75, 1), ("Sevilla", 37.39, -5.98, 2),
    ("València", 39.47, -0.38, 2), ("Porto", 41.15, -8.61, 2), ("Göteborg", 57.71, 11.97, 2),
    ("Istanbul", 41.01, 28.98, 1), ("Innsbruck", 47.27, 11.39, 2), ("Salzburg", 47.81, 13.04, 2),
    ("Basel", 47.56, 7.59, 2), ("Hannover", 52.37, 9.73, 2), ("Nürnberg", 49.45, 11.08, 2),
    ("Dresden", 51.05, 13.74, 2), ("Bremen", 53.08, 8.81, 2), ("Dortmund", 51.51, 7.47, 2),
]


# ── Geometry helpers ─────────────────────────────────────────────────────────

def _shoelace(ring: list[tuple[float, float]]) -> float:
    """Unsigned area of a closed ring, in square degrees (lon/lat, unscaled)."""
    a = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _clip_ring(ring: list[tuple[float, float]], bbox) -> list[tuple[float, float]]:
    """Sutherland-Hodgman clip of a closed ring against an axis-aligned rect.

    Per-segment and deterministic: a segment shared by two countries is clipped to
    the same new vertices in both, so the topology survives clipping.
    """
    minx, miny, maxx, maxy = bbox

    def clip_edge(pts, inside, intersect):
        if not pts:
            return []
        out = []
        prev = pts[-1]
        prev_in = inside(prev)
        for cur in pts:
            cur_in = inside(cur)
            if cur_in:
                if not prev_in:
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif prev_in:
                out.append(intersect(prev, cur))
            prev, prev_in = cur, cur_in
        return out

    def ix_x(a, b, x):
        t = (x - a[0]) / (b[0] - a[0])
        return (x, a[1] + t * (b[1] - a[1]))

    def ix_y(a, b, y):
        t = (y - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), y)

    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring[:]
    pts = clip_edge(pts, lambda p: p[0] >= minx, lambda a, b: ix_x(a, b, minx))
    pts = clip_edge(pts, lambda p: p[0] <= maxx, lambda a, b: ix_x(a, b, maxx))
    pts = clip_edge(pts, lambda p: p[1] >= miny, lambda a, b: ix_y(a, b, miny))
    pts = clip_edge(pts, lambda p: p[1] <= maxy, lambda a, b: ix_y(a, b, maxy))
    if not pts:
        return []
    return pts + [pts[0]]


def _quantize(ring, precision: int):
    """Round to the output grid and drop consecutive duplicates.

    Purely local, so identical input vertices stay identical across countries —
    this is what lets the topology build below find shared borders at all.
    """
    out = []
    for x, y in ring:
        p = (round(x, precision), round(y, precision))
        if not out or p != out[-1]:
            out.append(p)
    if len(out) > 1 and out[0] != out[-1]:
        out.append(out[0])
    return out


def _perp2(p, a, b) -> float:
    """Squared perpendicular distance from p to segment a→b, in *rendered* space."""
    px, py = p[0] * K, p[1]
    ax, ay = a[0] * K, a[1]
    bx, by = b[0] * K, b[1]
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:  # degenerate (closed arc): fall back to point distance
        return (px - ax) ** 2 + (py - ay) ** 2
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def _douglas_peucker(pts, tol: float):
    """Iterative Douglas-Peucker (no recursion limit worries on long coastlines)."""
    n = len(pts)
    if n <= 2:
        return list(pts)
    tol2 = tol * tol
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        worst, worst_i = -1.0, -1
        a, b = pts[lo], pts[hi]
        for i in range(lo + 1, hi):
            d = _perp2(pts[i], a, b)
            if d > worst:
                worst, worst_i = d, i
        if worst > tol2:
            keep[worst_i] = True
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    return [p for p, k in zip(pts, keep) if k]


def _on_bbox_edge(a, b, bbox, eps=1e-9) -> bool:
    """True if the whole edge a→b lies along one side of the clip window.

    Those are an artifact of clipping, not coastline; drawing them would put a
    ruler-straight 'shore' through the middle of Russia.
    """
    minx, miny, maxx, maxy = bbox
    return (
        (abs(a[0] - minx) < eps and abs(b[0] - minx) < eps)
        or (abs(a[0] - maxx) < eps and abs(b[0] - maxx) < eps)
        or (abs(a[1] - miny) < eps and abs(b[1] - miny) < eps)
        or (abs(a[1] - maxy) < eps and abs(b[1] - maxy) < eps)
    )


# ── Pipeline ─────────────────────────────────────────────────────────────────

def load_rings(source: Path, bbox) -> list[list[tuple[float, float]]]:
    """Exterior rings of every country touching the bbox, clipped and quantized.

    Interior rings (holes) are dropped: at 50m there are only 4 in Europe and the
    largest is ~95 km² — they are enclaves (Vatican, San Marino, Büsingen,
    Campione), and since every country fills in the same land colour, the enclave's
    own polygon fills the hole. Dropping them is visually identical and removes the
    fill-rule question entirely.
    """
    data = json.loads(source.read_text())
    rings: list[list[tuple[float, float]]] = []
    for feat in data["features"]:  # file order — deterministic
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        polys = [coords] if geom.get("type") == "Polygon" else coords
        for poly in polys:
            if not poly:
                continue
            ring = [(float(x), float(y)) for x, y in poly[0]]
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            if max(xs) < bbox[0] or min(xs) > bbox[2] or max(ys) < bbox[1] or min(ys) > bbox[3]:
                continue
            clipped = _clip_ring(ring, bbox)
            if len(clipped) < 4:
                continue
            if _shoelace(clipped) < MIN_ISLAND_AREA:
                continue
            q = _quantize(clipped, PRECISION)
            if len(q) < 4 or _shoelace(q) <= 0:
                continue
            rings.append(q)
    return rings


def build_topology(rings, bbox, tol: float):
    """Cut rings into shared arcs, simplify each arc once, rebuild.

    Returns (land_rings, coast_arcs, border_arcs).
    """
    # 1. Which rings own each undirected edge?
    edge_owners: dict[tuple, set[int]] = {}
    for rid, ring in enumerate(rings):
        for a, b in zip(ring, ring[1:]):
            edge_owners.setdefault((a, b) if a <= b else (b, a), set()).add(rid)

    def sig(a, b) -> tuple:
        return tuple(sorted(edge_owners[(a, b) if a <= b else (b, a)]))

    # 2. Walk each ring, cutting a new arc wherever edge ownership changes.
    ring_arcs: list[list[tuple]] = []   # per ring: ordered list of arc keys
    arc_pts: dict[tuple, list] = {}     # canonical key -> raw points
    arc_sig: dict[tuple, tuple] = {}

    for ring in rings:
        edges = list(zip(ring, ring[1:]))
        sigs = [sig(a, b) for a, b in edges]
        n = len(edges)
        # Rotate so the walk starts at an ownership change; a ring with uniform
        # ownership (a lone island) has none and stays a single closed arc.
        start = next((i for i in range(n) if sigs[i] != sigs[i - 1]), None)
        order = range(n) if start is None else [(start + i) % n for i in range(n)]
        cur_pts: list = []
        cur_sig = None
        keys: list[tuple] = []

        def flush():
            if len(cur_pts) >= 2:
                fwd, rev = tuple(cur_pts), tuple(reversed(cur_pts))
                # Canonical orientation, so the two rings sharing a border agree on
                # one key. Store the points *in the key's own orientation* — storing
                # `cur_pts` here instead would hand back a backwards arc to whichever
                # ring happened to traverse it the other way, tearing the ring open.
                key = fwd if fwd <= rev else rev
                arc_pts.setdefault(key, list(key))
                arc_sig[key] = cur_sig
                keys.append((key, fwd == key))
        for i in order:
            a, b = edges[i]
            if cur_sig is not None and sigs[i] != cur_sig:
                flush()
                cur_pts = []
            if not cur_pts:
                cur_pts = [a]
            cur_sig = sigs[i]
            cur_pts.append(b)
        flush()
        ring_arcs.append(keys)

    # 3. Simplify each unique arc exactly once — the whole point of the exercise.
    simp = {k: _douglas_peucker(v, tol) for k, v in arc_pts.items()}

    # 4. Rebuild rings from the shared, simplified arcs.
    land = []
    for keys in ring_arcs:
        out: list = []
        for key, forward in keys:
            pts = simp[key]
            seg = pts if forward else list(reversed(pts))
            out.extend(seg if not out else seg[1:])
        if len(out) >= 4:
            if out[0] != out[-1]:
                out.append(out[0])
            land.append(out)

    # 5. Classify: one owner = coastline, two+ = internal border. Arcs lying on the
    #    clip window are neither — they are where we cut the world off.
    coast, borders = [], []
    for key, owners in sorted(arc_sig.items()):
        pts = simp[key]
        if len(pts) < 2:
            continue
        if all(_on_bbox_edge(a, b, bbox) for a, b in zip(pts, pts[1:])):
            continue
        (borders if len(owners) >= 2 else coast).append(pts)
    return land, coast, borders


def flat(rings, precision: int) -> list[list[float]]:
    out = []
    for r in rings:
        f = []
        for x, y in r:
            f.append(round(x, precision))
            f.append(round(y, precision))
        out.append(f)
    return out


def render(payload: dict) -> str:
    """Compact JSON: no spaces, one array per line so diffs stay reviewable."""
    def arr(a):
        return "[" + ",".join(json.dumps(v, ensure_ascii=False) for v in a) + "]"
    parts = [
        '{"note":%s' % json.dumps(payload["note"], ensure_ascii=False),
        '"bbox":%s' % arr(payload["bbox"]),
    ]
    for name in ("land", "coast", "borders"):
        rows = ",\n".join(arr(r) for r in payload[name])
        parts.append('"%s":[\n%s]' % (name, rows))
    cities = ",\n".join(arr(c) for c in payload["cities"])
    parts.append('"cities":[\n%s]' % cities)
    return ",\n".join(parts) + "}\n"


def fetch(url: str, cache: Path) -> Path:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    print(f"  ↓ {url}\n    → {cache}", file=sys.stderr)
    tmp = cache.with_suffix(cache.suffix + ".part")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:  # noqa: S310
        f.write(r.read())
    tmp.replace(cache)  # atomic: a Ctrl-C mid-download never leaves a half cache
    return cache


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)  # atomic — the old asset survives any interruption
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


BEGIN = "  // >>> BEGIN GENERATED"
END = "  // <<< END GENERATED"


def splice_html(html: str, payload_json: str) -> str:
    """Re-inline the asset into route-maps.html between its generated markers.

    The page is opened straight off disk, so it cannot fetch(); the data has to
    live in the file. Owning the splice here means the three copies of this asset
    (canonical, inlined, iOS bundle) cannot drift apart.
    """
    a = html.index(BEGIN)
    b = html.index(END, a)
    head = html[a:html.index("\n", html.index("\n", a) + 1) + 1]  # keep the 2 marker comment lines
    return html[:a] + head + "  const GEO = " + payload_json + ";\n" + html[b:]


def main() -> int:
    global MIN_ISLAND_AREA
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", help="path or URL of an admin_0_countries GeoJSON")
    ap.add_argument("--out", default=str(repo / "design" / "europe-geo.json"),
                    help="the canonical vendored asset")
    ap.add_argument("--ios-out",
                    default=str(repo / "ios" / "TransfrApp" / "Sources" / "TransfrUI"
                               / "Resources" / "europe-geo.json"),
                    help="bundle copy for RouteMap.swift (Bundle.module)")
    ap.add_argument("--html", default=str(repo / "design" / "route-maps.html"),
                    help="page to re-inline the asset into (it opens offline, so it can't fetch)")
    ap.add_argument("--cache", default=str(repo / ".geo-cache" / "ne_admin_0.geojson"),
                    help="where to keep the downloaded source (gitignored)")
    ap.add_argument("--tolerance", type=float, default=TOLERANCE)
    ap.add_argument("--min-island-area", type=float, default=MIN_ISLAND_AREA)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed asset differs from a fresh build")
    args = ap.parse_args()

    src = args.source or SOURCE_URL
    path = fetch(src, Path(args.cache)) if src.startswith(("http://", "https://")) else Path(src)
    if not path.exists():
        print(f"error: source not found: {path}", file=sys.stderr)
        return 2

    MIN_ISLAND_AREA = args.min_island_area

    print(f"  source {path} ({path.stat().st_size/1e6:.2f} MB)", file=sys.stderr)
    rings = load_rings(path, BBOX)
    pts_in = sum(len(r) for r in rings)
    land, coast, borders = build_topology(rings, BBOX, args.tolerance)
    pts_out = sum(len(r) for r in land)

    payload = {
        "note": ("Generated by scripts/build_map_geo.py from Natural Earth "
                 "admin_0_countries (public domain). Do not hand-edit. "
                 "lon,lat interleaved; land rings are closed."),
        "bbox": list(BBOX),
        "land": flat(land, PRECISION),
        "coast": flat(coast, PRECISION),
        "borders": flat(borders, PRECISION),
        "cities": [[n, round(lo, 2), round(la, 2), r] for n, la, lo, r in _dedupe_cities()],
    }
    text = render(payload)
    inline = text.rstrip("\n")

    out, ios_out, html_path = Path(args.out), Path(args.ios_out), Path(args.html)
    html = html_path.read_text() if html_path.exists() else None
    spliced = splice_html(html, inline) if html else None

    # This asset lives in three places — the canonical file, inlined in the page
    # (which opens offline and so cannot fetch), and the iOS bundle copy. They are
    # written together and checked together so they cannot drift.
    targets = [(out, text), (ios_out, text)]
    if spliced is not None:
        targets.append((html_path, spliced))

    if args.check:
        stale = []
        for path, want in targets:
            if not path.exists():
                stale.append(f"FAIL {path} does not exist")
            elif path.read_text() != want:
                stale.append(f"FAIL {path} is stale")
        if stale:
            print("\n".join(stale) + f"\n     re-run {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"OK   {len(targets)} targets up to date ({len(text)/1024:.1f} KB)", file=sys.stderr)
        return 0

    for path, want in targets:
        write_atomic(path, want)
    print(
        f"  rings {len(rings)} · pts {pts_in} → {pts_out} "
        f"({100*pts_out/max(pts_in,1):.0f}%) · coast {len(coast)} arcs · "
        f"borders {len(borders)} arcs · cities {len(payload['cities'])}\n"
        f"  wrote {len(targets)} targets — asset {len(text)/1024:.1f} KB\n"
        + "\n".join(f"    {p}" for p, _ in targets),
        file=sys.stderr,
    )
    return 0


def _dedupe_cities():
    """CITIES is hand-maintained; tolerate an accidental duplicate entry."""
    seen, out = set(), []
    for name, la, lo, rank in CITIES:
        if name in seen:
            continue
        seen.add(name)
        out.append((name, la, lo, rank))
    return out


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted — existing asset left untouched", file=sys.stderr)
        sys.exit(130)
