"""
The pipeline: journey search -> per-interchange transfer assessment -> response.

`enrich()` is the heart -- it takes a raw journeys.search_journeys result and a
DB connection and returns a typed JourneysResponse in which every change of train
carries a walkability verdict. It's kept separate from `plan_journeys()` (which
also does the network search) so it can be tested against captured fixtures with
the transfer assessment stubbed -- no network, no DB.
"""

from datetime import datetime
from typing import Any, Dict, List

from api import schemas
from api.transfers import (
    DEFAULT_ALGORITHM, DEFAULT_BUFFER_S,
    FEASIBLE, INFEASIBLE, TIGHT, UNKNOWN,
    assess_transfer,
)
from api.transitous import interchanges, search, transit_legs

# Journey-level rollup: the worst transfer wins. A definite infeasible dominates
# an unknown (a broken leg breaks the trip regardless of the unknowns); unknown
# dominates tight/feasible (we can't promise a trip with an unassessable change).
_VERDICT_RANK = {INFEASIBLE: 0, UNKNOWN: 1, TIGHT: 2, FEASIBLE: 3}


def rollup_verdict(verdicts: List[str]) -> str:
    """Worst verdict across a journey's transfers; feasible when there are none
    (a direct train can't miss a connection)."""
    if not verdicts:
        return FEASIBLE
    return min(verdicts, key=lambda v: _VERDICT_RANK.get(v, _VERDICT_RANK[UNKNOWN]))


def _place(d: Dict[str, Any]) -> schemas.Place:
    d = d or {}
    return schemas.Place(
        id=d.get("id"), name=d.get("name"),
        latitude=d.get("latitude"), longitude=d.get("longitude"),
    )


def _leg(d: Dict[str, Any]) -> schemas.Leg:
    return schemas.Leg(
        mode=d.get("mode", ""),
        train_name=d.get("train_name"),
        origin=_place(d.get("origin")),
        destination=_place(d.get("destination")),
        departure=d.get("departure"),
        arrival=d.get("arrival"),
        planned_departure=d.get("planned_departure"),
        planned_arrival=d.get("planned_arrival"),
        departure_platform=d.get("departure_platform"),
        arrival_platform=d.get("arrival_platform"),
        departure_delay_s=d.get("departure_delay_s"),
        arrival_delay_s=d.get("arrival_delay_s"),
        cancelled=bool(d.get("cancelled", False)),
        distance_m=d.get("distance_m"),
    )


def _assess(conn, arrive: Dict[str, Any], depart: Dict[str, Any],
            buffer_s: float, algorithm: str) -> schemas.Transfer:
    arr, dep = arrive.get("destination") or {}, depart.get("origin") or {}
    a = assess_transfer(
        conn,
        arr_lat=arr.get("latitude"), arr_lon=arr.get("longitude"),
        arr_platform=arrive.get("arrival_platform"), arr_time=arrive.get("arrival"),
        dep_lat=dep.get("latitude"), dep_lon=dep.get("longitude"),
        dep_platform=depart.get("departure_platform"), dep_time=depart.get("departure"),
        buffer_s=buffer_s, algorithm=algorithm,
    )
    return schemas.Transfer(
        at_station=a.station_name or arr.get("name"),
        relation_id=a.relation_id,
        arrival_platform=a.arrival_platform,
        departure_platform=a.departure_platform,
        layover_s=a.layover_s,
        walk_time_s=a.walk_time_s,
        walk_distance_m=a.walk_distance_m,
        verdict=a.verdict,
        reason=a.reason,
    )


def enrich(conn, search_result: Dict[str, Any], *,
           buffer_s: float = DEFAULT_BUFFER_S,
           algorithm: str = DEFAULT_ALGORITHM) -> schemas.JourneysResponse:
    journeys_out: List[schemas.Journey] = []
    for j in search_result.get("journeys", []):
        transfers = [
            _assess(conn, arrive, depart, buffer_s, algorithm)
            for arrive, depart in interchanges(j)
        ]
        n_changes = j.get("num_changes")
        if n_changes is None:
            n_changes = max(0, len(transit_legs(j)) - 1)
        journeys_out.append(schemas.Journey(
            id=j.get("id"),
            date=j.get("date"),
            duration_s=j.get("duration_s"),
            num_changes=n_changes,
            verdict=rollup_verdict([t.verdict for t in transfers]),
            legs=[_leg(leg) for leg in j.get("legs", [])],
            transfers=transfers,
        ))
    return schemas.JourneysResponse(
        origin=_place(search_result.get("origin")),
        destination=_place(search_result.get("destination")),
        departure_time=search_result.get("departure_time"),
        journeys=journeys_out,
    )


def plan_journeys(conn, origin: str, destination: str, when: datetime,
                  max_journeys: int = 5, **enrich_kwargs) -> schemas.JourneysResponse:
    """Search + enrich: the full product path used by the /journeys endpoint."""
    result = search(origin, destination, when, max_journeys=max_journeys)
    return enrich(conn, result, **enrich_kwargs)
