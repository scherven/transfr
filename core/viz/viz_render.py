"""
Render a viz JSON (see core/viz_export.py) as an interactive 3D Plotly scene.

Deliberately bare -- no axes, grid, or plot background, and NONE of the station's
wider mapped web of walkways, platforms and other stairs/escalators/elevators the
search merely looked at. Only what is *walk-relevant* is drawn:

  * the chosen route, thick, with its turns, and its start/end platforms marked;
  * how you change floors along it -- each level change drawn as a riser coloured
    by what you actually take (stairs / escalator / elevator / ramp) and labelled,
    because "which one do I take between floors" is the decision the view exists
    to answer;
  * the named shops the walk passes, drawn with their real OSM footprint (a low
    extruded prism; a small marker where OSM has only a point), so you orient by
    what you'd actually see -- fixtures nobody navigates by (ATMs, toilets, taxi
    stands, info booths) and shops off the walked route are dropped, and the names
    are dodged in the view plane so none overlap;
  * faint reference planes at the floors the walk uses, labelled, so the vertical
    structure reads.

The view is framed on the path (--margin sets how much breathing room around it).

The vertical axis is the OSM `level` tag scaled to a nominal per-floor height,
NOT real elevation -- OSM does not carry usable indoor elevations (verified: no
`ele` on nodes inside stations, `step_count` missing on most stairs). Levels are
evenly spaced and Z is multiplied by --z-exag for legibility (one 4 m floor is
invisible next to a 100 m concourse). Both facts are stated in the header.

Usage:
    .venv/bin/python core/viz_render.py core/viz_out/5688517_1_16.json --open
"""

import argparse
import json
import math
import os
from urllib.parse import quote

import plotly.graph_objects as go

# Tab favicon for the rendered walk pages: the "transfr" mark -- a lowercase t in
# the app azure with a green start / red end tip. Kept in sync with the canonical
# agents/design/favicon.svg; inlined as an SVG data URI so each page stays self-contained.
_FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='7' fill='#fff'/>"
    "<path d='M9 13.3 H21' fill='none' stroke='#0A63F0' stroke-width='3' stroke-linecap='round'/>"
    "<path d='M15 8 V22 Q15 25.8 18.7 25.8 H21' fill='none' stroke='#0A63F0' "
    "stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/>"
    "<circle cx='15' cy='8' r='2.5' fill='#0FA968'/>"
    "<circle cx='21' cy='25.8' r='2.5' fill='#E0402F'/>"
    "</svg>"
)
_FAVICON = "data:image/svg+xml," + quote(_FAVICON_SVG)

PATH_COLOR = "#ff8800"
START_COLOR = "#2f9e44"
END_COLOR = "#e03131"
# Synthetic stitch segments (core/build_stitch_bridges.py): the one part of a
# route we INFERRED -- a short hop onto a platform whose polygon a connector ends
# inside without a shared node -- rather than read off a mapped footpath. Drawn
# as its own beaded category in a caution red, on top of the path, so it's clear
# this stretch is not a verified way. Red-with-gaps stands in for a dashed line
# (Plotly 3D lines can't dash).
STITCH_COLOR = "#f03e3e"

PLANE_FILL = "rgb(120,128,148)"   # opacity applied separately on the mesh
PLANE_EDGE = "rgba(120,128,148,0.35)"
PLANE_LABEL = "#6a7280"

# Colours for the path's level-change risers, keyed by the connector kind you
# take. "vertical" = a level change at a node with nothing saying how.
KIND_COLOR = {"stairs": "#7048e8", "escalator": "#1098ad", "elevator": "#e64980",
              "ramp": "#2f9e44", "vertical": "#495057"}

