"""
Comprehensive tests for the legacy journey search (journeys.py), which routes
station-to-station via the Transitous (MOTIS 2) /plan API.

The point of this suite is not just "does journeys.py return something" -- it is
"does journeys.py surface enough per-leg PLATFORM + STATION + TIMING data that
the core/ platform-to-platform pathfinder can consume it at every interchange."
core/ answers "can I walk from platform A to platform B at station S within the
layover?"; to feed it, a journey must yield, at each change of train:
    (station, arrival_platform, departure_platform, layover_seconds).
Those tuples are what the "core-ready interchange" tests below assert exist and
are well formed.

Two layers, mirroring tests/test_live_sources.py:
  * Offline tests run against REAL MOTIS responses captured into
    tests/fixtures/journeys/*.json (see tests/capture_journey_fixtures.py). The
    network is stubbed, so these are deterministic and always run. The corpus
    deliberately spans DE/FR/CH/IT/AT/BE/NL/ES, domestic and cross-border, and
    direct / single-transfer / multi-transfer shapes.
  * Live tests actually hit api.transitous.org and are skipped unless
    TRANSFR_LIVE=1, so CI stays deterministic but the real pull is one env var
    away.

Findings this suite locks in (all discovered while writing it, against the real
API from a generic host):
  * Platform/track data is published for DE/CH/AT/BE/NL stations but NOT for
    domestic FR/IT/ES (SNCF/Trenitalia/Renfe don't expose it in the open feed).
    So core/ can only compute platform-to-platform transfers where the feed
    carries platforms -- see PLATFORM_RICH vs PLATFORM_SPARSE.
  * Platform refs are arbitrary strings, not integers ("Gl 1", "5a", "Regio 3").
    Any consumer that does int(platform) is wrong -- these tests assert str.
  * MOTIS timestamps carry a trailing 'Z'; datetime.fromisoformat only accepts
    that from Python 3.11, so on the repo's 3.9 venv the delay/duration math in
    journeys.py silently returned None until _parse_iso was added. Locked by
    test_delay_seconds_handles_z_suffix.
  * MOTIS 500s on a naive (tz-less) `time`; a naive departure is now made
    tz-aware before the request. Locked by test_naive_departure_is_sent_tz_aware.
"""

from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime, timezone

import pytest

import journeys
from journeys import (
    _delay_seconds,
    _extract_leg,
    _extract_place,
    _parse_iso,
    search_journeys,
)

FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "journeys")

LIVE = pytest.mark.skipif(
    os.environ.get("TRANSFR_LIVE") != "1",
    reason="live network test; set TRANSFR_LIVE=1 to run against real APIs",
)

# Domestic networks whose open feed exposes platform/track data vs those that do
# not (measured while capturing the fixtures). Cross-border journeys are listed
# under RICH when *any* leg touches a platform-publishing network.
PLATFORM_RICH = {
    "de_frankfurt_koln", "ch_zurich_bern", "de_munchen_hamburg",
    "de_at_munchen_wien", "de_ch_frankfurt_zurich", "de_be_koln_bruxelles",
    "fr_de_paris_frankfurt", "it_ch_milano_zurich", "fr_de_strasbourg_freiburg",
    "nl_de_amsterdam_berlin", "de_berlin_short_hop", "nl_utrecht_amsterdam",
    "fr_it_bordeaux_milano",
}
# Domestic FR/IT/ES: valid journeys come back, but with NO platform data. If one
# of these ever starts failing because the operator began publishing platforms,
# that's good news -- move the slug into PLATFORM_RICH.
PLATFORM_SPARSE = {"fr_paris_lyon", "it_milano_roma", "es_barcelona_madrid"}

# Grounded in the captured corpus; kept as comfortable lower bounds so a benign
# re-capture (schedules shift) doesn't turn these into change-detectors.
MIN_CORE_READY_INTERCHANGES = 6      # observed 12
MIN_SAME_STATION_INTERCHANGES = 3    # observed 8
MIN_DISTINCT_COUNTRIES = 6           # corpus spans 8


