"""
Smoke test for a SELF-HOSTED MOTIS instance (deploy/motis-selfhost/).

The spike question this answers is *API-shape parity*: does a response from our
own timetable-only MOTIS survive transfr's real Transitous parser
(api/journeys.py) and yield the (station, platform, timing) tuples the core
pathfinder consumes at each interchange? If this passes against a self-host, then
swapping Transitous for it is a data/ops exercise, not a code one.

Opt-in and OFF by the offline suite -- it needs a running instance, so it is
skipped unless TRANSFR_MOTIS_SMOKE=1. Run it against your spike like:

    TRANSFR_MOTIS_SMOKE=1 \
    TRANSFR_MOTIS_BASE=http://localhost:8080 \
    .venv/bin/python -m pytest tests/test_motis_selfhost.py -q

TRANSFR_MOTIS_BASE must be set BEFORE import so api.config/api.journeys pick it up
(they resolve the base URL once at module load); passing it on the pytest command
line as above does that. Default coordinate pair is Zürich HB -> Bern (the CH
default dataset in config.yml, which is platform/track-rich); override with
TRANSFR_MOTIS_SMOKE_FROM / _TO as "lat,lon".
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

SMOKE = pytest.mark.skipif(
    os.environ.get("TRANSFR_MOTIS_SMOKE") != "1",
    reason="self-hosted MOTIS smoke test; set TRANSFR_MOTIS_SMOKE=1 (needs a running instance)",
)

FROM = os.environ.get("TRANSFR_MOTIS_SMOKE_FROM", "47.3779,8.5403")  # Zürich HB
TO = os.environ.get("TRANSFR_MOTIS_SMOKE_TO", "46.9489,7.4390")     # Bern

# Fields api/journeys._extract_leg guarantees on every parsed leg -- the contract
# the core/ pathfinder feeds on (see tests/test_journeys.py's core-ready checks).
REQUIRED_LEG_KEYS = {
    "origin", "destination", "departure", "arrival",
    "departure_platform", "arrival_platform", "mode",
}
PLACE_KEYS = {"id", "name", "latitude", "longitude"}


def _plan(base: str) -> dict:
    """Query the self-hosted /api/v5/plan exactly as journeys.search_journeys does."""
    from api.journeys import _get_session

    when = datetime.now(timezone.utc) + timedelta(hours=1)
    resp = _get_session().get(
        f"{base}/api/v5/plan",
        params={
            "fromPlace": FROM,
            "toPlace": TO,
            "time": when.isoformat(),
            "numItineraries": "3",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


@SMOKE
def test_env_wiring_points_at_selfhost():
    """The TRANSFR_MOTIS_BASE override actually reaches the code path."""
    import api.config as config
    from api import journeys

    base = os.environ.get("TRANSFR_MOTIS_BASE", "http://localhost:8080")
    assert config.MOTIS_BASE == base.rstrip("/"), (
        "api.config.MOTIS_BASE did not pick up TRANSFR_MOTIS_BASE -- set it on the "
        "pytest command line so it is in the environment before import."
    )
    assert journeys.PLAN_URL == f"{base.rstrip('/')}/api/v5/plan"


@SMOKE
def test_selfhost_plan_parses_into_core_ready_shape():
    """A self-hosted /plan response survives transfr's real parser."""
    from api.journeys import _extract_leg

    base = os.environ.get("TRANSFR_MOTIS_BASE", "http://localhost:8080")
    data = _plan(base)

    itineraries = data.get("itineraries")
    assert itineraries, f"self-host returned no itineraries for {FROM} -> {TO}: keys={list(data)}"

    transit_leg_seen = False
    timed_transit_leg_seen = False

    for itin in itineraries:
        for raw_leg in itin.get("legs", []):
            leg = _extract_leg(raw_leg)  # the actual production parser

            # Shape parity: every key the pipeline reads is present and places are
            # well formed (values may be None for platform-sparse networks; keys
            # must exist).
            assert REQUIRED_LEG_KEYS <= set(leg), (
                f"parsed leg missing keys {REQUIRED_LEG_KEYS - set(leg)}"
            )
            assert PLACE_KEYS <= set(leg["origin"])
            assert PLACE_KEYS <= set(leg["destination"])

            if leg["mode"] != "walking":
                transit_leg_seen = True
                if leg["departure"] and leg["arrival"]:
                    timed_transit_leg_seen = True

    assert transit_leg_seen, "no transit legs parsed -- MOTIS returned walk-only or empty routing"
    assert timed_transit_leg_seen, "transit legs carried no usable departure/arrival timestamps"


@SMOKE
def test_selfhost_interchanges_extractable():
    """The interchange view (api/transitous.py) works on a self-hosted journey.

    This is what the pipeline actually iterates: consecutive train legs paired
    into changes of train. Proves the whole journeys -> transitous seam holds
    against the self-host, not just per-leg parsing.
    """
    from api import journeys as _journeys
    from api import transitous

    base = os.environ.get("TRANSFR_MOTIS_BASE", "http://localhost:8080")
    data = _plan(base)

    # Reshape into the journey dict transitous.transit_legs/interchanges expect.
    shaped = {
        "legs": [_journeys._extract_leg(l) for l in data["itineraries"][0].get("legs", [])]
    }
    transit = transitous.transit_legs(shaped)
    assert transit, "first itinerary had no transit legs to interchange over"

    # interchanges() must not raise and returns one fewer pair than transit legs.
    pairs = transitous.interchanges(shaped)
    assert len(pairs) == max(0, len(transit) - 1)
