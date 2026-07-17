"""Bulk-ingest platform labels from national GTFS feeds into the station-map
overlay (platform_labels.json) -- the complete, scalable alternative to the
journey harvest.

Every national feed publishes, in stops.txt, each platform as a stop with a
`platform_code` + coordinate under a parent station (location_type=1). One
download + parse per country therefore yields every platform the operator
labels -- no per-journey API calls, no rate limit, and it's exhaustive rather
than "whatever came up in sampled journeys". (This is the same source the live
feed serves tracks from; see mfdz GTFS-Issues #230 for the known DELFI
`platform_code` quirks -- e.g. Koeln's 88/89 -- which is exactly what the app's
coordinate-snap recovery already handles.)

Rail filter: national feeds carry all modes (bus/tram too), so by default we keep
only stations that sit within --rail-radius of an OSM railway=station/halt (loaded
once from transfr_eu) -- i.e. the rail stations the app's map actually resolves.
Cross-feed duplicates (a border station in two feeds) merge under that one OSM
station. Pass --no-rail-filter to keep every GTFS station with a platform_code.

Long-running (big downloads + parses), so it CHECKPOINTS the merged overlay after
every feed, caches downloads (skips a feed already downloaded), and saves on Ctrl-C.

Run from the repo root (with the venv):
    .venv/bin/python -m core.dbgen.ingest_gtfs_platforms --out platform_labels.json
    # one country / a cheap validation feed:
    .venv/bin/python -m core.dbgen.ingest_gtfs_platforms --only be --out be.json

Feeds (static GTFS zips, from the Transitous feed registry):
    be  SNCB (Belgium, rail-only, ~23 MB)         -- good for a first run
    nl  OpenOV (Netherlands, all modes, ~270 MB)
    ch  opentransportdata.swiss (all modes, large)
    de  DELFI (Germany, all modes, ~1 GB+)
    at  is split per Verkehrsverbund -- add the rail dataset URL(s) below by hand.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import signal
import sys
import zipfile
from typing import Dict, List, Optional, Tuple

import requests

from core.db import connect

FEEDS: Dict[str, str] = {
    "be": "https://sncb-opendata.hafas.de/gtfs/static/c21ac6758dd25af84cca5b707f3cb3de",
    "nl": "https://gtfs.openov.nl/gtfs-rt/gtfs-openov-nl.zip",
    "ch": "https://data.opentransportdata.swiss/de/dataset/timetable-2026-gtfs2020/permalink",
    "de": ("https://www.opendata-oepnv.de/ht/de/datensaetze/sharing?"
           "tx_vrrkit_view%5Bsharing%5D=eyJkYXRhc2V0IjoiZGV1dHNjaGxhbmR3ZWl0ZS1zb2xsZmFocnBsYW5kYXRlbi1ndGZzIn0"),
    # at: national feed is fragmented across Verkehrsverbuende -- add the rail
    # dataset URLs from https://data.mobilitaetsverbuende.at as needed.
}

# Load OSM rail stations within this generous box (DACH+BeNeLux and a margin).
OSM_BBOX = (45.0, 2.0, 55.5, 18.0)  # min_lat, min_lon, max_lat, max_lon
DEFAULT_RAIL_RADIUS_M = 250.0
_M_PER_DEG = 111_320.0
_CHUNK = 1 << 20  # 1 MiB download chunk


def _haversine_m(la1: float, lo1: float, la2: float, lo2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


# ---------------------------------------------------------------------------
# OSM rail-station index (the rail filter + the stable merge key)
# ---------------------------------------------------------------------------

class RailIndex:
    """OSM railway=station/halt nodes, in a ~1 km grid for fast nearest lookup."""

    CELL = 0.01  # ~1.1 km latitude

    def __init__(self, stations: List[Tuple[str, float, float]]):
        self.grid: Dict[Tuple[int, int], List[Tuple[str, float, float]]] = {}
        for name, lat, lon in stations:
            self.grid.setdefault((int(lat / self.CELL), int(lon / self.CELL)), []).append((name, lat, lon))

    def nearest(self, lat: float, lon: float, radius_m: float) -> Optional[Tuple[str, float, float]]:
        ci, cj = int(lat / self.CELL), int(lon / self.CELL)
        best, best_d = None, radius_m
        for i in (ci - 1, ci, ci + 1):
            for j in (cj - 1, cj, cj + 1):
                for name, slat, slon in self.grid.get((i, j), ()):
                    d = _haversine_m(lat, lon, slat, slon)
                    if d <= best_d:
                        best, best_d = (name, slat, slon), d
        return best


def load_rail_index(conn, bbox) -> RailIndex:
    min_lat, min_lon, max_lat, max_lon = bbox
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tags->>'name' AS name, lat, lon FROM osm_nodes "
            "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
            "  AND tags->>'railway' IN ('station','halt')",
            (min_lat, max_lat, min_lon, max_lon),
        )
        rows = [(r["name"], r["lat"], r["lon"]) for r in cur.fetchall() if r["name"]]
    print(f"  loaded {len(rows)} OSM rail stations for the filter/merge")
    return RailIndex(rows)


# ---------------------------------------------------------------------------
# GTFS parse
# ---------------------------------------------------------------------------

def _read_csv(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def _plausible_track(code: str) -> bool:
    """Reject placeholder platform_codes some feeds emit (SNCB's "TE BEPAL" = 'to
    be determined', "?", spelled-out words). A real track is a short number
    (optionally a letter/compound suffix: '3a', '41/42') or a lone letter ('A')."""
    c = code.strip()
    if not c or " " in c or len(c) > 6:
        return False
    if any(ch.isdigit() for ch in c):
        return True
    return len(c) <= 2 and c.isalpha()


def parse_stops(zip_path: str) -> List[dict]:
    """Return one dict per platform: {station, name, code, lat, lon}. A platform is
    a stop carrying a non-empty platform_code; `station` is its parent's name (or
    its own if it has no parent). Coordinates are the platform's own."""
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        if "stops.txt" not in names:
            raise SystemExit(f"{zip_path}: no stops.txt")
        rows = list(_read_csv(zf, "stops.txt"))

    stations: Dict[str, str] = {}
    for r in rows:
        if (r.get("location_type") or "0").strip() == "1":
            stations[r["stop_id"]] = (r.get("stop_name") or "").strip()

    platforms: List[dict] = []
    for r in rows:
        code = (r.get("platform_code") or "").strip()
        if not _plausible_track(code):
            continue
        try:
            lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
        except (KeyError, ValueError):
            continue
        parent = (r.get("parent_station") or "").strip()
        name = stations.get(parent) or (r.get("stop_name") or "").strip()
        platforms.append({"station": name, "code": code, "lat": lat, "lon": lon})
    return platforms


