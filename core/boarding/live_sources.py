"""
Data-process pipeline: pull REAL data from live public rail APIs and format it
into the shape the (already tested) algorithm consumes.

This module is deliberately kept apart from the algorithm. It imports the
algorithm-facing types (formation_model.NormalizedFormation / PlatformSectorMap,
boarding.PlatformGeometry) and produces them; it never modifies them, and the
pathfinding code has no idea any of this exists. The contract between the two
halves is exactly NormalizedFormation + PlatformGeometry -- nothing else crosses
the line.

WHAT ACTUALLY CONNECTS FROM A GENERIC HOST (measured 2026-07-12):
  * dbf.finalrewind.org/{eva}.json   -- live DB departure boards (derf's IRIS
    wrapper): train number, scheduled/real time, platform, class, full route.
    REACHABLE. -> fetch_db_departures()
  * api.transitous.org (MOTIS 2)     -- live multimodal routing with real
    per-stop platform/track + coordinates. REACHABLE. -> fetch_transitous_plan()
  * Deutsche Bahn coach-formation (the actual sectors/metres) lives ONLY at
    ist-wr.noncd.db.de (NXDOMAIN outside DB/DE networks) and
    www.apps-bahn.de/wr/... (completes TLS, then the host silently drops the
    HTTP request for non-DE IPs -- a geo-block). So the formation FETCH is not
    reachable from an arbitrary host, but the SCHEMA is public and stable: the
    field names below are confirmed against juliuste/db-wagenreihung's live
    client. parse_wagenreihung() turns that exact wire format into a
    NormalizedFormation, so the moment this runs where the host resolves (a DE
    egress / CI with the right network), it produces real formation with zero
    code change. fetch_db_formation() attempts it live and raises
    FormationUnavailable with the concrete reason when blocked.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from seat import PlatformGeometry
from formation_model import CoachPlacement, NormalizedFormation, PlatformSectorMap

_UA = "transfr/0.1 (+https://github.com/simonchervenak/transfr) data-process"
_TIMEOUT = 15

DERF_BASE = "https://dbf.finalrewind.org"
# Same TRANSFR_MOTIS_BASE knob as api/config.MOTIS_BASE; core/ must not import the
# api layer, so the env var is read directly here to keep the two in lockstep. A
# self-hosted MOTIS (deploy/motis-selfhost/) moves this tooling path too.
TRANSITOUS_PLAN = os.environ.get("TRANSFR_MOTIS_BASE", "https://api.transitous.org").rstrip("/") + "/api/v5/plan"
# Canonical DB Wagenreihung endpoint (as used by juliuste/db-wagenreihung).
# ist-wr.noncd.db.de is the newer host; apps-bahn.de the legacy one. Both are
# geo-restricted -- see the module docstring.
WAGENREIHUNG_URL = "https://www.apps-bahn.de/wr/wagenreihung/1.0/{train}/{when}"


class FormationUnavailable(RuntimeError):
    """The live coach-formation feed could not be reached or had no data.
    Carries the concrete reason (geo-block, not-in-2h-window, no data) so a
    caller can degrade to a platform-level answer instead of guessing."""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    return s


# ---------------------------------------------------------------------------
# Live departures (REACHABLE) -- dbf.finalrewind.org, DB IRIS under the hood
# ---------------------------------------------------------------------------

@dataclass
class Departure:
    """One real departure from a station board."""

    train: str                 # "ICE 124"
    train_number: str          # "124"
    train_class: str           # DB trainClasses[0]: "F" long-distance, "N" regional...
    scheduled_departure: Optional[str]
    delay_minutes: Optional[int]
    platform: Optional[str]
    destination: Optional[str]
    route: List[str]           # full downstream stop names


def parse_db_departures(payload: Dict[str, Any]) -> List[Departure]:
    """dbf.finalrewind.org JSON board -> Departure list. Pure (no network), so
    it is unit-tested against a captured real board."""
    out: List[Departure] = []
    for t in payload.get("departures", []):
        classes = t.get("trainClasses") or []
        out.append(Departure(
            train=t.get("train", ""),
            train_number=str(t.get("trainNumber", "")),
            train_class=classes[0] if classes else "",
            scheduled_departure=t.get("scheduledDeparture"),
            delay_minutes=t.get("delayDeparture"),
            platform=str(t["platform"]) if t.get("platform") is not None else None,
            destination=t.get("destination"),
            route=[r.get("name") for r in t.get("route", []) if isinstance(r, dict)],
        ))
    return out


def fetch_db_departures(eva: str, session: Optional[requests.Session] = None) -> List[Departure]:
    """LIVE: real departure board for a station EVA (e.g. 8000105 = Frankfurt Hbf)."""
    s = session or _session()
    resp = s.get(f"{DERF_BASE}/{eva}.json", timeout=_TIMEOUT)
    resp.raise_for_status()
    return parse_db_departures(resp.json())


def long_distance(departures: List[Departure]) -> List[Departure]:
    """Only trains that plausibly have coach-formation data (long-distance)."""
    return [d for d in departures if d.train_class == "F" and d.scheduled_departure]


# ---------------------------------------------------------------------------
# Live platform/track per stop (REACHABLE) -- Transitous MOTIS 2
# ---------------------------------------------------------------------------

@dataclass
class TripStopPlatform:
    """The real platform/track a leg uses at its origin and destination."""

    mode: str
    line: Optional[str]
    from_name: Optional[str]
    from_track: Optional[str]
    to_name: Optional[str]
    to_track: Optional[str]


def parse_transitous_platforms(plan: Dict[str, Any]) -> List[TripStopPlatform]:
    """MOTIS plan -> the transit legs' real platform/track. Pure, unit-testable."""
    out: List[TripStopPlatform] = []
    for itin in plan.get("itineraries", []):
        for leg in itin.get("legs", []):
            if leg.get("mode") in ("WALK", "BIKE", "CAR"):
                continue
            frm, to = leg.get("from") or {}, leg.get("to") or {}
            out.append(TripStopPlatform(
                mode=leg.get("mode", ""),
                line=leg.get("routeShortName") or leg.get("displayName"),
                from_name=frm.get("name"), from_track=frm.get("track"),
                to_name=to.get("name"), to_track=to.get("track"),
            ))
    return out


