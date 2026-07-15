#!/usr/bin/env python3
"""Regenerate the Swift test goldens from the Python contracts.

The whole point of these fixtures is that they are produced by the *same*
pydantic models the API serves (`api/schemas.py`) and by real
`core/viz_export.py` output — so a Swift decode test fails the instant the
server contract drifts. Run from the repo root with the project venv:

    .venv/bin/python ios/TransfrCore/Tests/generate_fixtures.py

The two viz_* goldens are copied from core/viz_out/ (regenerate those with
core/viz_export.py --relation 5688517 --ref1 1 --ref2 16 [--details]).
"""
import json
import os
import shutil
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from api.schemas import (  # noqa: E402
    Place, Leg, Transfer, Journey, JourneysResponse,
    StationSuggestion, PlatformWalkResponse,
)

FIX = os.path.join(os.path.dirname(__file__), "TransfrCoreTests", "Fixtures")
os.makedirs(FIX, exist_ok=True)


def write(name: str, text: str) -> None:
    with open(os.path.join(FIX, name), "w") as f:
        f.write(text)
    print("wrote", name)


# --- /journeys: the canonical Hamburg -> Stuttgart ICE ----------------------
# Göttingen 7->8 feasible + Mannheim 4->5 tight => journey verdict "tight"
# (worst-wins). This is the throughline case DESIGN.md is grounded in.
resp = JourneysResponse(
    origin=Place(id="8002549", name="Hamburg Hbf", latitude=53.552736, longitude=10.006909),
    destination=Place(id="8098096", name="Stuttgart Hbf", latitude=48.784084, longitude=9.181635),
    departure_time="2026-07-16T08:00:00+02:00",
    journeys=[Journey(
        id="j-hh-s-1", date="2026-07-16", duration_s=19560, num_changes=2, verdict="tight",
        legs=[
            Leg(mode="TRANSIT", train_name="ICE 571",
                origin=Place(id="8002549", name="Hamburg Hbf"),
                destination=Place(id="8000128", name="Göttingen"),
                departure="2026-07-16T08:02:00+02:00", arrival="2026-07-16T09:47:00+02:00",
                planned_departure="2026-07-16T08:02:00+02:00", planned_arrival="2026-07-16T09:47:00+02:00",
                departure_platform="7", arrival_platform="7",
                departure_delay_s=0, arrival_delay_s=0, distance_m=248000),
            Leg(mode="TRANSIT", train_name="ICE 273",
                origin=Place(id="8000128", name="Göttingen"),
                destination=Place(id="8000244", name="Mannheim Hbf"),
                departure="2026-07-16T09:55:00+02:00", arrival="2026-07-16T12:19:00+02:00",
                planned_departure="2026-07-16T09:55:00+02:00", planned_arrival="2026-07-16T12:19:00+02:00",
                departure_platform="8", arrival_platform="4",
                departure_delay_s=0, arrival_delay_s=120, distance_m=331000),
            Leg(mode="TRANSIT", train_name="ICE 592",
                origin=Place(id="8000244", name="Mannheim Hbf"),
                destination=Place(id="8098096", name="Stuttgart Hbf"),
                departure="2026-07-16T12:24:00+02:00", arrival="2026-07-16T13:03:00+02:00",
                planned_departure="2026-07-16T12:24:00+02:00", planned_arrival="2026-07-16T13:03:00+02:00",
                departure_platform="5", arrival_platform="16",
                departure_delay_s=0, arrival_delay_s=0, distance_m=72000),
        ],
        transfers=[
            Transfer(at_station="Göttingen", relation_id=1234567, arrival_platform="7",
                     departure_platform="8", layover_s=480, walk_time_s=42.0,
                     walk_distance_m=18.0, verdict="feasible"),
            Transfer(at_station="Mannheim Hbf", relation_id=2345678, arrival_platform="4",
                     departure_platform="5", layover_s=300, walk_time_s=181.0,
                     walk_distance_m=95.0, verdict="tight"),
        ],
    )],
)
write("journeys_hamburg_stuttgart.json", resp.model_dump_json(indent=2))

# --- /stations suggestions ---------------------------------------------------
stations = [
    StationSuggestion(id="8002549", name="Hamburg Hbf", latitude=53.552736, longitude=10.006909, country="DE"),
    StationSuggestion(id="8098096", name="Stuttgart Hbf", latitude=48.784084, longitude=9.181635, country="DE"),
]
write("stations_suggest.json",
      json.dumps([json.loads(s.model_dump_json()) for s in stations], indent=2))

# --- /transfer debug walk ----------------------------------------------------
pw = PlatformWalkResponse(lat=52.525, lon=13.369, relation_id=5688517, station="Berlin Hbf",
                          from_platform="1", to_platform="16", found=True,
                          walk_time_s=122.0, walk_distance_m=107.0)
write("platform_walk_berlin.json", pw.model_dump_json(indent=2))

# --- viz_export goldens (copied from real core/viz_out output) ---------------
for src, dst in [("5688517_1_16.json", "viz_berlin_1_16.json"),
                 ("5688517_1_16_details.json", "viz_berlin_1_16_details.json")]:
    p = os.path.join(REPO, "core", "viz_out", src)
    if os.path.exists(p):
        shutil.copy(p, os.path.join(FIX, dst))
        print("copied", dst)
    else:
        print("SKIP (regenerate via core/viz_export.py):", src)