# ---------------------------------------------------------------------------
# Download (cached, resumable-per-feed)
# ---------------------------------------------------------------------------

def download(url: str, dest: str) -> str:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"  cached: {dest} ({os.path.getsize(dest) // (1 << 20)} MB)")
        return dest
    print(f"  downloading {url}")
    tmp = dest + ".part"
    with requests.get(url, stream=True, timeout=60,
                      headers={"User-Agent": "transfr/0.1 (+github.com/scherven/transfr)"}) as r:
        r.raise_for_status()
        got = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(_CHUNK):
                f.write(chunk)
                got += len(chunk)
                if got % (32 << 20) < _CHUNK:
                    print(f"    {got // (1 << 20)} MB", flush=True)
    os.replace(tmp, dest)
    print(f"  saved {dest} ({os.path.getsize(dest) // (1 << 20)} MB)")
    return dest


# ---------------------------------------------------------------------------
# Merge into the overlay
# ---------------------------------------------------------------------------

def merge_feed(platforms: List[dict], rail: Optional[RailIndex], radius_m: float,
               overlay: Dict[str, dict]) -> Tuple[int, int]:
    """Fold one feed's platforms into `overlay` (keyed by OSM station name when the
    rail filter is on, else the GTFS station name). Dedupe platforms by track within
    a station. Returns (platforms_kept, stations_touched)."""
    kept, touched = 0, set()
    for p in platforms:
        if rail is not None:
            match = rail.nearest(p["lat"], p["lon"], radius_m)
            if match is None:
                continue  # not near any OSM rail station -> bus/tram, drop
            key, klat, klon = match
        else:
            key = p["station"] or f"{round(p['lat'],4)},{round(p['lon'],4)}"
            klat, klon = p["lat"], p["lon"]
        entry = overlay.setdefault(key, {"lat": klat, "lon": klon, "_seen": {}})
        if p["code"] not in entry["_seen"]:
            entry["_seen"][p["code"]] = {"track": p["code"],
                                         "lat": round(p["lat"], 6), "lon": round(p["lon"], 6)}
            kept += 1
        touched.add(key)
    return kept, len(touched)