def fetch_transitous_plan(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float,
    when_iso: str, session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """LIVE: a real MOTIS itinerary between two coordinates."""
    s = session or _session()
    resp = s.get(TRANSITOUS_PLAN, params={
        "fromPlace": f"{from_lat},{from_lon}", "toPlace": f"{to_lat},{to_lon}",
        "time": when_iso, "numItineraries": "3",
    }, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Coach formation (schema public, live fetch geo-blocked) -- DB Wagenreihung
# ---------------------------------------------------------------------------

# kategorie (DB vehicle category) -> our travel_class. Passenger cars only;
# power cars / control cars carry no reservable seats and are dropped.
_KATEGORIE_CLASS = {
    "REISEZUGWAGENERSTEKLASSE": "1",
    "REISEZUGWAGENZWEITEKLASSE": "2",
    "REISEZUGWAGENERSTEZWEITEKLASSE": "12",
    "HALBSPEISEWAGENERSTEKLASSE": "WR",
    "HALBSPEISEWAGENZWEITEKLASSE": "WR",
    "SPEISEWAGEN": "WR",
}
_SKIP_KATEGORIE = ("TRIEBKOPF", "LOK", "STEUERWAGEN")


def _num(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def sector_map_from_wagenreihung(payload: Dict[str, Any]) -> Optional[PlatformSectorMap]:
    """Build a PlatformSectorMap from the feed's own allSektor block (each
    sector's start/end metres on the platform) -- the highest-fidelity sector
    map there is, straight from the operator."""
    ist = payload.get("data", {}).get("istformation", {})
    spans: Dict[str, tuple] = {}
    for sek in ist.get("allSektor", []):
        pos = sek.get("positionamgleis", {})
        s, e = _num(pos.get("startmeter")), _num(pos.get("endemeter"))
        name = sek.get("sektorbezeichnung")
        if name and s is not None and e is not None and e > s:
            spans[name.upper()] = (s, e)
    return PlatformSectorMap(spans) if spans else None


def parse_wagenreihung(payload: Dict[str, Any]) -> NormalizedFormation:
    """DB Wagenreihung (ist-wr / apps-bahn) wire format -> NormalizedFormation.

    Schema (field names confirmed against juliuste/db-wagenreihung's live
    client): data.istformation.allFahrzeuggruppe[].allFahrzeug[], each with
    wagenordnungsnummer, kategorie, fahrzeugsektor, and positionamhalt
    {startmeter, endemeter, startprozent, endeprozent}. allFahrzeuggruppe are
    the united/divided train portions; halt carries the station/platform.
    """
    ist = payload.get("data", {}).get("istformation")
    if not ist:
        raise FormationUnavailable("wagenreihung payload has no data.istformation")
    halt = ist.get("halt", {})
    platform_len = None
    smap = sector_map_from_wagenreihung(payload)
    if smap:
        platform_len = max(e for _, e in smap.spans.values())

    placements: List[CoachPlacement] = []
    for gruppe in ist.get("allFahrzeuggruppe", []):
        group = gruppe.get("fahrzeuggruppebezeichnung") or gruppe.get("verkehrlichezugnummer")
        for fz in gruppe.get("allFahrzeug", []):
            kat = (fz.get("kategorie") or "").upper()
            if any(k in kat for k in _SKIP_KATEGORIE):
                continue
            pos = fz.get("positionamhalt", {})
            start_m, end_m = _num(pos.get("startmeter")), _num(pos.get("endemeter"))
            if start_m is None and platform_len and _num(pos.get("startprozent")) is not None:
                start_m = _num(pos["startprozent"]) / 100.0 * platform_len
                end_m = _num(pos["endeprozent"]) / 100.0 * platform_len
            num = fz.get("wagenordnungsnummer")
            placements.append(CoachPlacement(
                coach=str(num),
                order=int(num) if num is not None and str(num).isdigit() else None,
                travel_class=_KATEGORIE_CLASS.get(kat),
                sectors=[fz["fahrzeugsektor"]] if fz.get("fahrzeugsektor") else [],
                start_m=start_m, end_m=end_m,
                group=group,
            ))
    if not placements:
        raise FormationUnavailable("wagenreihung payload had no boardable coaches")

    return NormalizedFormation(
        train_id=str(ist.get("fahrtnummer") or halt.get("fahrtnummer") or ""),
        country="DE", source="db-wagenreihung", placements=placements,
        station=halt.get("bahnhofsname"), track=str(halt.get("gleisbezeichnung") or "") or None,
    )


def fetch_db_formation(
    train_number: str, when_yyyymmddhhmm: str, session: Optional[requests.Session] = None,
) -> NormalizedFormation:
    """LIVE attempt at real coach formation. Raises FormationUnavailable (never a
    raw network traceback) so callers can fall back cleanly. `when` is the local
    scheduled departure as YYYYMMDDHHmm; the train must depart within 2 hours."""
    s = session or _session()
    url = WAGENREIHUNG_URL.format(train=train_number, when=when_yyyymmddhhmm)
    try:
        resp = s.get(url, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise FormationUnavailable(
            f"could not reach the DB Wagenreihung host ({type(e).__name__}); it is "
            f"geo-restricted to DB/DE networks -- see live_sources module docstring"
        ) from e
    if resp.status_code != 200 or not resp.content:
        raise FormationUnavailable(f"DB Wagenreihung returned HTTP {resp.status_code} / {len(resp.content)}B")
    return parse_wagenreihung(resp.json())


# ---------------------------------------------------------------------------
# Geometry from the feed (bridges the data half to the algorithm half)
# ---------------------------------------------------------------------------

def straight_geometry_for(
    length_m: float, base_lat: float = 50.107, base_lon: float = 8.663, node_spacing_m: float = 10.0,
) -> PlatformGeometry:
    """A straight synthetic PlatformGeometry of the given length, for when the
    real OSM platform polyline isn't loaded (transfr's Postgres side). In
    production this is replaced by the actual platform_edge geometry SearchContext
    already resolves; the algorithm can't tell the difference -- both are just a
    PlatformGeometry. Default coordinates are near Frankfurt Hbf."""
    n = max(2, int(length_m // node_spacing_m) + 1)
    offsets = [round(min(i * node_spacing_m, length_m), 3) for i in range(n)]
    if offsets[-1] < length_m:
        offsets.append(length_m)
    return PlatformGeometry.straight_line(base_lat, base_lon, offsets)
