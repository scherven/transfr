"""
Render a viz JSON (see core/viz_export.py) as an interactive 3D Plotly scene.

Deliberately bare -- no axes, grid, or plot background -- but structured so the
station reads at a glance:

  * translucent reference planes, one per OSM `level`, labelled, so you can see
    which floor each way and each stretch of the path sits on;
  * vertical circulation split by type -- stairs, escalator, elevator, ramp --
    each its own colour, because "which one do I take between floors" is the
    whole question a transfer view exists to answer;
  * the chosen path drawn thick and on top, with its start/end platforms marked.

The view is framed on the path: context ways are cropped to a margin around the
route (--margin) so the local transfer structure fills the screen instead of a
station's full-length platform tails swamping it. The export keeps the full
geometry; only the render crops.

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
import os

import plotly.graph_objects as go

PATH_COLOR = "#ff8800"
START_COLOR = "#2f9e44"
END_COLOR = "#e03131"

# Context ways grouped by semantic kind. Order == draw order (later on top), so
# vertical circulation stays visible over flat floor where they overlap.
# (group_key, colour, line width, legend label)
GROUPS = [
    ("walkway", "rgba(150,152,162,0.34)", 2, "walkway"),
    ("platform", "rgba(64,74,96,0.55)", 4, "platform"),
    ("ramp", "#2f9e44", 4, "ramp"),
    ("stairs", "#7048e8", 5, "stairs"),
    ("escalator", "#1098ad", 5, "escalator"),
    ("elevator", "#e64980", 6, "elevator"),
]
VERTICAL_KINDS = {"platform", "ramp", "stairs", "escalator", "elevator"}

PLANE_FILL = "rgb(120,128,148)"   # opacity applied separately on the mesh
PLANE_EDGE = "rgba(120,128,148,0.35)"
PLANE_LABEL = "#6a7280"

# Colours for the path's level-change risers, keyed by connector kind (matches
# GROUPS). "vertical" = a level change at a node with nothing saying how.
KIND_COLOR = {"stairs": "#7048e8", "escalator": "#1098ad", "elevator": "#e64980",
              "ramp": "#2f9e44", "vertical": "#495057"}

# Details layer (landmarks/stores/buildings), revealed by the distance slider.
DETAIL_COLOR = {"shop": "#f59f00", "amenity": "#4263eb", "tourism": "#ae3ec9",
                "office": "#868e96", "leisure": "#0ca678", "building": "#a1887f"}
DETAIL_RINGS = 5


def _group_key(way):
    return way["kind"] if way["kind"] in VERTICAL_KINDS else "walkway"


def _focus_window(data, margin):
    """(x0, x1, y0, y1) to crop context to: a margin around the path if there is
    one, else the full drawn footprint."""
    path = data["path"]
    if path.get("found") and path.get("points"):
        xs = [p[0] for p in path["points"]]
        ys = [p[1] for p in path["points"]]
    else:
        bb = data["meta"].get("bbox") or {"min_x": -1, "max_x": 1, "min_y": -1, "max_y": 1}
        xs = [bb["min_x"], bb["max_x"]]
        ys = [bb["min_y"], bb["max_y"]]
    return (min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin)


def _clip_runs(points, win):
    """Split a polyline into the contiguous runs that fall inside win, so a
    430 m platform contributes only the stretch near the path."""
    x0, x1, y0, y1 = win
    runs, cur = [], []
    for p in points:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            cur.append(p)
        elif len(cur) >= 2:
            runs.append(cur)
            cur = []
        else:
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def _batch_lines(runs, exag):
    xs, ys, zs = [], [], []
    for pts in runs:
        for x, y, z in pts:
            xs.append(x)
            ys.append(y)
            zs.append(z * exag)
        xs.append(None)
        ys.append(None)
        zs.append(None)
    return xs, ys, zs


def _level_label(v):
    return f"L{int(v)}" if float(v).is_integer() else f"L{v:g}"


def _add_level_planes(fig, data, win, exag):
    """A faint slab + labelled outline at each level, sized to the focus window.
    This is what makes the vertical structure legible."""
    meta = data["meta"]
    levels = meta.get("levels_present") or []
    if not levels:
        return
    x0, x1, y0, y1 = win
    floor = meta.get("floor_height_m", 4.0)

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
        # Stack labels at one back corner so they read like floor numbers up
        # the side of a shaft. A short vertical stem ties them to their slab.
        lx.append(x0)
        ly.append(y0)
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
        return
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


def _add_details(fig, data, exag):
    """Add the details layer (buildings as faint outlines, POIs as labelled
    markers coloured by category), bucketed into distance rings so a slider can
    reveal them progressively. Returns the ring index of each trace added (for
    the slider's visibility steps) and the ring width in metres."""
    details = data.get("details") or []
    radius = data["meta"].get("detail_radius_m") or 0
    if not details or radius <= 0:
        return [], 0.0
    width = radius / DETAIL_RINGS

    def ring_of(d):
        return min(DETAIL_RINGS - 1, int(d["dist"] // width))

    rings = []
    for r in range(DETAIL_RINGS):
        items = [d for d in details if ring_of(d) == r]
        if not items:
            continue
        # building footprints as faint outlines
        bx, by, bz = [], [], []
        for d in items:
            if d["kind"] != "building":
                continue
            for x, y, z in d["points"]:
                bx.append(x)
                by.append(y)
                bz.append(z * exag)
            bx.append(None)
            by.append(None)
            bz.append(None)
        if bx:
            fig.add_trace(go.Scatter3d(x=bx, y=by, z=bz, mode="lines",
                                       line=dict(color=DETAIL_COLOR["building"], width=1.5),
                                       opacity=0.55, hoverinfo="skip", showlegend=False))
            rings.append(r)
        # POI points (+ named building centroids), coloured by category. Names
        # are on hover/tap, not always-on labels -- a major station packs
        # hundreds of shops too densely for readable static text.
        px, py, pz, pc, pt = [], [], [], [], []
        for d in items:
            if d["kind"] == "building":
                if not d.get("name"):
                    continue
                pts = d["points"]
                x = sum(p[0] for p in pts) / len(pts)
                y = sum(p[1] for p in pts) / len(pts)
                z = pts[0][2]
            else:
                x, y, z = d["xyz"]
            px.append(x)
            py.append(y)
            pz.append(z * exag)
            pc.append(DETAIL_COLOR.get(d["category"], "#868e96"))
            pt.append(d.get("name") or d.get("subtype") or d["category"])
        if px:
            fig.add_trace(go.Scatter3d(x=px, y=py, z=pz, mode="markers",
                                       marker=dict(size=5, color=pc),
                                       hovertext=pt, hoverinfo="text", showlegend=False))
            rings.append(r)
    return rings, width


def build_figure(data, exag: float, margin: float) -> go.Figure:
    meta, path = data["meta"], data["path"]
    win = _focus_window(data, margin)
    fig = go.Figure()

    _add_level_planes(fig, data, win, exag)

    grouped = {}
    for w in data["ways"]:
        key = _group_key(w)
        for run in _clip_runs(w["points"], win):
            grouped.setdefault(key, []).append(run)
    for key, color, width, label in GROUPS:
        runs = grouped.get(key)
        if not runs:
            continue
        xs, ys, zs = _batch_lines(runs, exag)
        fig.add_trace(go.Scatter3d(x=xs, y=ys, z=zs, mode="lines",
                                   line=dict(color=color, width=width),
                                   hoverinfo="skip", name=label))

    if path.get("found"):
        pts = path["points"]
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in pts], y=[p[1] for p in pts], z=[p[2] * exag for p in pts],
            mode="lines", line=dict(color=PATH_COLOR, width=8),
            hoverinfo="skip", name="path"))
        _add_transitions(fig, path, exag)
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
        subtitle = f"{path['walking_time_seconds']:g}s · {path['walking_distance_meters']:g}m"
    else:
        subtitle = f"NO PATH ({path.get('reason')})"

    # Category colour key for the details layer (always in the legend so the
    # hover-for-name dots are still interpretable at a glance).
    if data.get("details"):
        present = [c for c in ("shop", "amenity", "tourism", "office", "leisure", "building")
                   if any(d["category"] == c for d in data["details"])]
        for c in present:
            fig.add_trace(go.Scatter3d(x=[None], y=[None], z=[None], mode="markers",
                                       marker=dict(size=7, color=DETAIL_COLOR[c]),
                                       name=c, hoverinfo="skip"))

    # Details layer + slider. Everything drawn so far is "core" and stays visible
    # at every slider step; detail traces switch on ring by ring as you drag out.
    n_core = len(fig.data)
    detail_rings, ring_width = _add_details(fig, data, exag)
    sliders = []
    if detail_rings:
        active = 1  # default: show just the nearest ring; drag out for more, or off
        for i, rg in enumerate(detail_rings):
            fig.data[n_core + i].visible = rg < active
        steps = [
            dict(method="restyle", label=("off" if s == 0 else f"{int(ring_width * s)}m"),
                 args=[{"visible": [True] * n_core + [rg < s for rg in detail_rings]}])
            for s in range(DETAIL_RINGS + 1)
        ]
        sliders = [dict(active=active, x=0.5, xanchor="center", y=0.0, len=0.6,
                        pad=dict(b=4, t=2), steps=steps,
                        currentvalue=dict(prefix="nearby detail ≤ ", font=dict(size=11)))]

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
            camera=dict(eye=dict(x=1.25, y=1.25, z=0.9), up=dict(x=0, y=0, z=1),
                        projection=dict(type="orthographic")),
        ),
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=0.12 if sliders else 0.02,
                    xanchor="center", x=0.5, itemsizing="constant", font=dict(size=11),
                    bgcolor="rgba(255,255,255,0.65)"),
        sliders=sliders,
        margin=dict(l=0, r=0, t=8, b=70 if sliders else 8),
        template="plotly_white", paper_bgcolor="white",
    )
    fig._viz_subtitle = subtitle  # picked up by main() for the overlay header
    return fig


def _header_html(meta, subtitle, exag):
    floor = meta.get("floor_height_m", 4.0)
    station = meta.get("station_name") or f"relation {meta['relation_id']}"
    caveat = (f"Z = OSM level ×{exag:g} · nominal ~{floor:g} m/floor, not real elevation"
              f" · {meta.get('context_mode', '')}")
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
    ap.add_argument("--margin", type=float, default=45.0, help="metres of context to keep around the path")
    ap.add_argument("--out", default=None, help="output html (default: alongside the json)")
    ap.add_argument("--open", action="store_true", help="open in the browser when done")
    args = ap.parse_args()

    with open(args.json) as f:
        data = json.load(f)

    fig = build_figure(data, args.z_exag, args.margin)
    out = args.out or os.path.splitext(args.json)[0] + ".html"
    write_html(fig, out, data["meta"], fig._viz_subtitle, args.z_exag, args.open)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
