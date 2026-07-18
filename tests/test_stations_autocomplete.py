"""
English-exonym search for the CSV station autocomplete (api/stations.py, #54).

Offline and deterministic: api.stations loads the vendored stations.csv at import
-- no DB, no network. The contract under test: an English exonym ("Munich",
"Vienna", ...) must FIND the station, while the displayed/returned `name` stays
the canonical local name ("München Hbf") -- the exonym is a search key only. That
keeps the iOS re-resolve contract intact: the app commits the shown name string
and /journeys re-resolves it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api import stations  # noqa: E402

# (English exonym typed, expected first result = the local MAIN station).
# All of these are covered by the CSV's own info:en column -- no external data.
EXONYMS = [
    ("Munich", "München Hbf"),
    ("Vienna", "Wien Hbf"),
    ("Cologne", "Köln Hbf"),
    ("Rome", "Roma Termini"),
    ("Florence", "Firenze Santa Maria Novella"),
    ("Venice", "Venezia Santa Lucia"),
    ("Warsaw", "Warszawa-Centralna"),
]


@pytest.mark.parametrize("query,expected_local_name", EXONYMS)
def test_autocomplete_english_exonym_surfaces_local_main_station(query, expected_local_name):
    results = stations.autocomplete_station(query)
    assert results, f"{query!r} returned nothing"
    assert results[0]["name"] == expected_local_name


@pytest.mark.parametrize("query,expected_local_name", EXONYMS)
def test_resolve_english_exonym_returns_local_main_station(query, expected_local_name):
    assert stations.resolve_station(query)["name"] == expected_local_name


def test_display_name_is_local_never_the_alias():
    # The suggestion must show "München Hbf", never "Munich": the app commits the
    # displayed string and /journeys re-resolves it, so the alias must not leak.
    top = stations.autocomplete_station("Munich")[0]
    assert top["name"] == "München Hbf"
    # And the result carries exactly the StationSuggestion wire fields -- no alias.
    assert set(top) == {"id", "name", "latitude", "longitude", "country"}


def test_one_result_per_station_despite_matching_name_and_alias():
    # "mun" matches many München stations by BOTH their local name ("münchen …")
    # and their alias ("munich"); each station must still appear at most once.
    results = stations.autocomplete_station("mun", max_results=25)
    ids = [r["id"] for r in results]
    assert len(ids) == len(set(ids))


def test_shared_city_exonym_ranks_main_station_first():
    # "Munich" is the exonym for the whole München cluster; the Hauptbahnhof
    # (main) must come first, not e.g. München Harras (earlier in CSV order).
    results = stations.autocomplete_station("Munich")
    assert results[0]["name"] == "München Hbf"
    assert results[0]["id"] == stations.resolve_station("Munich")["id"]


def test_local_name_and_accent_search_unchanged():
    # Regression: the pre-existing local-name and accent-folded paths still work.
    assert stations.autocomplete_station("Frankf")[0]["name"] == "Frankfurt (Main) Hbf"
    assert stations.autocomplete_station("Zurich")[0]["name"] == "Zürich HB"
    assert stations.resolve_station("München Hbf")["name"] == "München Hbf"
    assert stations.resolve_station("Köln")["name"] == "Köln"


def test_own_local_name_outranks_another_stations_alias():
    # Regression for the resolve tiebreak: a bare query equal to a station's own
    # local name resolves to THAT station, not a namesake whose free-form info:en
    # happens to equal it (only a handful of such collisions exist in the CSV).
    assert stations.resolve_station("Bari")["name"] == "Bari"
    assert stations.resolve_station("Toulouse")["name"] == "Toulouse"


def test_unresolvable_name_still_raises():
    with pytest.raises(ValueError):
        stations.resolve_station("Nowheresville-XYZ-123")
