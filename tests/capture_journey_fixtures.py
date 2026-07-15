"""Capture REAL Transitous (MOTIS 2) /plan responses as test fixtures.

    .venv/bin/python tests/capture_journey_fixtures.py           # fill in what's missing
    .venv/bin/python tests/capture_journey_fixtures.py --force   # re-capture everything

Follows this repo's fixture methodology (see tests/test_live_sources.py): the
offline test suite runs against captured REAL responses so it is deterministic,
while the live layer (TRANSFR_LIVE=1) can hit the network directly.

The matrix below deliberately spans countries, cities, train types (high-speed,
intercity, regional, suburban/urban) and journey shapes (direct, single- and
multi-transfer) so the suite proves journeys.py surfaces the platform/track +
station + timing data the core/ backend needs at every interchange.

Resumable and interrupt-safe: each fixture is written the instant it is fetched,
already-present fixtures are skipped (unless --force), and Ctrl-C stops cleanly
without corrupting a partially written file.
"""

from __future__ import annotations

import json
import os
import sys
import time as _time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.journeys import PLAN_URL, _get_session  # noqa: E402
from api.stations import resolve_station  # noqa: E402

FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "journeys")

# Monday 09:00 CEST — a weekday daytime with full long-distance service.
WHEN = datetime(2026, 7, 13, 9, 0, tzinfo=timezone(timedelta(hours=2)))
NUM_ITINERARIES = 3

# (origin, destination, slug, note) — origin/destination must resolve in stations.csv.
MATRIX = [
    ("Frankfurt",           "Köln",             "de_frankfurt_koln",         "DE domestic high-speed (ICE), usually direct"),
    ("Paris Gare de Lyon",  "Lyon Part-Dieu",   "fr_paris_lyon",             "FR domestic TGV, direct"),
    ("Milano Centrale",     "Roma Termini",     "it_milano_roma",            "IT domestic Frecciarossa, direct"),
    ("Zürich HB",           "Bern",             "ch_zurich_bern",            "CH domestic InterCity, direct"),
    ("München Hbf",         "Hamburg Hbf",      "de_munchen_hamburg",        "DE long ICE, many intermediate stops"),
    ("München Hbf",         "Wien Hbf",         "de_at_munchen_wien",        "DE->AT cross-border Railjet"),
    ("Frankfurt",           "Zürich HB",        "de_ch_frankfurt_zurich",    "DE->CH cross-border ICE/EC"),
    ("Köln",                "Bruxelles-Midi",   "de_be_koln_bruxelles",      "DE->BE cross-border high-speed"),
    ("Paris Gare de Lyon",  "Frankfurt",        "fr_de_paris_frankfurt",     "FR->DE cross-border, expect a transfer"),
    ("Milano Centrale",     "Zürich HB",        "it_ch_milano_zurich",       "IT->CH cross-border EuroCity (Gotthard)"),
    ("Strasbourg",          "Freiburg",         "fr_de_strasbourg_freiburg", "FR->DE regional cross-border, expect transfer(s)"),
    ("Amsterdam",           "Berlin Hbf",       "nl_de_amsterdam_berlin",    "NL->DE InterCity, long, expect transfer"),
    ("Berlin Hbf",          "Berlin Ostbahnhof","de_berlin_short_hop",       "intra-city hop: suburban/urban legs, small platforms"),
    ("Utrecht Centraal",    "Amsterdam",        "nl_utrecht_amsterdam",      "NL short domestic, sprinter/IC"),
    ("Barcelona Sants",     "Madrid",           "es_barcelona_madrid",       "ES domestic AVE high-speed"),
    ("Bordeaux",            "Milano Centrale",  "fr_it_bordeaux_milano",     "long multi-country, expect multiple transfers"),
]


def _fixture_path(slug: str) -> str:
    return os.path.join(FIX_DIR, f"{slug}.json")


def _atomic_write(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)  # atomic: never leaves a half-written fixture


def _summarize(response: dict) -> str:
    its = response.get("itineraries", []) or []
    transit = _WALK = {"WALK", "BIKE", "CAR", "BIKE_SHARING", "CAR_SHARING", "SCOOTER_SHARING"}
    max_transit = 0
    any_track = False
    for it in its:
        legs = [l for l in it.get("legs", []) if l.get("mode") not in transit]
        max_transit = max(max_transit, len(legs))
        for l in legs:
            if (l.get("from") or {}).get("track") or (l.get("to") or {}).get("track"):
                any_track = True
    shape = "direct" if max_transit <= 1 else f"{max_transit - 1}-transfer"
    return f"{len(its)} itin, up to {max_transit} transit legs ({shape}), track={'yes' if any_track else 'NONE'}"


def capture_one(origin: str, destination: str, slug: str, note: str, force: bool) -> str:
    path = _fixture_path(slug)
    if os.path.exists(path) and not force:
        return "skip (exists)"

    o = resolve_station(origin)
    d = resolve_station(destination)
    resp = _get_session().get(
        PLAN_URL,
        params={
            "fromPlace": f"{o['latitude']},{o['longitude']}",
            "toPlace": f"{d['latitude']},{d['longitude']}",
            "time": WHEN.isoformat(),
            "numItineraries": str(NUM_ITINERARIES),
        },
        timeout=25,
    )
    resp.raise_for_status()
    response = resp.json()

    _atomic_write(path, {
        "meta": {
            "slug": slug,
            "note": note,
            "origin_query": origin,
            "destination_query": destination,
            "origin_name": o["name"],
            "destination_name": d["name"],
            "origin_country": o.get("country"),
            "destination_country": d.get("country"),
            "when": WHEN.isoformat(),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": PLAN_URL,
        },
        "response": response,
    })
    return _summarize(response)


def main() -> int:
    force = "--force" in sys.argv
    os.makedirs(FIX_DIR, exist_ok=True)
    print(f"Capturing {len(MATRIX)} journey fixtures into {FIX_DIR}")
    print(f"when={WHEN.isoformat()}  numItineraries={NUM_ITINERARIES}  force={force}\n")

    done, skipped, failed = 0, 0, 0
    try:
        for origin, destination, slug, note in MATRIX:
            try:
                status = capture_one(origin, destination, slug, note, force)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 — log and continue; one bad pair shouldn't abort the run
                failed += 1
                print(f"  FAIL {slug:28} {origin} -> {destination}: {type(e).__name__}: {e}")
                continue
            if status.startswith("skip"):
                skipped += 1
                print(f"  ---- {slug:28} {status}")
            else:
                done += 1
                print(f"  OK   {slug:28} {o_c(origin)}->{o_c(destination)}  {status}")
                _time.sleep(1.0)  # be polite to the free public API
    except KeyboardInterrupt:
        print("\nInterrupted — fixtures captured so far are saved and valid; re-run to continue.")
        return 130

    print(f"\nDone. captured={done} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


def o_c(name: str) -> str:
    try:
        return resolve_station(name).get("country") or "??"
    except Exception:
        return "??"


if __name__ == "__main__":
    raise SystemExit(main())