# Landmark footprints: the named stores nearest the WALKED route, drawn with
# their real OSM footprint extruded to a low prism -- so you orient by each
# shop's actual shape and position, not a uniform cube. Shops OSM maps as only a
# point (no footprint) get a small marker instead. The raw details layer is
# mostly fixtures a traveller does NOT navigate by -- ATMs, toilets, taxi stands,
# info booths, artwork -- plus unnamed building parts; all of that is dropped.
# Only the handful closest to the stretch of route the walk actually touches are
# kept, so the always-on labels stay readable instead of a wall of text.
LANDMARK_DENY_SUBTYPE = {
    "atm", "toilets", "shower", "taxi", "vending_machine", "charging_station",
    "bicycle_rental", "bicycle_parking", "car_rental", "motorcycle_rental",
    "mobility_hub", "information", "artwork", "clock", "drinking_water",
    "vacant", "parking", "parking_entrance", "bureau_de_change",
    "luggage_locker", "locker", "left_luggage",
}
LANDMARK_MAX_DIST_M = 15.0    # a shop counts only if its footprint comes within
                              # this of a segment the walk ACTUALLY traverses on
                              # that floor (point-to-segment, not crow-flies)
LANDMARK_MAX_COUNT = 10       # cap so the always-on labels don't crowd
SHOP_HEIGHT_M = 4.0          # extrusion height of a footprint prism (scene z units)
NODE_TOP_M = 2.5             # a point-only shop's marker/label sits this high

# Camera direction the default view uses (shared with the scene below). Label
# placement projects into this camera's screen plane so it can dodge overlaps
# where they actually happen -- on screen -- not in world space, where two cubes
# on different floors can be far apart yet stack right on top of each other.
CAMERA_EYE = (1.25, 1.25, 0.9)

# Each name floats over its cube on a thin stem. In a packed concourse many cubes
# sit within a few metres, so a label that would collide (on screen) with one
# already placed is lifted straight up -- vertically on screen -- until it clears,
# giving a sign-post look that keeps every name readable. Keep-outs are in metres
# in the screen plane; nearest landmarks are placed first so they sit lowest.
LABEL_KEEPOUT_X_M = 11.0      # min horizontal gap between label centres on screen
LABEL_KEEPOUT_Y_M = 4.5       # min vertical gap between labels on screen
LABEL_STEM_MIN_M = 12.0       # a label starts this far above its cube top (clears
                              # cubes standing in front of it, nearer the camera)
LABEL_STEM_STEP_M = 4.5       # and is raised in these steps until it clears

LANDMARK_COLOR = {
    "shop": "#f59f00", "amenity": "#e8590c", "tourism": "#ae3ec9",
    "office": "#4263eb", "leisure": "#0ca678",
}


def _focus_window(data, margin):
    """(x0, x1, y0, y1) the scene is framed to: a margin around the path if there
    is one, else the full drawn footprint."""
    path = data["path"]
    if path.get("found") and path.get("points"):
        xs = [p[0] for p in path["points"]]
        ys = [p[1] for p in path["points"]]
    else:
        bb = data["meta"].get("bbox") or {"min_x": -1, "max_x": 1, "min_y": -1, "max_y": 1}
        xs = [bb["min_x"], bb["max_x"]]
        ys = [bb["min_y"], bb["max_y"]]
    return (min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin)


def _level_label(v):
    return f"L{int(v)}" if float(v).is_integer() else f"L{v:g}"


