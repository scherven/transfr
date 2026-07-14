"""
Tight-connection recovery experiment.

Question: does MOTIS (Transitous) drop train-to-train connections that are
actually makeable, because it assumes a conservative transfer time, when the
real platform-to-platform walk (from core/) is much shorter?

Two measurements, both grounded in live data:

  (A) MECHANISM. For every interchange MOTIS actually returns, MOTIS emits the
      transfer as a WALK leg whose `duration` is its *required* transfer time
      (the footpath defaultDuration; at API defaults factor=1, add=0). Compare
      that to core/'s real OSM platform-to-platform walk. The gap
      [core_real .. motis_assumed) is the band of connections MOTIS would reject
      but that are physically makeable.

  (B) IMPACT. Holding the first leg fixed (train A into hub H, arriving platform
      P_arr at t_arr), is there an EARLIER onward train from H toward the
      destination that MOTIS did not use, but that the real walk makes catchable?
      We surface it by querying MOTIS again with H's *platform stopId* as the
      origin (origin boarding is ungated by transfer time), enumerate onward
      trains, and apply the real-walk gate ourselves.

Everything is checkpointed to a JSON file after each O-D pair and Ctrl-C is
handled so a long run never loses progress.
"""

from __future__ import annotations

import json
import os
import sys
import time as _time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # this dir (for iris)
sys.path.insert(0, os.path.join(REPO, "core"))
sys.path.insert(0, REPO)

import db as _db  # core/db.py
from api.transfers import assess_transfer, DEFAULT_BUFFER_S
import iris  # DB IRIS platform enrichment (scratchpad module)

PLAN_URL = "https://api.transitous.org/api/v5/plan"
GEOCODE_URL = "https://api.transitous.org/api/v1/geocode"
STATE_PATH = os.path.join(os.path.dirname(__file__), "tight_state.json")

_geocache: Dict[str, Any] = {}


def geocode(text: str) -> Dict[str, Any]:
    """Resolve a station name to the top MOTIS STOP (id, name, lat, lon).

    Uses MOTIS's own geocoder so names resolve to the right station/country
    (the local CSV mis-resolved e.g. 'St. Gallen' to an Austrian halt)."""
    if text in _geocache:
        return _geocache[text]
    r = _session.get(GEOCODE_URL, params={"text": text}, timeout=20)
    r.raise_for_status()
    for m in r.json():
        if m.get("type") == "STOP":
            out = {"id": m["id"], "name": m.get("name"), "lat": m.get("lat"), "lon": m.get("lon")}
            _geocache[text] = out
            return out
    raise RuntimeError(f"no STOP geocode for {text!r}")

WALK_MODES = {"WALK", "BIKE", "CAR", "BIKE_SHARING", "CAR_SHARING", "SCOOTER_SHARING"}

_session = requests.Session()
_session.headers.update({
    "Accept": "application/json",
    "User-Agent": "transfr-research/0.1 (tight-connection study)",
})


def _iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def plan(from_place: str, to_place: str, when: datetime, n: int = 5,
         page_cursor: Optional[str] = None, tries: int = 3) -> Dict[str, Any]:
    """One MOTIS /plan call, with polite retry/backoff."""
    params = {
        "fromPlace": from_place,
        "toPlace": to_place,
        "time": when.isoformat(),
        "numItineraries": str(n),
    }
    if page_cursor:
        params["pageCursor"] = page_cursor
    last = None
    for k in range(tries):
        try:
            r = _session.get(PLAN_URL, params=params, timeout=25)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}"
        _time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"plan failed ({from_place}->{to_place}): {last}")


def transit_and_transfers(itin: Dict[str, Any]) -> Tuple[List[dict], List[dict]]:
    """Return (transit_legs, transfer_walk_legs_between_them).

    transfer_walk_legs[i] is the list of WALK legs sitting between transit leg i
    and i+1 (usually 0 or 1). Their summed duration is MOTIS's required transfer.
    """
    legs = itin.get("legs", [])
    transit = [l for l in legs if l.get("mode") not in WALK_MODES]
    transfers: List[List[dict]] = []
    # Walk legs strictly between consecutive transit legs (by index in `legs`).
    idx = [i for i, l in enumerate(legs) if l.get("mode") not in WALK_MODES]
    for a, b in zip(idx, idx[1:]):
        transfers.append([legs[j] for j in range(a + 1, b) if legs[j].get("mode") in WALK_MODES])
    return transit, transfers