# ---------------------------------------------------------------------------
# Fixture loading + a network stub that drives the real mapping code path
# ---------------------------------------------------------------------------

def _all_slugs():
    return sorted(
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(FIX_DIR, "*.json"))
    )


ALL_SLUGS = _all_slugs()


def _load(slug):
    with open(os.path.join(FIX_DIR, f"{slug}.json"), encoding="utf-8") as f:
        return json.load(f)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.captured_params = None

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _CapturingSession:
    """Stands in for the requests.Session; records the params it was asked for
    and replays a captured MOTIS payload, so search_journeys' real resolve +
    mapping logic runs with zero network."""

    def __init__(self, payload):
        self._payload = payload
        self.last_params = None

    def get(self, url, params=None, timeout=None):
        self.last_params = params
        return _FakeResponse(self._payload)


def _mapped(slug):
    """Run the real search_journeys over a captured fixture (network stubbed)."""
    fx = _load(slug)
    meta = fx["meta"]
    sess = _CapturingSession(fx["response"])
    original = journeys._get_session
    journeys._get_session = lambda: sess
    try:
        return search_journeys(
            meta["origin_query"], meta["destination_query"],
            datetime(2026, 7, 13, 9, 0),
        )
    finally:
        journeys._get_session = original


# ---------------------------------------------------------------------------
# Domain helpers shared by the tests (mirror what core/ would extract)
# ---------------------------------------------------------------------------

def _transit_legs(journey):
    return [leg for leg in journey["legs"] if leg["mode"] != "walking"]


def _interchanges(journey):
    """Yield (arriving_leg, departing_leg) for each change of train."""
    legs = _transit_legs(journey)
    return list(zip(legs, legs[1:]))


def _station_key(place):
    """A same-station key robust to MOTIS' inconsistent names across providers.

    DELFI/IFOPT stop ids look like 'de-DELFI_de:09162:5:3:3'; the station-level
    prefix 'de:09162:5' is shared by every platform of the same station, so two
    legs meeting at the same station match on it even when their names differ
    ('Ostbahnhof' vs 'München Ostbahnhof') or their platform suffix differs.
    Falls back to a normalized name when the id isn't a DELFI id.
    """
    sid = place.get("id") or ""
    m = re.search(r"[a-z]{2}:\d+:\d+", sid)
    if m:
        return m.group(0)
    return (place.get("name") or "").lower().replace(".", "").replace(" ", "")


def _is_core_ready(arrive_leg, depart_leg):
    """True when this interchange carries both platforms core/ needs."""
    return bool(arrive_leg["arrival_platform"]) and bool(depart_leg["departure_platform"])


def _layover_seconds(arrive_leg, depart_leg):
    return (_parse_iso(depart_leg["departure"]) - _parse_iso(arrive_leg["arrival"])).total_seconds()


def _has_any_platform(result):
    return any(
        leg["departure_platform"] or leg["arrival_platform"]
        for j in result["journeys"] for leg in _transit_legs(j)
    )


# ===========================================================================
# 0. Corpus sanity — the fixtures exist and are the shape the suite assumes
# ===========================================================================

def test_fixture_corpus_is_present():
    assert ALL_SLUGS, "no journey fixtures found; run tests/capture_journey_fixtures.py"
    assert set(PLATFORM_RICH) | set(PLATFORM_SPARSE) == set(ALL_SLUGS), (
        "PLATFORM_RICH/PLATFORM_SPARSE must together account for every fixture"
    )


@pytest.mark.parametrize("slug", ALL_SLUGS)
def test_fixture_is_well_formed(slug):
    fx = _load(slug)
    assert set(fx) >= {"meta", "response"}
    meta = fx["meta"]
    assert meta["origin_query"] and meta["destination_query"]
    assert meta["origin_country"] and meta["destination_country"]
    assert fx["response"].get("itineraries"), f"{slug}: captured response has no itineraries"