def _add_level_planes(fig, data, win, exag, levels):
    """A faint slab + labelled outline at each of `levels`, sized to the focus
    window -- the reference that makes the walk's vertical structure legible.
    Only the floors the walk actually uses are passed in, so empty and
    connector-interpolated levels don't add ghost planes."""
    if not levels:
        return
    meta = data["meta"]
    x0, x1, y0, y1 = win
    floor = meta.get("floor_height_m", 4.0)

    # Stack the floor labels just *outside* the back corner (pushed out from the
    # window centre) so they read like floor numbers up the side of a shaft and
    # stay clear of the landmark cubes/labels that fill the middle.
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    llx = x0 - (cx - x0) * 0.5
    lly = y0 - (cy - y0) * 0.5

    ox, oy, oz = [], [], []
    lx, ly, lz, lt = [], [], [], []
    for lv in levels:
        z = lv * floor * exag
        fig.add_trace(go.Mesh3d(
            x=[x0, x1, x1, x0], y=[y0, y0, y1, y1], z=[z, z, z, z],
            i=[0, 0], j=[1, 2], k=[2, 3],
            color=PLANE_FILL, opacity=0.05, flatshading=True,
            hoverinfo="skip", showlegend=False,
        ))
        ox += [x0, x1, x1, x0, x0, None]
        oy += [y0, y0, y1, y1, y0, None]
        oz += [z, z, z, z, z, None]
        lx.append(llx)
        ly.append(lly)
        lz.append(z)
        lt.append(_level_label(lv))

    zmin, zmax = min(lz), max(lz)
    fig.add_trace(go.Scatter3d(x=ox + [x0, x0], y=oy + [y0, y0], z=oz + [zmin, zmax],
                               mode="lines", line=dict(color=PLANE_EDGE, width=1),
                               hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter3d(x=lx, y=ly, z=lz, mode="text", text=lt,
                               textposition="middle left",
                               textfont=dict(size=13, color=PLANE_LABEL),
                               hoverinfo="skip", showlegend=False))


def _add_transitions(fig, path, exag):
    """Draw each path level-change as a riser in its connector colour, over the
    orange path, labelled -- so the route shows *how* you change floors, incl.
    elevators/stairs mapped as a single node (which have no way to colour, and
    are why the plain path is otherwise all one colour through a vertical move)."""
    trans = path.get("transitions") or []
    if not trans:
        return []
    by_kind = {}
    for t in trans:
        by_kind.setdefault(t["kind"], []).append(t)
    lx, ly, lz, lt = [], [], [], []
    for kind, items in by_kind.items():
        xs, ys, zs = [], [], []
        for t in items:
            f, to = t["from"], t["to"]
            xs += [f[0], to[0], None]
            ys += [f[1], to[1], None]
            zs += [f[2] * exag, to[2] * exag, None]
            top = to if to[2] >= f[2] else f
            lx.append(top[0])
            ly.append(top[1])
            lz.append(top[2] * exag)
            lt.append(kind)
        fig.add_trace(go.Scatter3d(x=xs, y=ys, z=zs, mode="lines",
                                   line=dict(color=KIND_COLOR.get(kind, "#495057"), width=11),
                                   hoverinfo="skip", name=kind))
    fig.add_trace(go.Scatter3d(x=lx, y=ly, z=lz, mode="text", text=lt,
                               textposition="middle right",
                               textfont=dict(size=11, color="#343a40"),
                               hoverinfo="skip", showlegend=False))
    return list(zip(lx, ly, lz))  # label anchors, so landmarks can dodge them


def _add_stitches(fig, path, exag):
    """Draw each synthetic stitch hop as its own beaded caution-red segment on
    top of the path -- the one stretch of a route we INFERRED (a connector ends
    inside a platform polygon without a shared node) rather than read off a
    mapped footpath. Its own legend entry (via the trace name) so the viewer
    knows this link can't be taken as a verified way."""
    segs = path.get("stitch_segments") or []
    if not segs:
        return
    xs, ys, zs = [], [], []
    for s in segs:
        f, t = s["from"], s["to"]
        xs += [f[0], t[0], None]
        ys += [f[1], t[1], None]
        zs += [f[2] * exag, t[2] * exag, None]
    fig.add_trace(go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines+markers",
        line=dict(color=STITCH_COLOR, width=9),
        marker=dict(size=3.5, color=STITCH_COLOR),
        hoverinfo="skip", name="stitched · inferred link"))


def _is_landmark(d):
    """A detail worth orienting by: a *named* POI (not a building part) whose
    subtype is a shopfront/place, not a fixture on the deny list."""
    return (d.get("kind") == "poi" and bool(d.get("name"))
            and d.get("subtype") not in LANDMARK_DENY_SUBTYPE)


