"""HAFAS-based journey search and station autocomplete.

Uses pyhafas with the Deutsche Bahn profile to query real-time train
schedules including platform information.  The DB HAFAS endpoint covers
most European long-distance and regional rail networks.
"""

from datetime import datetime
from typing import Dict, List, Any, Optional

from pyhafas import HafasClient
from pyhafas.profile import DBProfile

_client: Optional[HafasClient] = None


def get_client() -> HafasClient:
    """Lazily initialise and return the shared HafasClient."""
    global _client
    if _client is None:
        _client = HafasClient(DBProfile())
    return _client


def autocomplete_station(term: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Return stations matching a partial name.

    Each result dict has keys: id, name, latitude, longitude.
    Results are ordered by relevance (best match first).
    """
    client = get_client()
    stations = client.locations(term)
    return [
        {
            "id": s.id,
            "name": s.name,
            "latitude": s.latitude,
            "longitude": s.longitude,
        }
        for s in stations[:max_results]
    ]


def _serialize_station(station) -> Dict[str, Any]:
    return {
        "id": station.id,
        "name": station.name,
        "latitude": station.latitude,
        "longitude": station.longitude,
    }


def _serialize_stopover(so) -> Dict[str, Any]:
    return {
        "station": {"id": so.stop.id, "name": so.stop.name},
        "arrival": so.arrival.isoformat() if so.arrival else None,
        "departure": so.departure.isoformat() if so.departure else None,
        "arrival_platform": so.arrival_platform,
        "departure_platform": so.departure_platform,
        "arrival_delay_s": (
            so.arrival_delay.total_seconds() if so.arrival_delay else None
        ),
        "departure_delay_s": (
            so.departure_delay.total_seconds() if so.departure_delay else None
        ),
        "cancelled": so.cancelled,
    }


def _serialize_leg(leg) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "origin": _serialize_station(leg.origin),
        "destination": _serialize_station(leg.destination),
        "departure": leg.departure.isoformat() if leg.departure else None,
        "arrival": leg.arrival.isoformat() if leg.arrival else None,
        "departure_platform": leg.departure_platform,
        "arrival_platform": leg.arrival_platform,
        "departure_delay_s": (
            leg.departure_delay.total_seconds() if leg.departure_delay else None
        ),
        "arrival_delay_s": (
            leg.arrival_delay.total_seconds() if leg.arrival_delay else None
        ),
        "train_name": leg.name,
        "mode": leg.mode.value if leg.mode else None,
        "cancelled": leg.cancelled,
    }
    if leg.distance is not None:
        d["distance_m"] = leg.distance
    if leg.stopovers:
        d["stopovers"] = [_serialize_stopover(so) for so in leg.stopovers]
    return d


def search_journeys(
    origin_name: str,
    destination_name: str,
    departure_time: datetime,
    max_journeys: int = 5,
) -> Dict[str, Any]:
    """Search for train journeys between two stations.

    Looks up stations by name, then queries HAFAS for possible journeys.
    Returns a dict with the resolved origin/destination and a list of
    journey options.  Each journey contains legs with departure/arrival
    platform information.

    Raises ValueError if either station name cannot be resolved.
    """
    client = get_client()

    origin_stations = client.locations(origin_name)
    if not origin_stations:
        raise ValueError(f"No station found for: {origin_name!r}")

    dest_stations = client.locations(destination_name)
    if not dest_stations:
        raise ValueError(f"No station found for: {destination_name!r}")

    origin = origin_stations[0]
    destination = dest_stations[0]

    journeys = client.journeys(
        origin=origin,
        destination=destination,
        date=departure_time,
        max_journeys=max_journeys,
    )

    results = []
    for journey in journeys:
        legs = [_serialize_leg(leg) for leg in (journey.legs or [])]
        results.append(
            {
                "id": journey.id,
                "date": journey.date.isoformat() if journey.date else None,
                "duration_s": (
                    journey.duration.total_seconds() if journey.duration else None
                ),
                "legs": legs,
                "num_changes": max(0, len(legs) - 1),
            }
        )

    return {
        "origin": _serialize_station(origin),
        "destination": _serialize_station(destination),
        "departure_time": departure_time.isoformat(),
        "journeys": results,
    }
