"""
Thin wrapper over the tested Transitous (MOTIS 2) client in journeys.py.

Keeps the API decoupled from the module name and exposes the interchange view
the pipeline works in: the sequence of consecutive *train* legs (walking legs --
station access and the transfer walk itself -- dropped), each adjacent pair
being one change of train.
"""

from datetime import datetime
from typing import Any, Dict, List, Tuple

import journeys as _journeys

WALKING = "walking"  # journeys.py lowercases modes and labels every non-transit leg this


def search(origin: str, destination: str, when: datetime, max_journeys: int = 5) -> Dict[str, Any]:
    return _journeys.search_journeys(origin, destination, when, max_journeys=max_journeys)


def transit_legs(journey: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [leg for leg in journey["legs"] if leg.get("mode") != WALKING]


def interchanges(journey: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(arriving_leg, departing_leg) for each change of train."""
    legs = transit_legs(journey)
    return list(zip(legs, legs[1:]))