def _point_seg_dist(px, py, ax, ay, bx, by):
    """Distance from point (px,py) to segment (a,b). A zero-length segment (a==b)
    degrades to point-to-point, so path vertices double as their own segments."""
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    if d2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / d2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _pick_landmarks(details, path_pts, floor, max_dist, max_count):
    """Named landmarks that abut the stretch of route the walk actually touches:
    the shop must be on a floor the path walks, and its footprint must come within
    `max_dist` of a segment the walk traverses *on that floor* -- not merely near
    some point of the route two floors up. Deduped by name (chains recur), nearest
    first, capped.

    Measuring against the walked segments (not just proximity in plan) is what
    keeps the shops lining the corridor you take, rather than every store that
    happens to sit above the platforms below. The export drops each POI onto its
    OSM `level` (ground when untagged, which most are); a floor the route never
    sets foot on contributes no segments, so its shops are excluded outright."""
    if not path_pts:
        return []
    lvl = (lambda z: round(z / floor)) if floor else (lambda z: 0)
    # Walked segments per floor. A consecutive pair that stays on one level is a
    # walked stretch; a pair that changes level is a riser (a vertical move, not a
    # walked corridor) and is skipped. Every vertex is also a zero-length segment
    # so a floor touched at a single point still counts.
    segs_by_level = {}
    for a, b in zip(path_pts, path_pts[1:]):
        if lvl(a[2]) == lvl(b[2]):
            segs_by_level.setdefault(lvl(a[2]), []).append((a[0], a[1], b[0], b[1]))
    for p in path_pts:
        segs_by_level.setdefault(lvl(p[2]), []).append((p[0], p[1], p[0], p[1]))

    def footprint_pts(d):
        if d.get("outline"):
            return [(p[0], p[1]) for p in d["outline"]]
        return [(d["xyz"][0], d["xyz"][1])]

    cand = []
    for d in details:
        if not _is_landmark(d):
            continue
        segs = segs_by_level.get(lvl(d["xyz"][2]))
        if not segs:                       # a floor the route never walks
            continue
        dist = min(_point_seg_dist(vx, vy, *s)
                   for vx, vy in footprint_pts(d) for s in segs)
        if dist <= max_dist:
            cand.append((dist, d))
    cand.sort(key=lambda t: t[0])

    seen, out = set(), []
    for _, d in cand:
        key = d["name"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
        if len(out) >= max_count:
            break
    return out


def _prism_faces(ring, base_z, height):
    """Vertices + triangles extruding a footprint `ring` [(x,y),...] from z=base_z
    up by `height` -- a low prism of the shop's real shape. Fan-triangulated caps
    (fine for the simple, near-convex footprints stations use) plus wall quads.
    Returned as parallel lists so many shops batch into one Mesh3d."""
    r = ring[:-1] if len(ring) > 2 and ring[0] == ring[-1] else list(ring)
    n = len(r)
    cx = sum(p[0] for p in r) / n
    cy = sum(p[1] for p in r) / n
    top = base_z + height
    x = [p[0] for p in r] + [p[0] for p in r] + [cx, cx]
    y = [p[1] for p in r] + [p[1] for p in r] + [cy, cy]
    z = [base_z] * n + [top] * n + [base_z, top]
    bc, tc = 2 * n, 2 * n + 1          # bottom-centre, top-centre
    i, j, k = [], [], []
    for a in range(n):
        b = (a + 1) % n
        i += [bc, tc, a, a]            # bottom cap, top cap, two wall triangles
        j += [a, n + b, b, n + b]
        k += [b, n + a, n + b, n + a]
    return x, y, z, i, j, k


def _screen_basis(eye=CAMERA_EYE):
    """Right / up unit vectors of the view plane for a camera looking from `eye`
    at the scene centre with world-up +Z. Projecting a world point onto these
    gives its (horizontal, vertical) position on screen -- what actually decides
    whether two labels overlap."""
    n = math.sqrt(sum(c * c for c in eye))
    vd = (eye[0] / n, eye[1] / n, eye[2] / n)          # view direction (to eye)
    up = (0.0, 0.0, 1.0)
    rx = up[1] * vd[2] - up[2] * vd[1]                  # right = up x view_dir
    ry = up[2] * vd[0] - up[0] * vd[2]
    rz = up[0] * vd[1] - up[1] * vd[0]
    rn = math.sqrt(rx * rx + ry * ry + rz * rz)
    right = (rx / rn, ry / rn, rz / rn)
    sup = (vd[1] * right[2] - vd[2] * right[1],         # screen-up = view_dir x right
           vd[2] * right[0] - vd[0] * right[2],
           vd[0] * right[1] - vd[1] * right[0])
    return right, sup


def _add_landmarks(fig, data, exag, avoid_pts=()):
    """Draw the named landmarks abutting the walked route with their real OSM
    footprint (a low prism), coloured by category, each name floating over the
    shop on a thin stem -- so you orient by the actual shape and place of the
    shops you pass. Point-only shops get a small marker. Labels are dodged in the
    view plane so none overlap in the default view, including the path's own
    labels passed in `avoid_pts`. Returns the categories drawn, for the legend."""
    picks = _pick_landmarks(data.get("details") or [],
                            data.get("path", {}).get("points") or [],
                            data["meta"].get("floor_height_m", 4.0) or 4.0,
                            LANDMARK_MAX_DIST_M, LANDMARK_MAX_COUNT)
    if not picks:
        return []
    right, sup = _screen_basis()

    def screen(p):
        return (right[0] * p[0] + right[1] * p[1] + right[2] * p[2],
                sup[0] * p[0] + sup[1] * p[1] + sup[2] * p[2])

    # Place labels nearest-first (closest, most useful names sit lowest/clearest).
    # Raise each straight up the screen from its shop until it clears every label
    # already down -- moving along screen-up changes only the vertical screen
    # position, so a name stays directly over its own shop. Seed with the path's
    # own labels (endpoints, level-change risers) so names dodge those too.
    prisms = {}                         # category -> [(ring, base_z)]
    markers = {}                        # category -> [(x, y, z)]  (point-only shops)
    present = set()
    placed = [screen(p) for p in avoid_pts]   # (screen_x, screen_y) of set labels
    sx, sy, sz = [], [], []             # stem segments (shop top -> label)
    lx, ly, lz, lt = [], [], [], []     # label anchors + text
    for d in picks:
        cx, cy, z = d["xyz"]
        base = z * exag
        cat = d["category"]
        present.add(cat)
        if d.get("outline"):
            prisms.setdefault(cat, []).append(([(p[0], p[1]) for p in d["outline"]], base))
            top_z = base + SHOP_HEIGHT_M
        else:
            markers.setdefault(cat, []).append((cx, cy, base))
            top_z = base + NODE_TOP_M
        top = (cx, cy, top_z)
        px, py0 = screen(top)
        t = LABEL_STEM_MIN_M
        while any(abs(px - qx) < LABEL_KEEPOUT_X_M and abs((py0 + t) - qy) < LABEL_KEEPOUT_Y_M
                  for qx, qy in placed):
            t += LABEL_STEM_STEP_M
        placed.append((px, py0 + t))
        lp = (top[0] + sup[0] * t, top[1] + sup[1] * t, top[2] + sup[2] * t)
        sx += [top[0], lp[0], None]
        sy += [top[1], lp[1], None]
        sz += [top[2], lp[2], None]
        lx.append(lp[0])
        ly.append(lp[1])
        lz.append(lp[2])
        lt.append(d["name"])

    for cat, items in prisms.items():
        vx, vy, vz, vi, vj, vk = [], [], [], [], [], []
        for ring, base in items:
            fx, fy, fz, fi, fj, fk = _prism_faces(ring, base, SHOP_HEIGHT_M)
            off = len(vx)
            vx += fx
            vy += fy
            vz += fz
            vi += [n + off for n in fi]
            vj += [n + off for n in fj]
            vk += [n + off for n in fk]
        fig.add_trace(go.Mesh3d(
            x=vx, y=vy, z=vz, i=vi, j=vj, k=vk,
            color=LANDMARK_COLOR.get(cat, "#868e96"), opacity=0.9,
            flatshading=True, hoverinfo="skip", showlegend=False))
    for cat, items in markers.items():
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in items], y=[p[1] for p in items], z=[p[2] for p in items],
            mode="markers", marker=dict(size=6, color=LANDMARK_COLOR.get(cat, "#868e96"),
                                        symbol="square", line=dict(width=0)),
            hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter3d(
        x=sx, y=sy, z=sz, mode="lines", line=dict(color="rgba(60,64,72,0.4)", width=1),
        hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter3d(
        x=lx, y=ly, z=lz, mode="text", text=lt, textposition="middle center",
        textfont=dict(size=11, color="#212529"), hoverinfo="skip",
        showlegend=False))
    return sorted(present)


