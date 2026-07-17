"""DB IRIS platform enrichment (fills the S-Bahn / no_platform_data gaps that
MOTIS's DELFI feed omits).

Source: dbf.finalrewind.org/{eva}.json -- derf's IRIS wrapper (reachable, free,
no key). It carries a scheduledPlatform for essentially every DB departure,
including the S-Bahn platforms MOTIS drops.

Resolution chain:
  hub lat/lon --(nearest DE station in stations.csv with a db_id)--> EVA
  EVA --(IRIS board, cached)--> {line, destination} -> platform

Because IRIS realtime only spans now..+2h, we can't fetch a specific future
train's platform directly. Instead we harvest the current board into a
(line, destination) -> platform map and apply it to the itinerary's trains.
This is exact for the static-platform lines that matter (S-Bahn platforms are
fixed by line+direction and stable across the day); marked approximate elsewhere.
"""

from __future__ import annotations

import csv
import math
import os
import re
from typing import Dict, List, Optional, Tuple

import requests

# This file lives at agents/research/tight_connections/iris.py, i.e. four
# directory levels below the repo root, which is where stations.csv lives.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
STATIONS_CSV = os.path.join(REPO, "stations.csv")

_session = requests.Session()
_session.headers.update({"User-Agent": "transfr-research/0.1 (IRIS platform enrichment)"})

# ---- EVA resolution from stations.csv (db_id == EVA for DB stations) --------
# (lat, lon, eva, name, is_main, norm_tokens)
_eva_index: List[Tuple[float, float, str, str, bool, frozenset]] = []

_STOPWORDS = {"hbf", "bf", "bhf", "bahnhof", "hauptbahnhof", "s", "u", "su", "main",
              "berlin", "hamburg", "st", "der", "am", "an"}


def _norm_name(s: str) -> frozenset:
    s = s.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"\(.*?\)", " ", s)                 # drop parentheticals
    s = re.sub(r"hauptbahnhof|hbf", " hbf ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = {t for t in s.split() if t and t not in _STOPWORDS and len(t) > 2}
    return frozenset(toks)


def _load_eva_index() -> None:
    if _eva_index:
        return
    with open(STATIONS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            eva = (row.get("db_id") or "").strip()
            if not eva:
                continue
            lat, lon = (row.get("latitude") or "").strip(), (row.get("longitude") or "").strip()
            if not lat or not lon:
                continue
            try:
                _eva_index.append((float(lat), float(lon), eva, row["name"],
                                   row.get("is_main_station") == "t", _norm_name(row["name"])))
            except ValueError:
                continue


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def eva_for_coords(lat: float, lon: float, name: Optional[str] = None,
                   max_m: float = 1600.0) -> Optional[Tuple[str, str]]:
    """Resolve a hub to its DB EVA. Prefers, within range: a name-token match,
    then a main station, then proximity -- so a nearby bus stop or sub-platform
    with its own db_id doesn't outrank the real Hauptbahnhof."""
    _load_eva_index()
    want = _norm_name(name) if name else frozenset()
    cands = []
    for slat, slon, eva, sname, is_main, toks in _eva_index:
        if abs(slat - lat) > 0.04 or abs(slon - lon) > 0.06:
            continue
        d = _haversine_m(lat, lon, slat, slon)
        if d > max_m:
            continue
        name_hit = bool(want & toks)
        cands.append((0 if name_hit else 1, 0 if is_main else 1, d, eva, sname))
    if not cands:
        return None
    cands.sort()
    return cands[0][3], cands[0][4]


# ---- IRIS board fetch + parse (cached per EVA) -----------------------------
_board_cache: Dict[str, List[dict]] = {}


def _norm_line(s: Optional[str]) -> str:
    """MOTIS 'S8' / 'RE80' vs IRIS 'S S8' / 'RE 80' -> compact 'S8' / 'RE80'."""
    if not s:
        return ""
    return re.sub(r"\s+", "", str(s)).upper()


def board(eva: str) -> List[dict]:
    if eva in _board_cache:
        return _board_cache[eva]
    rows: List[dict] = []
    try:
        r = _session.get(f"https://dbf.finalrewind.org/{eva}.json", timeout=20)
        if r.status_code == 200:
            for t in r.json().get("departures", []):
                rows.append({
                    "line": _norm_line(t.get("train")),      # 'S S8' -> 'SS8'
                    "line_alt": _norm_line((t.get("trainClasses") and "") or t.get("train", "").split(" ")[-1]),
                    "dest": (t.get("destination") or "").strip(),
                    "platform": (t.get("scheduledPlatform") or t.get("platform") or "").strip() or None,
                    "delay": t.get("delayDeparture"),
                    "cancelled": bool(t.get("isCancelled")),
                })
    except requests.RequestException:
        pass
    _board_cache[eva] = rows
    return rows


def _line_matches(motis_line: str, iris_line: str) -> bool:
    """MOTIS 'S8' should match IRIS 'SS8' (class S + line S8) or 'S8'."""
    m, i = _norm_line(motis_line), iris_line
    if not m:
        return False
    return i == m or i.endswith(m) or i == ("S" + m if not m.startswith("S") else m)


def platform_for(eva: str, line: str, destination: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Platform for a (line, destination) at EVA, from the IRIS board.

    Returns (platform, how) where how in {exact, line_unique, line_major, none}:
      exact       -- matched the same line AND destination (direction-safe)
      line_unique -- the line uses a single platform here (direction-independent)
      line_major  -- the line's most common platform (approx; multi-platform line)
      none        -- no IRIS data for this line
    """
    rows = [b for b in board(eva) if b["platform"] and _line_matches(line, b["line"])]
    if not rows:
        return None, "none"
    if destination:
        dn = destination.lower()
        exact = [b for b in rows if b["dest"] and (dn in b["dest"].lower() or b["dest"].lower() in dn)]
        if exact:
            return exact[0]["platform"], "exact"
    plats = [b["platform"] for b in rows]
    uniq = set(plats)
    if len(uniq) == 1:
        return plats[0], "line_unique"
    major = max(uniq, key=plats.count)
    return major, "line_major"


def fill(lat: float, lon: float, name: Optional[str], line: Optional[str],
         headsign: Optional[str] = None) -> Tuple[Optional[str], str]:
    """One-shot: resolve a hub (coords + name) to its EVA and look up the
    departure platform for (line, terminus). Returns (platform, how). Non-DE
    hubs resolve to no EVA and return (None, 'no_eva') -- IRIS is DB-only."""
    if not line:
        return None, "no_line"
    ev = eva_for_coords(lat, lon, name)
    if not ev:
        return None, "no_eva"
    return platform_for(ev[0], line, headsign)
