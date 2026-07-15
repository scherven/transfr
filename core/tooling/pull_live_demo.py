"""
Runnable demo of the live data-process pipeline feeding the untouched algorithm.

    .venv/bin/python core/pull_live_demo.py

Pulls REAL data from public APIs (departures + platform tracks), attempts the
real DB coach-formation feed, and runs the seat -> point -> transfer routing on
a real DB Wagenreihung payload. Prints what it gets; degrades cleanly when the
geo-blocked formation host can't be reached.
"""

import json
import os
import sys

# core/ root plus the pathfinding submodule (graph/... moved there in the reorg).
_C = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # core/
for _p in (_C, *(os.path.join(_C, _d) for _d in ("pathfinding", "boarding", "tooling"))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from seat import find_path_from_seat  # noqa: E402
from graph import WALKING_SPEED_MS, haversine_meters  # noqa: E402
from live_sources import (  # noqa: E402
    FormationUnavailable,
    fetch_db_departures,
    fetch_db_formation,
    fetch_transitous_plan,
    long_distance,
    parse_transitous_platforms,
    parse_wagenreihung,
    straight_geometry_for,
)

FRA = ("50.1070", "8.6634")   # Frankfurt(Main)Hbf
KOELN = ("50.9430", "6.9583")
FIX = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")


def rule(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def main():
    import datetime

    rule("1. LIVE departures — dbf.finalrewind.org (DB IRIS)  [REACHABLE]")
    deps = fetch_db_departures("8000105")
    ld = long_distance(deps)
    print(f"{len(deps)} departures, {len(ld)} long-distance. Next few:")
    for d in ld[:6]:
        delay = f" (+{d.delay_minutes})" if d.delay_minutes else ""
        print(f"  {d.train:9} dep {d.scheduled_departure}{delay:6} platform {d.platform:>3}  -> {d.destination}")

    rule("2. LIVE platform tracks — api.transitous.org (MOTIS 2)  [REACHABLE]")
    when = (datetime.datetime.utcnow() + datetime.timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    legs = parse_transitous_platforms(fetch_transitous_plan(*FRA, *KOELN, when))
    for l in legs:
        print(f"  {l.mode:15} {str(l.line):>4}: {l.from_name} [track {l.from_track}] -> {l.to_name} [track {l.to_track}]")

    rule("3. LIVE coach formation — DB Wagenreihung  [GEO-BLOCKED from here]")
    if ld:
        d = ld[0]
        when_wr = "20260712" + (d.scheduled_departure or "0000").replace(":", "")
        try:
            nf = fetch_db_formation(d.train_number, when_wr)
            print(f"  got REAL formation for {d.train}: {len(nf.placements)} coaches")
        except FormationUnavailable as e:
            print(f"  {d.train}: {e}")

    rule("4. Real DB Wagenreihung payload -> algorithm  [seat -> point -> transfer]")
    with open(os.path.join(FIX, "wagenreihung_ice124.json"), encoding="utf-8") as f:
        nf = parse_wagenreihung(json.load(f))
    print(f"  train {nf.train_id} at {nf.station} platform {nf.track}: "
          f"coaches {nf.coach_ids()}  (metres: {nf.has_metres()})")
    tf = nf.to_train_formation(402.0)
    geom = straight_geometry_for(402.0)

    # one station exit ~70 m west of the A-end, so transfer == metres-from-A + rest
    coords = dict(geom.coords)
    exit_node = -1
    base = geom.coords[geom.nodes[0]]
    coords[exit_node] = (base[0], base[1] - 0.001)
    graph = {}
    ids = geom.nodes
    for a, b in list(zip(ids, ids[1:])) + [(ids[0], exit_node)]:
        w = haversine_meters(*coords[a], *coords[b]) / WALKING_SPEED_MS
        graph.setdefault(a, []).append((b, w, None))
        graph.setdefault(b, []).append((a, w, None))

    print(f"  {'coach':>5} {'class':>5} {'seat':>4} {'door@m':>7} {'transfer m':>10} {'walk s':>7}")
    for coach in ("11", "13", "15", "18"):
        cls = next(p.travel_class for p in nf.placements if p.coach == coach)
        r = find_path_from_seat(graph, coords, tf, geom, coach, 30, {exit_node})
        print(f"  {coach:>5} {str(cls):>5} {30:>4} {r['alighting_offset_m']:>7.1f} "
              f"{r['walking_distance_meters']:>10.1f} {r['walking_time_seconds']:>7.1f}")


if __name__ == "__main__":
    main()