def itin_arrival(itin: Dict[str, Any]) -> Optional[datetime]:
    legs = itin.get("legs", [])
    return _iso(legs[-1].get("endTime")) if legs else None


MAX_PLAUSIBLE_TRANSFER_M = 1500.0  # a real change of train, not an inter-station hike/glitch


def _max_transfer_walk_m(itin: Dict[str, Any]) -> float:
    _, transfers = transit_and_transfers(itin)
    return max((sum(w.get("distance") or 0 for w in ws) for ws in transfers), default=0.0)


def best_itin(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Earliest-arriving itinerary a traveller would actually take. Degenerate
    itineraries (MOTIS occasionally emits a huge 'transfer' walk that teleports)
    are excluded unless nothing else is available."""
    its = data.get("itineraries", [])
    dated = [(itin_arrival(it), it) for it in its]
    dated = [(a, it) for a, it in dated if a is not None]
    if not dated:
        return None
    clean = [(a, it) for a, it in dated if _max_transfer_walk_m(it) <= MAX_PLAUSIBLE_TRANSFER_M]
    pool = clean or dated
    return min(pool, key=lambda x: x[0])[1]


@dataclass
class Interchange:
    hub: Optional[str]
    hub_id_arr: Optional[str]
    hub_id_dep: Optional[str]
    arr_track: Optional[str]
    dep_track: Optional[str]
    arr_lat: Optional[float]
    arr_lon: Optional[float]
    dep_lat: Optional[float]
    dep_lon: Optional[float]
    t_arr: Optional[str]
    t_dep: Optional[str]
    gap_s: Optional[float]           # scheduled slack between the two trains
    motis_assumed_s: Optional[float] # MOTIS required transfer (walk-leg duration)
    motis_walk_m: Optional[float]
    # core/ side (filled later)
    core_walk_s: Optional[float] = None
    core_walk_m: Optional[float] = None
    core_reason: Optional[str] = None
    core_station: Optional[str] = None
    arr_line: Optional[str] = None
    dep_line: Optional[str] = None
    arr_headsign: Optional[str] = None
    dep_headsign: Optional[str] = None
    arr_track_src: str = "motis"      # 'motis' | 'iris' | 'none'
    dep_track_src: str = "motis"      # 'motis' | 'iris' | 'none'
    arr_track_iris_how: Optional[str] = None
    dep_track_iris_how: Optional[str] = None


def interchanges_of(itin: Dict[str, Any]) -> List[Interchange]:
    transit, transfers = transit_and_transfers(itin)
    out: List[Interchange] = []
    for i in range(len(transit) - 1):
        a, b = transit[i], transit[i + 1]
        a_to, b_from = a.get("to") or {}, b.get("from") or {}
        walks = transfers[i]
        assumed = sum(w.get("duration") or 0 for w in walks) if walks else 0.0
        wmeters = sum(w.get("distance") or 0 for w in walks) if walks else 0.0
        t_arr, t_dep = _iso(a.get("endTime")), _iso(b.get("startTime"))
        gap = (t_dep - t_arr).total_seconds() if (t_arr and t_dep) else None
        out.append(Interchange(
            hub=a_to.get("name"),
            hub_id_arr=a_to.get("stopId"), hub_id_dep=b_from.get("stopId"),
            arr_track=a_to.get("track"), dep_track=b_from.get("track"),
            arr_lat=a_to.get("lat"), arr_lon=a_to.get("lon"),
            dep_lat=b_from.get("lat"), dep_lon=b_from.get("lon"),
            t_arr=a.get("endTime"), t_dep=b.get("startTime"),
            gap_s=gap, motis_assumed_s=float(assumed), motis_walk_m=float(wmeters),
            arr_line=a.get("routeShortName") or a.get("displayName"),
            dep_line=b.get("routeShortName") or b.get("displayName"),
            arr_headsign=a.get("headsign"),
            dep_headsign=b.get("headsign"),
        ))
    return out


def core_walk(conn, ic: Interchange) -> None:
    """Fill core_walk_s / _m / reason on the interchange via the production
    assess_transfer path (same code the API uses).

    NB: we deliberately do NOT pass max_search_seconds -- it has a bug (any value
    flips a correct 'feasible' into 'exceeded_plausibility_bound'). Real transfers
    resolve in <1s and fast-fails in <0.3s, so unbounded is safe here."""
    # IRIS enrichment: MOTIS often omits a platform (S-Bahn etc.). Fill from DB
    # IRIS by (line, terminus). For a through train the arrival platform equals
    # its departure platform at the hub, so the same departures-board lookup works.
    if ic.dep_track is None and ic.dep_lat is not None:
        p, how = iris.fill(ic.dep_lat, ic.dep_lon, ic.hub, ic.dep_line, ic.dep_headsign)
        if p:
            ic.dep_track, ic.dep_track_src, ic.dep_track_iris_how = p, "iris", how
    if ic.arr_track is None and ic.arr_lat is not None:
        p, how = iris.fill(ic.arr_lat, ic.arr_lon, ic.hub, ic.arr_line, ic.arr_headsign)
        if p:
            ic.arr_track, ic.arr_track_src, ic.arr_track_iris_how = p, "iris", how
    if ic.arr_track is None or ic.dep_track is None:
        ic.core_reason = "no_platform_data"
        if ic.dep_track_src != "iris":
            ic.dep_track_src = "none"
        return
    a = assess_transfer(
        conn,
        arr_lat=ic.arr_lat, arr_lon=ic.arr_lon,
        arr_platform=ic.arr_track, arr_time=ic.t_arr,
        dep_lat=ic.dep_lat, dep_lon=ic.dep_lon,
        dep_platform=ic.dep_track, dep_time=ic.t_dep,
    )
    ic.core_walk_s = a.walk_time_s
    ic.core_walk_m = a.walk_distance_m
    ic.core_station = a.station_name
    if a.walk_time_s is None:
        ic.core_reason = a.reason or "not_found"


@dataclass
class OnwardOption:
    dep_track: Optional[str]
    dep_lat: Optional[float]
    dep_lon: Optional[float]
    t_dep: str
    final_arr: str
    first_line: Optional[str]
    headsign: Optional[str] = None
    dep_track_src: str = "motis"      # 'motis' | 'iris'
    hub_name: Optional[str] = None


def enumerate_onward(hub_stop_id: str, to_place: str, around: datetime,
                     pages: int = 2, n: int = 7) -> List[OnwardOption]:
    """All onward itineraries from the hub platform (as origin) toward D, over a
    window starting ~15 min before `around`. Origin boarding is transfer-ungated,
    so this reveals trains MOTIS hid behind the transfer gate in the O->D query."""
    start = around - timedelta(minutes=15)
    out: List[OnwardOption] = []
    cursor = None
    for _ in range(pages):
        data = plan(hub_stop_id, to_place, start, n=n, page_cursor=cursor)
        for it in data.get("itineraries", []):
            transit, _ = transit_and_transfers(it)
            if not transit:
                continue
            b0 = transit[0]
            bf = b0.get("from") or {}
            fa = itin_arrival(it)
            if fa is None:
                continue
            out.append(OnwardOption(
                dep_track=bf.get("track"), dep_lat=bf.get("lat"), dep_lon=bf.get("lon"),
                t_dep=b0.get("startTime"), final_arr=fa.isoformat(),
                first_line=b0.get("routeShortName") or b0.get("displayName"),
                headsign=b0.get("headsign"), hub_name=bf.get("name"),
            ))
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    # de-dup by (t_dep, dep_track)
    seen = set(); uniq = []
    for o in out:
        k = (o.t_dep, o.dep_track)
        if k not in seen:
            seen.add(k); uniq.append(o)
    return uniq


# ---------------------------------------------------------------- persistence
def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"pairs": {}}


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, default=str)
    os.replace(tmp, STATE_PATH)
