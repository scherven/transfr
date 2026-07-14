"""
Tests for the live re-assessment poller (api/live.py). All offline: the MOTIS
itinerary parsing and the reassess loop are pure, and the monitor is driven with
an injected fetch so no network is touched.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from api.transfers import LiveTransfer, FEASIBLE, INFEASIBLE, TIGHT  # noqa: E402
from api.live import (  # noqa: E402
    DelayUpdate, updates_from_motis_itinerary, reassess_journey, LiveMonitor,
)


def _live(**over):
    kw = dict(relation_id=1, arr_ref="8", dep_ref="5", walk_time_s=71.0,
              scheduled_layover_s=420.0, motis_assumed_s=240.0, buffer_s=60.0)
    kw.update(over)
    return LiveTransfer(**kw)


def _itin():
    """A Hamburg->Stuttgart-shaped itinerary: access walk, train A into a hub
    (arriving 3 min late on track 8), the transfer, train B (on time, track 5),
    egress walk."""
    return {"legs": [
        {"mode": "WALK", "from": {"name": "START"}, "to": {"name": "Hub", "track": "8"}},
        {"mode": "REGIONAL_RAIL",
         "from": {"name": "A", "track": "1"},
         "to": {"name": "Hub", "track": "8"},
         "scheduledEndTime": "2026-07-14T07:44:00Z", "endTime": "2026-07-14T07:47:00Z"},
        {"mode": "WALK", "from": {"name": "Hub", "track": "8"}, "to": {"name": "Hub", "track": "5"}},
        {"mode": "SUBURBAN",
         "from": {"name": "Hub", "track": "5"},
         "to": {"name": "B", "track": "3"},
         "scheduledStartTime": "2026-07-14T07:49:00Z", "startTime": "2026-07-14T07:49:00Z"},
        {"mode": "WALK", "from": {"name": "Hub", "track": "5"}, "to": {"name": "END"}},
    ]}


def test_updates_from_motis_itinerary():
    ups = updates_from_motis_itinerary(_itin())
    assert len(ups) == 1
    u = ups[0]
    assert u.inbound_delay_s == 180.0     # train A arrived 3 min late
    assert u.outbound_delay_s == 0.0
    assert u.arr_track_now == "8" and u.dep_track_now == "5"
    assert u.cancelled is False


def test_updates_empty_for_direct_trip():
    itin = {"legs": [{"mode": "WALK"}, {"mode": "REGIONAL_RAIL"}, {"mode": "WALK"}]}
    assert updates_from_motis_itinerary(itin) == []


def test_reassess_journey_applies_updates():
    transfers = [_live()]
    verdicts = reassess_journey(transfers, updates_from_motis_itinerary(_itin()))
    assert len(verdicts) == 1
    # 420s scheduled - 180s inbound delay = 240s effective; 71s walk -> still feasible,
    # and MOTIS's 240s minimum is right at the edge (not below), so no rescue here.
    assert verdicts[0].verdict == FEASIBLE
    assert verdicts[0].effective_layover_s == 240.0


def test_reassess_journey_rescue_when_delay_bites():
    transfers = [_live()]
    updates = [DelayUpdate(inbound_delay_s=300.0)]  # 5 min late -> eff 120s
    v = reassess_journey(transfers, updates)[0]
    assert v.verdict in (FEASIBLE, TIGHT)
    assert v.rescued is True   # MOTIS(240s) drops it; real 71s walk still makes it


def test_reassess_journey_shorter_updates_default_on_time():
    transfers = [_live(), _live(scheduled_layover_s=90.0)]
    verdicts = reassess_journey(transfers, [DelayUpdate(inbound_delay_s=360.0)])
    assert verdicts[0].verdict == INFEASIBLE      # first got the delay
    assert verdicts[1].verdict in (FEASIBLE, TIGHT)  # second scored on schedule


def test_live_monitor_tick_checkpoints(tmp_path):
    state = str(tmp_path / "live.json")
    seen = []
    mon = LiveMonitor(
        [_live()],
        fetch_updates=lambda: [DelayUpdate(inbound_delay_s=300.0)],
        state_path=state,
        on_update=lambda vs: seen.append(vs),
    )
    verdicts = mon.tick()
    assert verdicts[0].rescued is True
    assert seen and seen[0] is verdicts
    saved = json.load(open(state))
    assert saved["verdicts"][0]["rescued"] is True


def test_live_monitor_survives_fetch_failure():
    def boom():
        raise RuntimeError("network down")

    mon = LiveMonitor([_live()], fetch_updates=boom)
    mon.last = ["previous"]           # pretend we had a prior good tick
    assert mon.tick() == ["previous"]  # keeps last verdicts, does not raise


def test_live_monitor_run_max_ticks():
    ticks = []
    mon = LiveMonitor([_live()], fetch_updates=lambda: ticks.append(1) or [],
                      interval_s=0)
    mon.run(max_ticks=3)
    assert len(ticks) == 3