def build_figure(data, exag: float, margin: float) -> go.Figure:
    meta, path = data["meta"], data["path"]
    win = _focus_window(data, margin)
    fig = go.Figure()

    # Only the walk itself is drawn -- the route, how you change floors, and the
    # shops you pass -- NOT the station's full mapped web of walkways, platforms
    # and every other stair/escalator/elevator the search merely looked at. So
    # the reference planes are limited to the floors the walk actually touches.
    floor = meta.get("floor_height_m", 4.0) or 4.0
    if path.get("found") and path.get("points"):
        walked_levels = sorted({round(p[2] / floor) for p in path["points"]})
    else:
        walked_levels = [lv for lv in (meta.get("levels_present") or []) if float(lv).is_integer()]
    _add_level_planes(fig, data, win, exag, walked_levels)

    avoid_pts = []   # path's own label anchors, so landmark names dodge them
    if path.get("found"):
        pts = path["points"]
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in pts], y=[p[1] for p in pts], z=[p[2] * exag for p in pts],
            mode="lines", line=dict(color=PATH_COLOR, width=8),
            hoverinfo="skip", name="path"))
        _add_stitches(fig, path, exag)
        avoid_pts += _add_transitions(fig, path, exag)
        floor_h = meta.get("floor_height_m", 4.0)
        for key, color, sym in (("start", START_COLOR, "circle"), ("end", END_COLOR, "diamond")):
            ep = path["endpoints"][key]
            x, y, z = ep["xyz"]
            lvl = _level_label(round(z / floor_h)) if floor_h else "?"
            fig.add_trace(go.Scatter3d(
                x=[x], y=[y], z=[z * exag], mode="markers+text",
                marker=dict(size=7, color=color, symbol=sym),
                text=[f" {ep['ref']} · {lvl}"], textposition="top center",
                textfont=dict(size=13), hoverinfo="skip",
                name=f"{key}: platform {ep['ref']} ({lvl})"))
            avoid_pts.append((x, y, z * exag))
        subtitle = f"{path['walking_time_seconds']:g}s · {path['walking_distance_meters']:g}m"
    else:
        subtitle = f"NO PATH ({path.get('reason')})"

    # Nearby named landmarks as labelled cubes -- orient by the shops you'd
    # actually see. A square key per category present goes in the legend.
    present = _add_landmarks(fig, data, exag, avoid_pts=avoid_pts)
    for c in present:
        fig.add_trace(go.Scatter3d(
            x=[None], y=[None], z=[None], mode="markers",
            marker=dict(size=8, color=LANDMARK_COLOR.get(c, "#868e96"), symbol="square"),
            name=c, hoverinfo="skip"))

    # Title/caveat live in a responsive HTML overlay (see write_html), not a
    # Plotly title -- Plotly titles do not wrap and clip on a phone viewport.
    hidden = dict(visible=False, showgrid=False, showbackground=False, showticklabels=False, title="")
    fig.update_layout(
        scene=dict(
            xaxis=hidden, yaxis=hidden, zaxis=hidden,
            aspectmode="data",
            # Orthographic, not perspective: a given level then reads at the same
            # screen height everywhere, so the reference planes/labels line up
            # with the geometry on that floor regardless of depth. Essential for
            # a floor-stack where judging "which level" is the whole point.
            camera=dict(eye=dict(x=CAMERA_EYE[0], y=CAMERA_EYE[1], z=CAMERA_EYE[2]),
                        up=dict(x=0, y=0, z=1), projection=dict(type="orthographic")),
        ),
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=0.02,
                    xanchor="center", x=0.5, itemsizing="constant", font=dict(size=11),
                    bgcolor="rgba(255,255,255,0.65)"),
        margin=dict(l=0, r=0, t=8, b=8),
        template="plotly_white", paper_bgcolor="white",
    )
    fig._viz_subtitle = subtitle  # picked up by main() for the overlay header
    return fig


