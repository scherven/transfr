"""
Tests for the enrichment pipeline (api/pipeline.py).

  * Pure: the journey-level verdict rollup.
  * Offline: enrich() over REAL captured MOTIS fixtures with the transfer
    assessment stubbed (shape + verdict wiring), and enrich() over a
    platform-less FR/IT/ES fixture with the REAL assessor and no DB (proving
    graceful no_platform_data degradation).
  * DB-gated: enrich() over a DACH fixture against the real transfr_eu DB,
    proving the whole chain runs on real journey data.
"""

import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from api import journeys  # noqa: E402
import api.pipeline as P  # noqa: E402
from api.pipeline import enrich, plan_journeys, rollup_verdict  # noqa: E402
from api.transfers import (  # noqa: E402
    FEASIBLE, INFEASIBLE, TIGHT, UNKNOWN, NO_PLATFORM_DATA, TransferAssessment,
)

FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "journeys")

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)

_ALLOWED_VERDICTS = {FEASIBLE, TIGHT, INFEASIBLE, UNKNOWN}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self._payload = payload

    def get(self, *a, **k):
        return _Resp(self._payload)


def _search_result(slug):
    """Run the real journeys.search_journeys over a captured fixture (net stubbed)."""
    with open(os.path.join(FIX_DIR, f"{slug}.json"), encoding="utf-8") as f:
        fx = json.load(f)
    original = journeys._get_session
    journeys._get_session = lambda: _Session(fx["response"])
    try:
        return journeys.search_journeys(
            fx["meta"]["origin_query"], fx["meta"]["destination_query"],
            datetime(2026, 7, 13, 9, 0),
        )
    finally:
        journeys._get_session = original


# ---------------------------------------------------------------------------
# rollup_verdict
# ---------------------------------------------------------------------------

def test_rollup_verdict():
    assert rollup_verdict([]) == FEASIBLE               # direct journey
    assert rollup_verdict([FEASIBLE, FEASIBLE]) == FEASIBLE
    assert rollup_verdict([FEASIBLE, TIGHT]) == TIGHT
    assert rollup_verdict([TIGHT, UNKNOWN]) == UNKNOWN   # unknown worse than tight
    assert rollup_verdict([UNKNOWN, INFEASIBLE]) == INFEASIBLE  # infeasible worst


# ---------------------------------------------------------------------------
# enrich() with the assessor stubbed
# ---------------------------------------------------------------------------

def test_enrich_shape_and_verdict_wiring(monkeypatch):
    monkeypatch.setattr(P, "assess_transfer", lambda *a, **k: TransferAssessment(
        verdict=FEASIBLE, walk_time_s=90.0, walk_distance_m=110.0, layover_s=300.0,
        relation_id=42, station_name="Somewhere", arrival_platform="3", departure_platform="7",
    ))
    resp = enrich(conn=None, search_result=_search_result("de_at_munchen_wien"))
    assert resp.origin.name and resp.destination.name
    assert resp.journeys
    for j in resp.journeys:
        assert j.verdict in _ALLOWED_VERDICTS
        assert len(j.transfers) == max(0, len([l for l in j.legs if l.mode != "walking"]) - 1)
        assert j.num_changes == len(j.transfers)
        if j.transfers:
            assert j.verdict == FEASIBLE          # every stubbed transfer is feasible
            for t in j.transfers:
                assert t.walk_time_s == 90.0 and t.verdict == FEASIBLE
        else:
            assert j.verdict == FEASIBLE          # direct -> feasible


def test_enrich_platformless_network_degrades_gracefully_without_db():
    """FR/IT/ES fixtures carry no platforms; the real assessor must short-circuit
    to no_platform_data BEFORE any DB call, so enrich works with conn=None."""
    resp = enrich(conn=None, search_result=_search_result("es_barcelona_madrid"))
    assert resp.journeys
    saw_transfer = False
    for j in resp.journeys:
        for t in j.transfers:
            saw_transfer = True
            assert t.verdict == UNKNOWN
            assert t.reason == NO_PLATFORM_DATA
    assert saw_transfer, "expected at least one (platformless) interchange in this corpus"


def test_plan_journeys_wires_search_and_enrich(monkeypatch):
    monkeypatch.setattr(P, "search", lambda *a, **k: _search_result("de_frankfurt_koln"))
    monkeypatch.setattr(P, "assess_transfer", lambda *a, **k: TransferAssessment(
        verdict=TIGHT, walk_time_s=200.0, layover_s=240.0, relation_id=1, station_name="X",
        arrival_platform="a", departure_platform="b",
    ))
    resp = plan_journeys(None, "Frankfurt", "Köln", datetime(2026, 7, 13, 9, 0))
    assert resp.journeys
    assert any(j.transfers for j in resp.journeys)  # the 1-transfer itinerary


# ---------------------------------------------------------------------------
# DB-gated: real enrichment over a DACH fixture
# ---------------------------------------------------------------------------

@DB
def test_enrich_real_dach_fixture_end_to_end():
    import db

    conn = db.connect(connect_timeout=5)
    resp = enrich(conn, _search_result("de_munchen_hamburg"))
    assert resp.journeys
    for j in resp.journeys:
        assert j.verdict in _ALLOWED_VERDICTS
        for t in j.transfers:
            assert t.verdict in _ALLOWED_VERDICTS
    # At least one interchange in this DACH journey must have resolved to a
    # station and been handed to core/ -- proving the bridge fires in-pipeline.
    assert any(
        t.relation_id is not None
        for j in resp.journeys for t in j.transfers
    ), "expected at least one interchange to resolve to an OSM station"
