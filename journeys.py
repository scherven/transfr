"""Connection routing via the Transitous (MOTIS 2) API.

Calls api.transitous.org/api/v5/plan and maps the response to
the same shape the frontend already expects from the old hafas module.
"""

from datetime import datetime
from typing import Dict, List, Any

import requests

from stations import resolve_station

TRANSITOUS_BASE = "https://api.transitous.org"
PLAN_URL = f"{TRANSITOUS_BASE}/api/v5/plan"

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "Accept": "application/json",
            "User-Agent": "transfr/0.1 (https://github.com/simonchervenak/transfr)",
        })
    return _session


def _extract_place(place: dict | None) -> Dict[str, Any]:
    if not place:
        return {"id": None, "name": None, "latitude": None, "longitude": None}
    return {
        "id": place.get("stopId"),
        "name": place.get("name"),
        "latitude": place.get("lat"),
        "longitude": place.get("lon"),
    }


def _delay_seconds(actual: str | None, scheduled: str | None) -> int | None:
    """Compute delay in seconds between two ISO timestamps."""
    if not actual or not scheduled:
        return None
    try:
        dt_a = datetime.fromisoformat(actual)
        dt_s = datetime.fromisoformat(scheduled)
        diff = int((dt_a - dt_s).total_seconds())
        return diff if diff != 0 else None
    except (ValueError, TypeError):
        return None


_WALK_MODES = {"WALK", "BIKE", "CAR", "BIKE_SHARING", "CAR_SHARING", "SCOOTER_SHARING"}


def _extract_stopover(stop: dict) -> Dict[str, Any]:
    return {
        "station": _extract_place(stop),
        "arrival": stop.get("arrival"),
        "planned_arrival": stop.get("scheduledArrival"),
        "departure": stop.get("departure"),
        "planned_departure": stop.get("scheduledDeparture"),
        "arrival_platform": stop.get("track"),
        "planned_arrival_platform": stop.get("scheduledTrack"),
        "departure_platform": stop.get("track"),
        "planned_departure_platform": stop.get("scheduledTrack"),
        "arrival_delay_s": _delay_seconds(stop.get("arrival"), stop.get("scheduledArrival")),
        "departure_delay_s": _delay_seconds(stop.get("departure"), stop.get("scheduledDeparture")),
        "cancelled": stop.get("cancelled", False),
    }


def _extract_leg(leg: dict) -> Dict[str, Any]:
    mode = leg.get("mode", "")
    is_walking = mode in _WALK_MODES
    from_place = leg.get("from") or {}
    to_place = leg.get("to") or {}

    d: Dict[str, Any] = {
        "origin": _extract_place(from_place),
        "destination": _extract_place(to_place),
        "departure": leg.get("startTime"),
        "planned_departure": leg.get("scheduledStartTime"),
        "arrival": leg.get("endTime"),
        "planned_arrival": leg.get("scheduledEndTime"),
        "departure_platform": from_place.get("track"),
        "planned_departure_platform": from_place.get("scheduledTrack"),
        "arrival_platform": to_place.get("track"),
        "planned_arrival_platform": to_place.get("scheduledTrack"),
        "departure_delay_s": _delay_seconds(leg.get("startTime"), leg.get("scheduledStartTime")),
        "arrival_delay_s": _delay_seconds(leg.get("endTime"), leg.get("scheduledEndTime")),
        "train_name": leg.get("displayName") or leg.get("routeShortName") if not is_walking else None,
        "mode": "walking" if is_walking else mode.lower(),
        "cancelled": leg.get("cancelled", False),
    }

    if is_walking and leg.get("distance") is not None:
        d["distance_m"] = int(leg["distance"])

    intermediates = leg.get("intermediateStops")
    if intermediates:
        d["stopovers"] = [_extract_stopover(s) for s in intermediates]

    return d


def search_journeys(
    origin_name: str,
    destination_name: str,
    departure_time: datetime,
    max_journeys: int = 5,
) -> Dict[str, Any]:
    """Search for train journeys between two stations.

    Resolves station names via the local CSV, then queries the
    Transitous MOTIS API for connections using coordinates.

    Returns the same response shape the frontend expects.
    Raises ValueError if either station name cannot be resolved.
    """
    origin = resolve_station(origin_name)
    destination = resolve_station(destination_name)

    from_place = f"{origin['latitude']},{origin['longitude']}"
    to_place = f"{destination['latitude']},{destination['longitude']}"

    resp = _get_session().get(
        PLAN_URL,
        params={
            "fromPlace": from_place,
            "toPlace": to_place,
            "time": departure_time.isoformat(),
            "numItineraries": str(max_journeys),
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    results: List[Dict[str, Any]] = []
    for itin in data.get("itineraries", []):
        legs = [_extract_leg(leg) for leg in itin.get("legs", [])]

        first_dep = legs[0]["departure"] if legs else None
        last_arr = legs[-1]["arrival"] if legs else None
        duration_s = itin.get("duration")

        if duration_s is None and first_dep and last_arr:
            try:
                dt_dep = datetime.fromisoformat(first_dep)
                dt_arr = datetime.fromisoformat(last_arr)
                duration_s = int((dt_arr - dt_dep).total_seconds())
            except (ValueError, TypeError):
                pass

        results.append({
            "id": f"{first_dep}_{itin.get('transfers', 0)}",
            "date": first_dep,
            "duration_s": duration_s,
            "legs": legs,
            "num_changes": itin.get("transfers", 0),
        })

    origin_out = {
        "id": origin.get("id"),
        "name": origin["name"],
        "latitude": origin["latitude"],
        "longitude": origin["longitude"],
    }
    dest_out = {
        "id": destination.get("id"),
        "name": destination["name"],
        "latitude": destination["latitude"],
        "longitude": destination["longitude"],
    }

    return {
        "origin": origin_out,
        "destination": dest_out,
        "departure_time": departure_time.isoformat(),
        "journeys": results,
    }
