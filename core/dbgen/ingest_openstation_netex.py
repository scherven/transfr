"""Ingest DB InfraGO's CC0 "OpenStation" NeTEx export into a coordinate->label +
accessibility overlay (openstation_labels.json) -- the German public-platform
crosswalk that fixes DELFI's renumbered track codes (Koeln Hbf "84-91" -> the real
Gleis 1-11) even where OpenStreetMap carries no label, and adds step-free / lift
accessibility as a fact on a platform.

The source is a single, CC0, no-auth bulk file:

    https://bahnhof.de/daten/netex     (redirects to a Mobilithek /noauth URL)
    ~18 MB gzip -> ~289 MB XML, NeTEx EPIP, participant "DB".

Per StopPlace (a station, DHID id like `de:05315:11201`) the file lists Quays.
Two kinds share one `<quays>` list:
  * the georeferenced PLATFORM quays -- Name "Bahnsteig Gleis 6/7", carrying an
    AccessibilityAssessment (StepFreeAccess / WheelchairAccess) and, via their
    `equipmentPlaces`, a lift/stair/escalator with a WGS84 `Centroid/Location`;
  * bare-number track quays -- Name just "6", "7" -- with NO coordinate.
Only the first kind is usable for a coordinate lookup, and only ~11% of all quays
are georeferenced at all (2,235 of 21,794 measured 2026-07-20), concentrated at the
hubs where the DELFI renumbering actually bites -- which is exactly where we need
it. Most georeferenced labels are ISLAND labels ("6/7"), so the overlay stores the
signed label as-is; api/transfers reconciles it with OSM's per-track sign (the
more specific "7" wins when it is a component of the island "6/7").

The `Length` on a quay is the platform's physical length, NOT a path length, and
the SitePathLinks carry no distance/duration/geometry -- so this file is NOT a
metric source. It is used ONLY for labels + accessibility (see [[map-source-
scraping-verdict]]).

Output overlay (keyed by station DHID), a sibling of platform_labels.json:

    {"de:05315:11201": {"name": "Koeln Hbf", "eva": "8000207",
        "lat": 50.9434, "lon": 6.9583,
        "quays": [{"public_label": "6/7", "lat": 50.9434381, "lon": 6.9585662,
                   "step_free": true, "wheelchair": true, "has_lift": true},
                  ...]}}

Small (~2.2k georeferenced quays -> a few hundred KB), so it is COMMITTED to the
repo like platform_labels.json; regenerate with this script when DB republishes.
Served lazily, mtime-cached and honest-empty, by api/openstation.py.

Licence: DB InfraGO "OpenStation" open data, CC0 1.0 (public domain). Credit is
not legally required but is given (ios AttributionsView) per the project's rule.

Long-running download, so it CACHES the gzip (skips a completed download), can
--resume by folding an existing overlay back in, CHECKPOINTS every few hundred
stations, and saves on Ctrl-C -- progress is never lost.

Run from the repo root (with the venv):
    .venv/bin/python -m core.dbgen.ingest_openstation_netex --out openstation_labels.json
    # parse a file already downloaded (skip the network):
    .venv/bin/python -m core.dbgen.ingest_openstation_netex --local netex.xml.gz --out openstation_labels.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import signal
import sys
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import requests

SOURCE_URL = "https://bahnhof.de/daten/netex"
_NS = "{http://www.netex.org.uk/netex}"
_CHUNK = 1 << 20  # 1 MiB
_CHECKPOINT_EVERY = 400  # stations between overlay checkpoints

# A quay's public track label, parsed out of its German Name ("Bahnsteig Gleis
# 6/7" -> "6/7", "Bahnsteig Gleis 1" -> "1", "Bahnsteig Gleise 2/3" -> "2/3").
_LABEL_RE = re.compile(
    r"(?:Gleise?|Bahnsteig)\s+([0-9]+[a-zA-Z]?(?:\s*/\s*[0-9]+[a-zA-Z]?)*)", re.IGNORECASE
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def public_label(name: Optional[str]) -> Optional[str]:
    """The bare track label from a quay Name, or None when the name carries no
    Gleis number (a bus bay, a mislabelled outdoor stub -- dropped)."""
    if not name:
        return None
    m = _LABEL_RE.search(name)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1))


def _text(el: Optional[ET.Element], path: str) -> Optional[str]:
    if el is None:
        return None
    x = el.find(path)
    return x.text.strip() if x is not None and x.text and x.text.strip() else None


def _name(el: ET.Element) -> Optional[str]:
    """A NeTEx <Name> may carry its text directly or in a nested <Text>."""
    n = el.find(f"{_NS}Name")
    if n is None:
        return None
    if n.text and n.text.strip():
        return n.text.strip()
    t = n.find(f"{_NS}Text")
    return t.text.strip() if t is not None and t.text and t.text.strip() else None


def _location(el: ET.Element) -> Optional[Tuple[float, float]]:
    loc = el.find(f"{_NS}Centroid/{_NS}Location")
    if loc is None:
        return None
    lat = _text(loc, f"{_NS}Latitude")
    lon = _text(loc, f"{_NS}Longitude")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


def _quay_coord(quay: ET.Element) -> Optional[Tuple[float, float]]:
    """The quay's WGS84 representative point: its own Centroid if it has one, else
    the mean of its equipment (lift/stair/escalator) Centroids. None when the quay
    is not georeferenced at all (the ~89% of quays we can't place)."""
    direct = _location(quay)
    if direct is not None:
        return direct
    pts = []
    for ep in quay.findall(f"{_NS}equipmentPlaces/{_NS}EquipmentPlace"):
        c = _location(ep)
        if c is not None:
            pts.append(c)
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _tri_bool(value: Optional[str]) -> Optional[bool]:
    """NeTEx accessibility flags are true/false/unknown; map to bool | None."""
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _limitation(el: ET.Element) -> Optional[ET.Element]:
    return el.find(f"{_NS}AccessibilityAssessment/{_NS}limitations/{_NS}AccessibilityLimitation")


def _has_lift(quay: ET.Element) -> bool:
    """True when the quay lists a lift among its equipment (a LiftEquipmentRef in
    an equipmentPlace or directly)."""
    return quay.find(f".//{_NS}LiftEquipmentRef") is not None


def _key_value(el: ET.Element, key: str) -> Optional[str]:
    for kv in el.findall(f"{_NS}keyList/{_NS}KeyValue"):
        if _text(kv, f"{_NS}Key") == key:
            return _text(kv, f"{_NS}Value")
    return None


def _station_key(elem: ET.Element) -> str:
    """The clean station DHID to group quays under. Prefer the ParentSiteRef (the
    `de:05315:11201` a StopPlace like `dhid:de:05315:11201:EdB` points at), else the
    StopPlace's own id stripped of the `dhid:` codespace and any trailing non-numeric
    sub-place segment (`:EdB`). Matches the DELFI/DHID ids the journey feed uses."""
    ref = elem.find(f"{_NS}ParentSiteRef")
    if ref is not None and ref.get("ref"):
        return re.sub(r"^[a-z0-9]+:", "", ref.get("ref"))
    raw = re.sub(r"^[a-z0-9]+:", "", elem.get("id", ""))
    # Drop a trailing alpha sub-place segment (":EdB"), keep numeric DHID parts.
    return re.sub(r":[A-Za-z][A-Za-z0-9]*$", "", raw)


def parse_stopplace(elem: ET.Element) -> Optional[dict]:
    """One StopPlace element -> {key, name, eva, quays:[...]} keeping only the
    georeferenced, Gleis-labelled quays (deduped by label). None when the station
    has none (nothing to place on a map / recover by coordinate).

    Step-free / wheelchair fall back to the StopPlace-level assessment when a quay
    carries none of its own -- the station's overall access is a fair default for a
    platform the operator didn't rate individually."""
    sp_lim = _limitation(elem)
    sp_step_free = _tri_bool(_text(sp_lim, f"{_NS}StepFreeAccess")) if sp_lim is not None else None
    sp_wheel = _tri_bool(_text(sp_lim, f"{_NS}WheelchairAccess")) if sp_lim is not None else None

    quays: Dict[str, dict] = {}
    for q in elem.findall(f".//{_NS}Quay"):
        label = public_label(_name(q))
        if label is None:
            continue
        coord = _quay_coord(q)
        if coord is None:
            continue  # not georeferenced -> unusable for a coordinate lookup
        if label in quays:
            continue  # first georeferenced quay for a label wins
        lim = _limitation(q)
        step_free = _tri_bool(_text(lim, f"{_NS}StepFreeAccess")) if lim is not None else None
        wheel = _tri_bool(_text(lim, f"{_NS}WheelchairAccess")) if lim is not None else None
        quays[label] = {
            "public_label": label,
            "lat": round(coord[0], 7),
            "lon": round(coord[1], 7),
            "step_free": sp_step_free if step_free is None else step_free,
            "wheelchair": sp_wheel if wheel is None else wheel,
            "has_lift": _has_lift(q),
        }
    if not quays:
        return None
    plats = sorted(quays.values(), key=lambda m: (len(m["public_label"]), m["public_label"]))
    clat = sum(p["lat"] for p in plats) / len(plats)
    clon = sum(p["lon"] for p in plats) / len(plats)
    return {
        "key": _station_key(elem),
        "name": _name(elem),
        "eva": _key_value(elem, "EVA"),
        "lat": round(clat, 7),
        "lon": round(clon, 7),
        "quays": plats,
    }


def _merge(overlay: Dict[str, dict], station: dict) -> None:
    """Fold one parsed station into the overlay under its DHID key, merging quays
    (by label) when a station complex spans several StopPlaces."""
    key = station["key"]
    existing = overlay.get(key)
    if existing is None:
        overlay[key] = {"name": station["name"], "eva": station["eva"],
                        "lat": station["lat"], "lon": station["lon"],
                        "quays": list(station["quays"])}
        return
    seen = {q["public_label"] for q in existing["quays"]}
    for q in station["quays"]:
        if q["public_label"] not in seen:
            existing["quays"].append(q)
            seen.add(q["public_label"])
    pts = existing["quays"]
    existing["lat"] = round(sum(p["lat"] for p in pts) / len(pts), 7)
    existing["lon"] = round(sum(p["lon"] for p in pts) / len(pts), 7)
    if not existing.get("name"):
        existing["name"] = station["name"]


def build_overlay(fileobj, overlay: Optional[Dict[str, dict]] = None,
                  on_progress=None, should_stop=None) -> Dict[str, dict]:
    """Stream-parse a NeTEx file object (a decompressed XML stream) into the
    overlay. `on_progress(n_stations)` is called periodically for checkpointing;
    `should_stop()` truthy stops early (Ctrl-C), returning what's parsed so far."""
    if overlay is None:
        overlay = {}
    n = 0
    for _event, elem in ET.iterparse(fileobj, events=("end",)):
        tag = _local(elem.tag)
        if tag == "StopPlace":
            station = parse_stopplace(elem)
            if station is not None:
                _merge(overlay, station)
                n += 1
                if on_progress is not None and n % _CHECKPOINT_EVERY == 0:
                    on_progress(n)
            elem.clear()
            if should_stop is not None and should_stop():
                break
        elif tag in ("SiteFrame", "CompositeFrame"):
            elem.clear()  # release the frame wrapper to bound memory
    return overlay


# ---------------------------------------------------------------------------
# Download (cached, redirect-following, resume-friendly)
# ---------------------------------------------------------------------------

def download(url: str, dest: str) -> str:
    """Fetch the gzip to `dest`, following the bahnhof.de -> Mobilithek /noauth
    redirects. Skips a completed download; writes to a .part file and renames on
    success so an interrupted download never leaves a truncated 'complete' file."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"  cached: {dest} ({os.path.getsize(dest) // (1 << 20)} MB)")
        return dest
    print(f"  downloading {url}")
    headers = {"User-Agent": "transfr/0.1 (+github.com/scherven/transfr)"}
    tmp = dest + ".part"
    with requests.get(url, stream=True, timeout=120, headers=headers, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(_CHUNK):
                f.write(chunk)
                got += len(chunk)
                if got % (8 << 20) < _CHUNK:
                    print(f"    {got // (1 << 20)} MB", flush=True)
    if total and os.path.getsize(tmp) != total:
        raise IOError(f"download truncated: got {os.path.getsize(tmp)} of {total} bytes")
    os.replace(tmp, dest)
    print(f"  saved {dest} ({os.path.getsize(dest) // (1 << 20)} MB)")
    return dest


def _save(path: str, overlay: Dict[str, dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(overlay, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _open_netex(path: str):
    """Open a NeTEx file for streaming, transparently handling gzip vs plain XML."""
    with open(path, "rb") as probe:
        magic = probe.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest DB OpenStation NeTEx -> label+a11y overlay.")
    ap.add_argument("--out", default="openstation_labels.json", help="overlay output (also the checkpoint)")
    ap.add_argument("--cache-dir", default="gtfs_cache", help="where the downloaded gzip is kept")
    ap.add_argument("--local", default=None, help="parse this local file instead of downloading")
    ap.add_argument("--resume", action="store_true",
                    help="fold an existing --out overlay back in before parsing (idempotent)")
    args = ap.parse_args(argv)

    overlay: Dict[str, dict] = {}
    if args.resume and os.path.exists(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                overlay = json.load(f)
            print(f"Resuming from {args.out}: {len(overlay)} stations already present.")
        except (OSError, ValueError):
            overlay = {}

    if args.local:
        path = args.local
    else:
        os.makedirs(args.cache_dir, exist_ok=True)
        path = download(SOURCE_URL, os.path.join(args.cache_dir, "openstation_netex.xml.gz"))

    interrupted = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: interrupted.__setitem__("flag", True))

    def checkpoint(n):
        _save(args.out, overlay)
        print(f"  checkpoint: {n} stations parsed, {len(overlay)} in overlay", flush=True)

    print(f"> parsing {path}")
    with _open_netex(path) as fh:
        build_overlay(fh, overlay, on_progress=checkpoint, should_stop=lambda: interrupted["flag"])

    _save(args.out, overlay)
    if interrupted["flag"]:
        print(f"\n^C -- overlay saved to {args.out}. Re-run with --resume to continue.", file=sys.stderr)
        return 130
    total = sum(len(e["quays"]) for e in overlay.values())
    print(f"Done. {len(overlay)} stations, {total} georeferenced platform labels -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
