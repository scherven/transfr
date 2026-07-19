"""List the stations a platform-label harvest could improve, most-improvable first.

DB-only, no network. For every train station (railway=station, optionally halt) in
a region, count the platforms OSM already labels (a platform way carrying ref /
local_ref) vs the ones it leaves blank nearby. A station with blank platforms is a
harvest candidate; the blank count is its priority. The output both sizes the job
(how many stations, how many calls) and is the target list for
core/dbgen/harvest_platform_labels.py.

Coverage here is a proxy -- it counts platform *ways*; a station whose platforms
are mapped only as stop nodes reads as "0 platforms" (still a candidate, correctly).
And "has >=1 label" is not "fully labelled": a hub with 3 of 22 labelled still shows
unlabelled > 0 and ranks as a candidate, which is what we want.

Long-running (a spatial check per station, thousands of them), so it CHECKPOINTS to
a sidecar .progress.json after every batch, RESUMES on restart, and saves on Ctrl-C.

Run (from repo root, with the venv):
    .venv/bin/python -m core.dbgen.list_harvest_candidates --out harvest_candidates.csv
    # tune region / threshold / include small halts:
    .venv/bin/python -m core.dbgen.list_harvest_candidates \
        --bbox 45.8,2.5,53.7,17.2 --kinds station,halt --min-unlabeled 1
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
from typing import Dict, List, Optional, Tuple

from core.db import connect

# Default region: DACH + BeNeLux (where the feed publishes platform/track data).
DEFAULT_BBOX = (45.8, 2.5, 53.7, 17.2)  # min_lat, min_lon, max_lat, max_lon
# Neighbourhood around a station node to scan for its platforms (~300 m).
_DLAT, _DLON = 0.003, 0.005
_BATCH = 200  # checkpoint cadence (stations)


def _station_rows(cur, bbox, kinds) -> List[Tuple[int, str, float, float, str]]:
    """(node_id, name, lat, lon, kind) for every station node of `kinds` in bbox."""
    min_lat, min_lon, max_lat, max_lon = bbox
    cur.execute(
        "SELECT id, tags->>'name' AS name, lat, lon, tags->>'railway' AS kind "
        "FROM osm_nodes "
        "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
        "  AND tags->>'railway' = ANY(%s)",
        (min_lat, max_lat, min_lon, max_lon, list(kinds)),
    )
    return [(r["id"], r["name"], r["lat"], r["lon"], r["kind"]) for r in cur.fetchall()]


def _platform_counts(cur, lat: float, lon: float) -> Tuple[int, int]:
    """(labelled, total) platform ways whose geometry passes within ~300 m of
    (lat, lon). labelled = carries ref or local_ref."""
    cur.execute(
        "SELECT "
        "  count(DISTINCT w.id) AS total, "
        "  count(DISTINCT w.id) FILTER (WHERE w.tags ? 'ref' OR w.tags ? 'local_ref') AS labelled "
        "FROM osm_nodes n "
        "JOIN node_way_ids nw ON nw.node_id = n.id "
        "JOIN LATERAL unnest(nw.way_ids) wid ON true "
        "JOIN osm_ways w ON w.id = wid "
        "WHERE n.lat BETWEEN %s AND %s AND n.lon BETWEEN %s AND %s "
        "  AND (w.tags->>'railway' IN ('platform','platform_edge') "
        "       OR w.tags->>'public_transport' = 'platform')",
        (lat - _DLAT, lat + _DLAT, lon - _DLON, lon + _DLON),
    )
    row = cur.fetchone()
    return int(row["labelled"] or 0), int(row["total"] or 0)


def _load_progress(path: str) -> Dict[str, dict]:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            print(f"! could not read {path}; starting fresh", file=sys.stderr)
    return {}


def _save_progress(path: str, rows: Dict[str, dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, path)


def _write_csv(path: str, rows: List[dict], min_unlabelled: int) -> int:
    """Write candidates (unlabelled >= threshold), most-improvable first."""
    cands = [r for r in rows if r["unlabelled"] >= min_unlabelled]
    cands.sort(key=lambda r: (-r["unlabelled"], -r["total"], r["name"] or ""))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "lat", "lon", "kind", "platforms_total",
                    "platforms_labelled", "platforms_unlabelled"])
        for r in cands:
            w.writerow([r["name"], r["lat"], r["lon"], r["kind"],
                        r["total"], r["labelled"], r["unlabelled"]])
    return len(cands)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="List stations a platform-label harvest could improve.")
    ap.add_argument("--out", default="harvest_candidates.csv", help="output CSV")
    ap.add_argument("--bbox", default=None, help="min_lat,min_lon,max_lat,max_lon (default DACH+BeNeLux)")
    ap.add_argument("--kinds", default="station", help="comma list of railway values: station[,halt]")
    ap.add_argument("--min-unlabeled", type=int, default=1,
                    help="only list stations with at least this many unlabelled platforms")
    args = ap.parse_args(argv)

    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else DEFAULT_BBOX
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    progress_path = args.out + ".progress.json"

    conn = connect()
    cur = conn.cursor()
    print(f"Enumerating {kinds} nodes in bbox {bbox} ...", flush=True)
    stations = _station_rows(cur, bbox, kinds)
    print(f"{len(stations)} station nodes to check.\n", flush=True)

    done = _load_progress(progress_path)
    if done:
        print(f"Resuming: {len(done)} already checked.\n", flush=True)

    # Save-and-exit on Ctrl-C without losing the batch in flight.
    interrupted = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: interrupted.__setitem__("flag", True))

    checked = 0
    for nid, name, lat, lon, kind in stations:
        key = str(nid)
        if key in done:
            continue
        labelled, total = _platform_counts(cur, lat, lon)
        done[key] = {"name": name, "lat": lat, "lon": lon, "kind": kind,
                     "labelled": labelled, "total": total, "unlabelled": total - labelled}
        checked += 1
        if checked % _BATCH == 0:
            _save_progress(progress_path, done)
            print(f"  checked {checked} ({len(done)}/{len(stations)} total)", flush=True)
        if interrupted["flag"]:
            _save_progress(progress_path, done)
            print(f"\n^C -- progress saved ({len(done)} checked). Re-run to resume.",
                  file=sys.stderr)
            return 130

    _save_progress(progress_path, done)
    n = _write_csv(args.out, list(done.values()), args.min_unlabeled)
    zero = sum(1 for r in done.values() if r["labelled"] == 0)
    total_unlabelled = sum(r["unlabelled"] for r in done.values() if r["unlabelled"] >= args.min_unlabeled)
    print(f"\nDone. {len(done)} stations checked; {n} are candidates "
          f"(>= {args.min_unlabeled} unlabelled platform), {zero} have NO labels at all.")
    print(f"~{total_unlabelled} unlabelled platforms across the candidates.")
    print(f"Candidates written to {args.out} (most-improvable first). "
          f"Delete {progress_path} to force a clean re-run.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