def _finalize(overlay: Dict[str, dict]) -> Dict[str, dict]:
    """Drop the internal _seen index; emit sorted platform lists."""
    out = {}
    for name, e in overlay.items():
        plats = sorted(e["_seen"].values(), key=lambda m: (len(m["track"]), m["track"]))
        out[name] = {"lat": e["lat"], "lon": e["lon"], "platforms": plats}
    return out


def _save(path: str, overlay: Dict[str, dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_finalize(overlay), f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Bulk-ingest platform labels from national GTFS feeds.")
    ap.add_argument("--out", default="platform_labels.json", help="overlay output (also the checkpoint)")
    ap.add_argument("--only", default=None, help="comma list of feeds to run (default: all in FEEDS)")
    ap.add_argument("--cache-dir", default="gtfs_cache", help="where downloaded zips are kept")
    ap.add_argument("--rail-radius", type=float, default=DEFAULT_RAIL_RADIUS_M,
                    help="keep GTFS stations within this many m of an OSM rail station")
    ap.add_argument("--no-rail-filter", action="store_true",
                    help="keep every GTFS station with a platform_code (bus/tram included)")
    ap.add_argument("--local", default=None,
                    help="parse a local zip instead of downloading, as country=path[,country=path]")
    args = ap.parse_args(argv)

    feeds = list(FEEDS.items())
    if args.only:
        want = {c.strip() for c in args.only.split(",")}
        feeds = [(c, u) for c, u in feeds if c in want]
    if args.local:
        feeds = [(kv.split("=", 1)[0], "local:" + kv.split("=", 1)[1]) for kv in args.local.split(",")]

    os.makedirs(args.cache_dir, exist_ok=True)

    rail = None
    if not args.no_rail_filter:
        print("Loading OSM rail-station index ...")
        rail = load_rail_index(connect(), OSM_BBOX)

    overlay: Dict[str, dict] = {}
    if os.path.exists(args.out):  # resume: fold the existing overlay back in
        try:
            with open(args.out, encoding="utf-8") as f:
                for name, e in json.load(f).items():
                    overlay[name] = {"lat": e["lat"], "lon": e["lon"],
                                     "_seen": {m["track"]: m for m in e.get("platforms", [])}}
            print(f"Resuming from {args.out}: {len(overlay)} stations already present.\n")
        except (OSError, ValueError):
            pass

    interrupted = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: interrupted.__setitem__("flag", True))

    for country, url in feeds:
        print(f"> {country}:")
        try:
            path = url[6:] if url.startswith("local:") else download(url, os.path.join(args.cache_dir, f"{country}.zip"))
            platforms = parse_stops(path)
        except Exception as e:  # one bad feed must not lose the others
            print(f"  ! {country} failed: {e.__class__.__name__}: {e}", file=sys.stderr)
            continue
        kept, touched = merge_feed(platforms, rail, args.rail_radius, overlay)
        _save(args.out, overlay)  # checkpoint after every feed
        print(f"  {country}: {len(platforms)} platform rows -> kept {kept} at {touched} rail stations "
              f"({len(overlay)} in overlay)\n")
        if interrupted["flag"]:
            print(f"\n^C -- overlay saved to {args.out}. Re-run to resume.", file=sys.stderr)
            return 130

    _save(args.out, overlay)
    total = sum(len(e["_seen"]) for e in overlay.values())
    print(f"Done. {len(overlay)} stations, {total} platform labels -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