def _header_html(meta, subtitle, exag):
    floor = meta.get("floor_height_m", 4.0)
    station = meta.get("station_name") or f"relation {meta['relation_id']}"
    caveat = (f"Z = OSM level ×{exag:g} · nominal ~{floor:g} m/floor, not real elevation"
              f" · {meta.get('context_mode', '')}")
    n_stitch = meta.get("n_stitches") or 0
    if n_stitch:
        caveat += f" · {n_stitch} inferred link{'s' if n_stitch != 1 else ''} (red, unverified)"
    return (
        "<div class='hdr'>"
        f"<div class='hdr-t'>{station}</div>"
        f"<div class='hdr-s'>{meta['ref_1']} → {meta['ref_2']} · {subtitle}</div>"
        f"<div class='hdr-c'>{caveat}</div>"
        "</div>"
    )


def write_html(fig, out, meta, subtitle, exag, auto_open):
    """Wrap Plotly's div in a mobile-ready page: viewport meta, full-height
    fill, and a wrapping overlay header (Plotly's own full_html omits the
    viewport tag and its title can't wrap)."""
    config = {"responsive": True, "displaylogo": False,
              "modeBarButtonsToRemove": ["toImage"]}
    div = fig.to_html(full_html=False, include_plotlyjs=True, config=config,
                      default_height="100vh", default_width="100vw")
    station_title = f"{meta.get('station_name', '')} {meta['ref_1']}→{meta['ref_2']}"
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, maximum-scale=1'>"
        f"<link rel='icon' href=\"{_FAVICON}\">"
        f"<title>{station_title}</title>"
        "<style>html,body{margin:0;padding:0;height:100%;overflow:hidden;"
        "font-family:-apple-system,Segoe UI,Roboto,sans-serif;}"
        ".hdr{position:fixed;top:0;left:0;right:0;z-index:10;padding:9px 14px;"
        "pointer-events:none;background:linear-gradient(rgba(255,255,255,0.94),"
        "rgba(255,255,255,0));}"
        ".hdr-t{font-weight:700;font-size:17px;color:#1a1d24;line-height:1.2;}"
        ".hdr-s{font-size:13px;color:#343a40;margin-top:1px;}"
        ".hdr-c{font-size:10px;color:#868e96;margin-top:2px;}</style></head>"
        f"<body>{_header_html(meta, subtitle, exag)}{div}</body></html>"
    )
    with open(out, "w") as f:
        f.write(html)
    if auto_open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(out))


def main():
    ap = argparse.ArgumentParser(description="Render a viz JSON as interactive 3D.")
    ap.add_argument("json", help="path to a viz_export.py output json")
    ap.add_argument("--z-exag", type=float, default=3.0, help="vertical exaggeration (legibility only)")
    ap.add_argument("--margin", type=float, default=None,
                    help="metres of context to keep around the path (default: 18 when landmarks "
                         "are shown, so their cubes fill the frame; 40 otherwise)")
    ap.add_argument("--out", default=None, help="output html (default: alongside the json)")
    ap.add_argument("--open", action="store_true", help="open in the browser when done")
    args = ap.parse_args()

    with open(args.json) as f:
        data = json.load(f)

    # Landmarks cluster tightly around the path, so frame in close when they're
    # present; a bare path view wants more surrounding corridor for context.
    margin = args.margin
    if margin is None:
        margin = 18.0 if data.get("details") else 40.0

    fig = build_figure(data, args.z_exag, margin)
    out = args.out or os.path.splitext(args.json)[0] + ".html"
    write_html(fig, out, data["meta"], fig._viz_subtitle, args.z_exag, args.open)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