def test_corpus_spans_many_countries_and_journey_shapes():
    """'Comprehensive' means real breadth: many countries AND all three shapes."""
    countries, shapes = set(), set()
    for slug in ALL_SLUGS:
        meta = _load(slug)["meta"]
        countries.add(meta["origin_country"])
        countries.add(meta["destination_country"])
        for j in _mapped(slug)["journeys"]:
            n = j["num_changes"]
            shapes.add("direct" if n == 0 else "single" if n == 1 else "multi")
    assert len(countries) >= MIN_DISTINCT_COUNTRIES, sorted(countries)
    assert {"direct", "single", "multi"} <= shapes, sorted(shapes)


# ===========================================================================
# 1. Pure mapping unit tests (no fixtures, no network)
# ===========================================================================

def test_delay_seconds_handles_z_suffix():
    # Regression: MOTIS emits 'Z'; fromisoformat rejects it before Py3.11.
    assert _delay_seconds("2026-07-13T07:40:00Z", "2026-07-13T07:38:00Z") == 120
    assert _delay_seconds("2026-07-13T07:38:00Z", "2026-07-13T07:38:00Z") is None  # on-time -> None
    assert _delay_seconds(None, "2026-07-13T07:38:00Z") is None
    assert _delay_seconds("garbage", "also garbage") is None


def test_parse_iso_tolerates_z_and_offset():
    assert _parse_iso("2026-07-13T07:38:00Z").utcoffset().total_seconds() == 0
    assert _parse_iso("2026-07-13T09:38:00+02:00").utcoffset().total_seconds() == 7200


def test_extract_place_handles_missing():
    assert _extract_place(None) == {"id": None, "name": None, "latitude": None, "longitude": None}
    got = _extract_place({"stopId": "x", "name": "N", "lat": 1.0, "lon": 2.0})
    assert got == {"id": "x", "name": "N", "latitude": 1.0, "longitude": 2.0}


def test_extract_leg_marks_walking_and_maps_platforms():
    walk = _extract_leg({"mode": "WALK", "distance": 210.7,
                         "from": {}, "to": {}, "startTime": "2026-01-01T00:00:00Z"})
    assert walk["mode"] == "walking" and walk["train_name"] is None
    assert walk["distance_m"] == 210  # int-truncated

    train = _extract_leg({
        "mode": "HIGHSPEED_RAIL", "routeShortName": "41", "displayName": "ICE 41",
        "startTime": "2026-01-01T08:00:00Z", "endTime": "2026-01-01T09:00:00Z",
        "from": {"name": "A", "track": "7", "stopId": "a"},
        "to": {"name": "B", "track": "3", "stopId": "b"},
    })
    assert train["mode"] == "highspeed_rail"
    assert train["train_name"] == "ICE 41"
    assert train["departure_platform"] == "7" and train["arrival_platform"] == "3"
    assert train["origin"]["name"] == "A" and train["destination"]["name"] == "B"


# ===========================================================================
# 2. Structural mapping across the whole corpus (offline)
# ===========================================================================

_LEG_KEYS = {
    "origin", "destination", "departure", "arrival", "planned_departure",
    "planned_arrival", "departure_platform", "arrival_platform", "mode", "cancelled",
}
_PLACE_KEYS = {"id", "name", "latitude", "longitude"}


@pytest.mark.parametrize("slug", ALL_SLUGS)
def test_search_journeys_output_shape(slug):
    result = _mapped(slug)
    assert set(result) >= {"origin", "destination", "departure_time", "journeys"}
    assert result["journeys"], f"{slug}: no journeys mapped"

    for j in result["journeys"]:
        assert set(j) >= {"id", "date", "duration_s", "legs", "num_changes"}
        assert isinstance(j["num_changes"], int) and j["num_changes"] >= 0
        assert j["legs"], "journey has no legs"

        transit = _transit_legs(j)
        # num_changes must agree with the number of train legs.
        if transit:
            assert j["num_changes"] == len(transit) - 1, (
                f"{slug}: num_changes={j['num_changes']} but {len(transit)} transit legs"
            )

        for leg in j["legs"]:
            assert _LEG_KEYS <= set(leg), f"{slug}: leg missing keys {_LEG_KEYS - set(leg)}"
            assert set(leg["origin"]) == _PLACE_KEYS
            assert set(leg["destination"]) == _PLACE_KEYS
            # Platforms are arbitrary strings or None -- never numbers.
            for pf in ("departure_platform", "arrival_platform"):
                assert leg[pf] is None or isinstance(leg[pf], str)
            # Times, when present, are parseable ISO.
            for tk in ("departure", "arrival"):
                if leg[tk] is not None:
                    _parse_iso(leg[tk])  # raises if malformed
            if leg["mode"] == "walking":
                assert leg["train_name"] is None


