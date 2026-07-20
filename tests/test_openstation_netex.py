"""
Tests for the DB OpenStation (NeTEx) crosswalk: the ingest parser, the overlay
lookup (api/openstation.py), and its wiring into transfer label-recovery +
accessibility (api/transfers.py).

All offline -- driven by a trimmed real Koeln StopPlace fixture
(tests/fixtures/openstation_koln.xml), no DB and no network. The DB-gated
end-to-end Koeln recovery is already covered in test_platform_tier3.py; this file
pins the NeTEx layer that feeds it.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import ingest_openstation_netex as ing  # noqa: E402  (core/dbgen on sys.path via conftest)
from api import openstation  # noqa: E402
import api.transfers as T  # noqa: E402
from api.transfers import (  # noqa: E402
    WalkResolution, _reconcile_label, _combine_step_free, _combine_has_lift,
    _recover_display_labels, _recover_accessibility,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "openstation_koln.xml")

# Koeln journey stop coordinates (as in test_platform_tier3.py). The feed labels
# these DELFI "89"/"88"/"84"; the public tracks are 7/6/2, on islands 6/7 and 2/3.
K89 = (50.943394, 6.9587010)   # feed "89" -> physical 7 (island 6/7)
K88 = (50.943320, 6.958548)    # feed "88" -> physical 6 (island 6/7)


# ---------------------------------------------------------------------------
# Pure: label parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Bahnsteig Gleis 1", "1"),
    ("Bahnsteig Gleis 6/7", "6/7"),
    ("Bahnsteig Gleise 2/3", "2/3"),
    ("Bahnsteig Gleis 3a", "3a"),
    ("Bahnsteig Gleis 10 / 11", "10/11"),
    ("Bussteig 4", None),      # not a Gleis
    ("7", None),               # bare sub-quay number carries no "Gleis" keyword
    ("", None),
    (None, None),
])
def test_public_label(name, expected):
    assert ing.public_label(name) == expected


# ---------------------------------------------------------------------------
# Ingest: parse the trimmed Koeln StopPlace fixture
# ---------------------------------------------------------------------------

def _overlay_from_fixture():
    with open(FIXTURE, "rb") as fh:
        return ing.build_overlay(fh)


def test_ingest_parses_koln_islands_and_drops_ungeoreferenced():
    overlay = _overlay_from_fixture()
    assert set(overlay) == {"de:05315:11201"}, "keyed by the clean parent DHID"
    st = overlay["de:05315:11201"]
    assert st["name"] == "Köln Hbf"
    assert st["eva"] == "8000207"
    labels = {q["public_label"] for q in st["quays"]}
    # The georeferenced island quays are kept; the bare-number "7" (no coordinate)
    # is dropped -- and every kept label is one the traveller actually reads.
    assert labels == {"1", "6/7", "8/9"}
    assert all(q["step_free"] is True and q["has_lift"] is True for q in st["quays"])


def test_ingest_quay_coordinate_is_equipment_centroid_mean():
    st = _overlay_from_fixture()["de:05315:11201"]
    q67 = next(q for q in st["quays"] if q["public_label"] == "6/7")
    assert round(q67["lat"], 5) == 50.94344 and round(q67["lon"], 5) == 6.95857
    # Gleis 1 has two lift centroids -> the stored point is their mean.
    q1 = next(q for q in st["quays"] if q["public_label"] == "1")
    assert round(q1["lat"], 5) == round((50.9430751 + 50.943903) / 2, 5)


def test_parse_stopplace_none_when_no_georeferenced_quays():
    import xml.etree.ElementTree as ET
    ns = "{http://www.netex.org.uk/netex}"
    sp = ET.parse(FIXTURE).getroot().find(f".//{ns}StopPlace")
    # A StopPlace whose georeferenced quays are all stripped yields None.
    for q in list(sp.findall(f"{ns}quays/{ns}Quay")):
        for ep in q.findall(f"{ns}equipmentPlaces"):
            q.remove(ep)
    assert ing.parse_stopplace(sp) is None


# ---------------------------------------------------------------------------
# Overlay lookup (api/openstation.py), backed by the fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def overlay_loaded(tmp_path):
    """Write the fixture-derived overlay to a temp file and point openstation at
    it, resetting its mtime cache so the lookup reads the fresh file."""
    path = tmp_path / "openstation_labels.json"
    path.write_text(json.dumps(_overlay_from_fixture()), encoding="utf-8")
    old = openstation._PATH
    openstation._PATH = str(path)
    openstation._cache = openstation._flat = openstation._cache_mtime = None
    yield
    openstation._PATH = old
    openstation._cache = openstation._flat = openstation._cache_mtime = None


def test_nearest_label_returns_island_for_koln_coord(overlay_loaded):
    assert openstation.available()
    assert openstation.nearest_label(*K89) == "6/7"
    assert openstation.nearest_label(*K88) == "6/7"


def test_accessibility_at_koln(overlay_loaded):
    acc = openstation.accessibility_at(*K89)
    assert acc == {"step_free": True, "wheelchair": True, "has_lift": True}


def test_lookup_misses_cleanly_far_away(overlay_loaded):
    # Paris coordinate -> no German NeTEx quay near -> honest None.
    assert openstation.nearest_label(48.8443, 2.3745) is None
    assert openstation.accessibility_at(48.8443, 2.3745) is None


def test_no_overlay_degrades_to_none(tmp_path):
    old = openstation._PATH
    openstation._PATH = str(tmp_path / "does_not_exist.json")
    openstation._cache = openstation._flat = openstation._cache_mtime = None
    try:
        assert not openstation.available()
        assert openstation.nearest_label(*K89) is None
        assert openstation.accessibility_at(*K89) is None
    finally:
        openstation._PATH = old
        openstation._cache = openstation._flat = openstation._cache_mtime = None


# ---------------------------------------------------------------------------
# Pure: label reconciliation + accessibility combination
# ---------------------------------------------------------------------------

def test_reconcile_prefers_specific_osm_when_component_of_netex_island():
    # Koeln: OSM per-track "7"/"6" AGREES with the NeTEx island "6/7" -> keep the
    # specific OSM track (this is what keeps test_platform_tier3's DB test green).
    assert _reconcile_label("7", "6/7", "89") == "7"
    assert _reconcile_label("6", "6/7", "88") == "6"


def test_reconcile_netex_fills_and_wins_conflicts():
    assert _reconcile_label(None, "6/7", "89") == "6/7"   # OSM absent -> NeTEx fills
    assert _reconcile_label("3", "6/7", "89") == "6/7"    # genuine conflict -> NeTEx wins
    assert _reconcile_label("7", None, "89") == "7"       # only OSM present


def test_reconcile_drops_label_equal_to_feed():
    assert _reconcile_label("89", None, "89") is None
    assert _reconcile_label(None, None, "89") is None


def test_combine_step_free():
    A = {"step_free": True, "has_lift": True}
    B = {"step_free": False, "has_lift": False}
    assert _combine_step_free(A, A) is True
    assert _combine_step_free(A, B) is False       # either not step-free -> False
    assert _combine_step_free(A, None) is None      # either uncovered -> unknown
    assert _combine_step_free({"step_free": None}, A) is None


def test_combine_has_lift():
    A = {"has_lift": True}
    assert _combine_has_lift(A, None) is True        # a lift at either end
    assert _combine_has_lift({"has_lift": False}, {"has_lift": False}) is False
    assert _combine_has_lift(None, None) is None


# ---------------------------------------------------------------------------
# Wiring into api/transfers -- no DB (OSM lookup stubbed, NeTEx from the fixture)
# ---------------------------------------------------------------------------

class _FakeConn:
    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def cursor(self): return self._Cur()


def test_recover_display_labels_uses_netex_when_osm_absent(overlay_loaded, monkeypatch):
    """A Koeln-style coordinate whose feed ref is a DELFI code and which OSM can't
    label still recovers a real sign -- from the NeTEx overlay."""
    monkeypatch.setattr(T, "nearest_platform_label", lambda cur, lat, lon, **k: None)
    res = WalkResolution(arrival_platform="89", departure_platform="88")
    _recover_display_labels(_FakeConn(), res, {"source_by_coord": True, "target_by_coord": True},
                            K89[0], K89[1], "89", K88[0], K88[1], "88")
    assert res.arrival_platform_actual == "6/7"
    assert res.departure_platform_actual == "6/7"


def test_recover_display_labels_keeps_specific_osm_track(overlay_loaded, monkeypatch):
    """When OSM has the per-track sign, it wins over the NeTEx island (they agree)."""
    monkeypatch.setattr(T, "nearest_platform_label",
                        lambda cur, lat, lon, **k: "7" if abs(lat - K89[0]) < 1e-6 else "6")
    res = WalkResolution(arrival_platform="89", departure_platform="88")
    _recover_display_labels(_FakeConn(), res, {"source_by_coord": True, "target_by_coord": True},
                            K89[0], K89[1], "89", K88[0], K88[1], "88")
    assert res.arrival_platform_actual == "7" and res.departure_platform_actual == "6"


def test_recover_display_labels_noop_when_not_by_coord(overlay_loaded):
    res = WalkResolution(arrival_platform="7", departure_platform="6")
    _recover_display_labels(_FakeConn(), res, {"source_by_coord": False, "target_by_coord": False},
                            K89[0], K89[1], "7", K88[0], K88[1], "6")
    assert res.arrival_platform_actual is None and res.departure_platform_actual is None


def test_recover_accessibility_from_overlay(overlay_loaded):
    res = WalkResolution(arrival_platform="89", departure_platform="88")
    _recover_accessibility(res, K89[0], K89[1], K88[0], K88[1])
    assert res.step_free is True
    assert res.has_lift is True


def test_recover_accessibility_none_without_overlay(tmp_path):
    old = openstation._PATH
    openstation._PATH = str(tmp_path / "missing.json")
    openstation._cache = openstation._flat = openstation._cache_mtime = None
    try:
        res = WalkResolution()
        _recover_accessibility(res, K89[0], K89[1], K88[0], K88[1])
        assert res.step_free is None and res.has_lift is None
    finally:
        openstation._PATH = old
        openstation._cache = openstation._flat = openstation._cache_mtime = None
