"""
#36: the client's "Boarding buffer" setting must reach the server's verdict
check. The buffer was already threaded through the pipeline (enrich /
assess_interchanges / classify -- see tests/test_pipeline.py and
tests/test_transfers.py); these tests lock the LAST mile: the `/journeys` and
`/assess` HTTP handlers forwarding the per-request buffer to the pipeline
instead of always using the server default.

The handlers are called directly (no TestClient/auth/DB) with the pipeline
functions stubbed to capture the buffer they receive -- a fast, focused unit
test of the wiring this issue added.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import api.main as M  # noqa: E402
from api import config, schemas  # noqa: E402


def _empty_response():
    return schemas.JourneysResponse(
        origin=schemas.Place(), destination=schemas.Place(),
        departure_time=None, journeys=[],
    )


def test_journeys_endpoint_forwards_buffer_to_pipeline(monkeypatch):
    """A `buffer_s` query value reaches `plan_journeys` (and thus every transfer's
    verdict), not the hard-coded server default."""
    captured = {}

    def fake_plan(conn, from_, to, when, **kw):
        captured.update(kw)
        return _empty_response()

    monkeypatch.setattr(M, "plan_journeys", fake_plan)
    M.get_journeys(from_="A", to="B", time=None, max=5,
                   assess=True, no_elevators=False, buffer_s=90, conn=None)
    assert captured["buffer_s"] == 90


def test_assess_endpoint_forwards_buffer_to_pipeline(monkeypatch):
    """A request carrying `buffer_s` streams verdicts judged against THAT buffer,
    so the streamed values match the `/journeys` search they backfill."""
    captured = {}

    def fake_assess(conn, interchanges, **kw):
        captured.update(kw)
        return schemas.AssessResponse(transfers=[])

    monkeypatch.setattr(M, "assess_interchanges", fake_assess)
    M.post_assess(schemas.AssessRequest(interchanges=[], buffer_s=30), conn=None)
    assert captured["buffer_s"] == 30


def test_assess_endpoint_defaults_to_server_buffer_when_omitted(monkeypatch):
    """A request that omits `buffer_s` (an older client, or a search made at the
    default) falls back to the server's own buffer -- unchanged from before the
    field existed."""
    captured = {}

    def fake_assess(conn, interchanges, **kw):
        captured.update(kw)
        return schemas.AssessResponse(transfers=[])

    monkeypatch.setattr(M, "assess_interchanges", fake_assess)
    M.post_assess(schemas.AssessRequest(interchanges=[]), conn=None)
    assert captured["buffer_s"] == config.BUFFER_S