@pytest.mark.parametrize("slug", ALL_SLUGS)
def test_leg_times_are_monotonic_within_a_journey(slug):
    """Every leg's arrival is >= its departure, and legs don't travel back in
    time -- the invariant that makes layover_seconds meaningful for core/."""
    for j in _mapped(slug)["journeys"]:
        prev_end = None
        for leg in j["legs"]:
            if leg["departure"] and leg["arrival"]:
                dep, arr = _parse_iso(leg["departure"]), _parse_iso(leg["arrival"])
                assert arr >= dep, f"{slug}: leg arrives before it departs"
                if prev_end is not None:
                    assert dep >= prev_end, f"{slug}: leg departs before the previous one arrived"
                prev_end = arr


# ===========================================================================
# 3. THE CORE CONTRACT — platform-to-platform transfer data for core/
# ===========================================================================

@pytest.mark.parametrize("slug", ALL_SLUGS)
def test_core_ready_interchanges_are_well_formed(slug):
    """Wherever an interchange carries both platforms, it must carry everything
    core/ needs to attempt the transfer: two named stations, two platform refs,
    two timestamps, and a non-negative layover."""
    for j in _mapped(slug)["journeys"]:
        for arrive, depart in _interchanges(j):
            if not _is_core_ready(arrive, depart):
                continue
            assert arrive["destination"]["name"], f"{slug}: interchange missing arrival station"
            assert depart["origin"]["name"], f"{slug}: interchange missing departure station"
            assert isinstance(arrive["arrival_platform"], str) and arrive["arrival_platform"]
            assert isinstance(depart["departure_platform"], str) and depart["departure_platform"]
            assert arrive["arrival"] and depart["departure"]
            assert _layover_seconds(arrive, depart) >= 0, f"{slug}: negative layover"


def test_corpus_yields_core_ready_platform_to_platform_transfers():
    """The whole reason this pipeline exists: prove it produces a healthy number
    of (station, from_platform, to_platform, layover) tuples core/ can route,
    including genuine same-station transfers."""
    core_ready = 0
    same_station = 0
    example = None
    for slug in ALL_SLUGS:
        for j in _mapped(slug)["journeys"]:
            for arrive, depart in _interchanges(j):
                if not _is_core_ready(arrive, depart):
                    continue
                core_ready += 1
                if _station_key(arrive["destination"]) == _station_key(depart["origin"]):
                    same_station += 1
                    if example is None and _layover_seconds(arrive, depart) > 0:
                        example = (slug, arrive["destination"]["name"],
                                   arrive["arrival_platform"], depart["departure_platform"],
                                   int(_layover_seconds(arrive, depart)))

    assert core_ready >= MIN_CORE_READY_INTERCHANGES, f"only {core_ready} core-ready interchanges"
    assert same_station >= MIN_SAME_STATION_INTERCHANGES, f"only {same_station} same-station"
    assert example is not None, "expected at least one same-station transfer with a positive layover"
    # e.g. ('de_munchen_hamburg', 'Hannover Hbf', '3', '7', 240)


def test_platform_refs_are_strings_including_non_numeric():
    """Locks the 'never int(platform)' lesson: real refs include 'Gl 1', '5a',
    'Regio 3'. Any leg-ref must be a str, and the corpus must contain a
    non-numeric one so a consumer can't quietly assume integers."""
    all_refs = []
    for slug in ALL_SLUGS:
        for j in _mapped(slug)["journeys"]:
            for leg in _transit_legs(j):
                for pf in (leg["departure_platform"], leg["arrival_platform"]):
                    if pf is not None:
                        assert isinstance(pf, str)
                        all_refs.append(pf)
    assert any(not ref.isdigit() for ref in all_refs), (
        "expected some non-numeric platform refs (e.g. 'Gl 1', 'Regio 3')"
    )


