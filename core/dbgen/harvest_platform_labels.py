"""Harvest platform labels (track number + real coordinate) for stations from the
Transitous / MOTIS feed -- the labels OpenStreetMap lacks, so the station map can
show every platform, not just the few OSM ref-tags.

Why this works: every MOTIS journey stop carries a `track` (the operator's
platform code) AND the quay's own `lat`/`lon`. Querying journeys from a station to
a geographic spread of destinations (and back) makes trains leave from most of its
platforms; we collect every (track -> coordinate) seen at the station. The result
is a marker overlay -- feed the labels straight onto the map at their coordinates,
no fragile matching to OSM polygons. It's the same feed data the Koeln transfer
recovery uses, gathered station-wide instead of per-transfer.

This is network-bound and slow (dozens of HTTP calls per station), so it
CHECKPOINTS after every station and RESUMES on restart (a station already in the
output file is skipped), and Ctrl-C saves progress before exiting -- you never
lose harvested work. It only needs `requests`.

Run from the repo root (with the venv):
    .venv/bin/python -m core.dbgen.harvest_platform_labels --out platform_labels.json
    # just one station:
    .venv/bin/python -m core.dbgen.harvest_platform_labels --station "Zürich HB" --out zh.json
    # resume after a Ctrl-C: run the same command again -- done stations are skipped.

Output shape (consumed by api/platform_labels.py -> /station-platform-markers):
    {
      "Zürich HB": {
        "lat": 47.3777, "lon": 8.5401,
        "platforms": [ {"track": "3", "lat": 47.37835, "lon": 8.53621, "n": 4}, ... ]
      },
      ...
    }
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import requests

PLAN_URL = "https://api.transitous.org/api/v5/plan"
_HEADERS = {"Accept": "application/json",
            "User-Agent": "transfr/0.1 (+https://github.com/scherven/transfr)"}
_WALK_MODES = {"WALK", "BIKE", "CAR", "BIKE_SHARING", "CAR_SHARING", "SCOOTER_SHARING"}

# A stop in a journey belongs to the target station iff its coordinate is within
# this of the target centroid. A big station's quays sit ~100-150 m from the
# centroid, so keep it generous but under the gap to a neighbouring station.
STATION_RADIUS_M = 700.0

# Stations to harvest (name must match the feed's stop name). Edit / extend freely;
# --station filters to one. These are major DACH/CH hubs whose OSM platform labels
# are sparse; the feed covers platform data across DE/CH/AT/BE/NL.
TARGETS: List[Dict] = [
    {"name": "Zürich HB", "lat": 47.377670, "lon": 8.540100},
    {"name": "Bern", "lat": 46.948830, "lon": 7.439130},
    {"name": "Basel SBB", "lat": 47.547407, "lon": 7.589551},
    {"name": "Genève", "lat": 46.210270, "lon": 6.142570},
    {"name": "Luzern", "lat": 47.050280, "lon": 8.310130},
    {"name": "München Hbf", "lat": 48.140232, "lon": 11.558335},
    {"name": "Frankfurt (Main) Hauptbahnhof", "lat": 50.107145, "lon": 8.663789},
    {"name": "Köln Hbf", "lat": 50.943033, "lon": 6.958729},
]

# Probe destinations -- a geographic spread so journeys leave the target in every
# direction, exercising most platform faces. Include a couple of nearby/regional
# stops to reach S-Bahn / suburban / underground platforms (mainline hubs alone
# tend to miss them). Extend for a specific station's local lines.
DESTINATIONS: List[Dict] = [
    {"name": "Bern", "lat": 46.948830, "lon": 7.439130},
    {"name": "Basel SBB", "lat": 47.547407, "lon": 7.589551},
    {"name": "Genève", "lat": 46.210270, "lon": 6.142570},
    {"name": "Luzern", "lat": 47.050280, "lon": 8.310130},
    {"name": "Chur", "lat": 46.852970, "lon": 9.528970},
    {"name": "St. Gallen", "lat": 47.423220, "lon": 9.369940},
    {"name": "Winterthur", "lat": 47.500370, "lon": 8.723980},
    {"name": "Lugano", "lat": 46.005420, "lon": 8.947110},
    {"name": "Milano Centrale", "lat": 45.486790, "lon": 9.204220},
    {"name": "München Hbf", "lat": 48.140232, "lon": 11.558335},
    {"name": "Stuttgart Hbf", "lat": 48.784084, "lon": 9.181635},
    {"name": "Frankfurt (Main) Hbf", "lat": 50.107145, "lon": 8.663789},
    {"name": "Paris Est", "lat": 48.876839, "lon": 2.359392},
    {"name": "Wien Hbf", "lat": 48.185184, "lon": 16.376974},
    {"name": "Berlin Hbf", "lat": 52.525592, "lon": 13.369545},
    {"name": "Hamburg Hbf", "lat": 53.552736, "lon": 10.006909},
]


def _haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _plan(session: requests.Session, frm: Tuple[float, float], to: Tuple[float, float],
          when: str, n: int, timeout: float) -> Optional[dict]:
    try:
        r = session.get(PLAN_URL, params={
            "fromPlace": f"{frm[0]},{frm[1]}", "toPlace": f"{to[0]},{to[1]}",
            "time": when, "numItineraries": str(n),
        }, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # network hiccup on one query must not abort the run
        print(f"    ! plan failed ({e.__class__.__name__}); skipping", file=sys.stderr)
        return None


def _collect_station_stops(data: dict, target: Dict,
                           into: Dict[str, List[Tuple[float, float]]]) -> None:
    """Add every (track -> coord) at the target station found anywhere in a plan
    response (leg endpoints + intermediate stops), matched by proximity."""
    tc = (target["lat"], target["lon"])

    def consider(place: Optional[dict]) -> None:
        if not place:
            return
        track, lat, lon = place.get("track"), place.get("lat"), place.get("lon")
        if track is None or lat is None or lon is None:
            return
        if _haversine_m(tc, (lat, lon)) > STATION_RADIUS_M:
            return
        into.setdefault(str(track), []).append((lat, lon))

    for itin in data.get("itineraries", []):
        for leg in itin.get("legs", []):
            if leg.get("mode") in _WALK_MODES:
                continue
            consider(leg.get("from"))
            consider(leg.get("to"))
            for s in leg.get("intermediateStops") or []:
                consider(s)


def harvest_station(session: requests.Session, target: Dict, when: str,
                    n: int, sleep_s: float, timeout: float) -> Dict:
    """All (track -> coord) at one station, from journeys to/from every destination
    that isn't the station itself. Coordinate returned per track is the median of
    its observations (robust to the occasional off quay), with the observation
    count `n` as a confidence signal."""
    tc = (target["lat"], target["lon"])
    obs: Dict[str, List[Tuple[float, float]]] = {}
    dests = [d for d in DESTINATIONS if _haversine_m(tc, (d["lat"], d["lon"])) > 2000]
    for i, dest in enumerate(dests):
        dc = (dest["lat"], dest["lon"])
        for frm, to in ((tc, dc), (dc, tc)):
            data = _plan(session, frm, to, when, n, timeout)
            if data:
                _collect_station_stops(data, target, obs)
            time.sleep(sleep_s)
        print(f"    [{i + 1}/{len(dests)}] via {dest['name']}: "
              f"{len(obs)} tracks so far", flush=True)

    platforms = []
    for track, coords in sorted(obs.items(), key=lambda kv: (len(kv[0]), kv[0])):
        platforms.append({
            "track": track,
            "lat": round(_median([c[0] for c in coords]), 6),
            "lon": round(_median([c[1] for c in coords]), 6),
            "n": len(coords),
        })
    return {"lat": target["lat"], "lon": target["lon"], "platforms": platforms}


def _load(path: str) -> Dict[str, Dict]:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            print(f"! could not read {path}; starting fresh", file=sys.stderr)
    return {}


def _save(path: str, data: Dict[str, Dict]) -> None:
    """Atomic write so a Ctrl-C mid-save never corrupts the checkpoint."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest platform labels from the MOTIS feed.")
    ap.add_argument("--out", default="platform_labels.json", help="output/checkpoint JSON file")
    ap.add_argument("--station", default=None, help="harvest only this target (by name)")
    ap.add_argument("--when", default="2026-07-20T09:00:00+02:00",
                    help="departure time (a weekday daytime gives the fullest board)")
    ap.add_argument("--itineraries", type=int, default=6, help="itineraries per plan query")
    ap.add_argument("--sleep", type=float, default=0.3, help="seconds between HTTP calls")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request timeout seconds")
    args = ap.parse_args(argv)

    targets = TARGETS
    if args.station:
        targets = [t for t in TARGETS if t["name"] == args.station]
        if not targets:
            print(f"no target named {args.station!r}; known: {[t['name'] for t in TARGETS]}",
                  file=sys.stderr)
            return 2

    out = _load(args.out)
    session = requests.Session()
    session.headers.update(_HEADERS)

    print(f"Harvesting {len(targets)} station(s) -> {args.out} "
          f"({len(out)} already done, will skip)\n")
    try:
        for t in targets:
            if t["name"] in out:
                print(f"= {t['name']}: already harvested ({len(out[t['name']]['platforms'])} "
                      f"platforms) -- skipping")
                continue
            print(f"> {t['name']} ({t['lat']}, {t['lon']}):")
            result = harvest_station(session, t, args.when, args.itineraries,
                                     args.sleep, args.timeout)
            out[t["name"]] = result
            _save(args.out, out)  # checkpoint after every station
            tracks = [p["track"] for p in result["platforms"]]
            print(f"  = {len(tracks)} platforms: {tracks}\n")
    except KeyboardInterrupt:
        _save(args.out, out)
        print(f"\n^C -- progress saved to {args.out} "
              f"({len(out)} stations done). Re-run to resume.", file=sys.stderr)
        return 130

    _save(args.out, out)
    print(f"Done. {len(out)} stations in {args.out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