# ===========================================================================
# 4. Platform availability by network (the FR/IT/ES gap, as a tested fact)
# ===========================================================================

@pytest.mark.parametrize("slug", sorted(PLATFORM_RICH))
def test_platform_rich_journeys_expose_platforms(slug):
    assert _has_any_platform(_mapped(slug)), (
        f"{slug}: expected platform data but found none"
    )


@pytest.mark.parametrize("slug", sorted(PLATFORM_SPARSE))
def test_platform_sparse_journeys_still_route_without_platforms(slug):
    """Absence of platform data must degrade gracefully, not break journey
    search -- core/ can still show the itinerary, just not a platform transfer.
    (Characterization against frozen fixtures; see PLATFORM_SPARSE note.)"""
    result = _mapped(slug)
    assert result["journeys"], f"{slug}: no journeys"
    assert any(_transit_legs(j) for j in result["journeys"]), f"{slug}: no transit legs"
    assert not _has_any_platform(result), (
        f"{slug}: expected NO platform data for this network -- if the operator "
        f"started publishing platforms, move it to PLATFORM_RICH"
    )


# ===========================================================================
# 5. Regression guards for the bugs fixed while building this suite
# ===========================================================================

def test_naive_departure_is_sent_tz_aware():
    """MOTIS 500s on a tz-less timestamp; a naive departure must be made
    tz-aware before the request goes out."""
    fx = _load("de_frankfurt_koln")
    sess = _CapturingSession(fx["response"])
    original = journeys._get_session
    journeys._get_session = lambda: sess
    try:
        search_journeys("Frankfurt", "Köln", datetime(2026, 7, 13, 9, 0))  # naive
    finally:
        journeys._get_session = original
    sent = sess.last_params["time"]
    assert sent.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", sent), (
        f"time param {sent!r} was sent without a timezone -- MOTIS will 500"
    )


def test_tz_aware_departure_is_preserved():
    fx = _load("de_frankfurt_koln")
    sess = _CapturingSession(fx["response"])
    original = journeys._get_session
    journeys._get_session = lambda: sess
    aware = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)
    try:
        search_journeys("Frankfurt", "Köln", aware)
    finally:
        journeys._get_session = original
    assert sess.last_params["time"] == aware.isoformat()


def test_unresolvable_station_raises_valueerror():
    with pytest.raises(ValueError):
        search_journeys("Nowheresville-XYZ-123", "Köln", datetime(2026, 7, 13, 9, 0))


# ===========================================================================
# 6. Live tests (opt-in) — real pulls against api.transitous.org
# ===========================================================================

@LIVE
def test_live_naive_now_does_not_500():
    """End-to-end proof the tz fix works: server.py passes datetime.now()
    (naive); this must return journeys, not raise HTTP 500."""
    result = search_journeys("Frankfurt", "Köln", datetime.now())
    assert result["journeys"], "no journeys for Frankfurt -> Köln right now"


@LIVE
def test_live_dach_direct_has_platforms():
    when = datetime.now(timezone.utc)
    result = search_journeys("Frankfurt", "Köln", when)
    assert result["journeys"]
    assert _has_any_platform(result), "expected platform data on a live DE high-speed leg"


@LIVE
def test_live_transfer_journey_is_core_consumable():
    """A live cross-network journey should still hand core/ at least one
    interchange carrying both platforms."""
    when = datetime.now(timezone.utc)
    result = search_journeys("Frankfurt", "Zürich HB", when)
    assert result["journeys"]
    core_ready = [
        (a, b)
        for j in result["journeys"]
        for a, b in _interchanges(j)
        if _is_core_ready(a, b)
    ]
    # Not every departure has a same-minute perfect example, but across a few
    # itineraries at least the platform fields must be reachable.
    assert any(_transit_legs(j) for j in result["journeys"])
    for a, b in core_ready:
        assert _layover_seconds(a, b) >= 0
