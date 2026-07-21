"""
Live re-assessment poller: keep a journey's transfers scored against realtime.

The heavy work (the platform-to-platform walk) is done once at plan time and
cached in api.transfers.LiveTransfer. This module just feeds fresh delays into
api.transfers.reassess on a loop.

Layering (kept deliberately thin, so MOTIS stays the router -- see the design
notes in transfers.py):
  * `updates_from_motis_itinerary` -- pure: turn a (refreshed) MOTIS itinerary
    into one DelayUpdate per transfer. Unit-tested without network.
  * `reassess_journey` -- pure: apply the updates to the cached transfers.
  * `refresh_itinerary` -- the one network call (MOTIS /refresh-itinerary,
    experimental) that produces a fresh itinerary to parse.
  * `LiveMonitor` -- the loop: fetch -> reassess -> callback, on an interval,
    checkpointing to disk and handling Ctrl-C without losing the last verdicts.

For DE stations, DB IRIS (dbf.finalrewind.org) surfaces platform changes sooner
than the DELFI feed MOTIS ingests; plug an IRIS-backed `fetch_updates` in when
you need faster re-tracking. MOTIS refresh is the multi-country default.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import requests

from api.transfers import LiveTransfer, LiveVerdict, reassess
from api.config import MOTIS_BASE

_WALK_MODES = {"WALK", "BIKE", "CAR", "BIKE_SHARING", "CAR_SHARING", "SCOOTER_SHARING"}


def _iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _delay_s(actual: Optional[str], scheduled: Optional[str]) -> float:
    a, s = _iso(actual), _iso(scheduled)
    if a is None or s is None:
        return 0.0
    return (a - s).total_seconds()


@dataclass
class DelayUpdate:
    """Live state of one transfer, aligned by position to the LiveTransfer list."""
    inbound_delay_s: float = 0.0
    outbound_delay_s: float = 0.0
    arr_track_now: Optional[str] = None
    dep_track_now: Optional[str] = None
    cancelled: bool = False


def _transit_legs(itin: Dict[str, Any]) -> List[dict]:
    return [l for l in itin.get("legs", []) if l.get("mode") not in _WALK_MODES]


def updates_from_motis_itinerary(itin: Dict[str, Any]) -> List[DelayUpdate]:
    """One DelayUpdate per change of train, in interchange order -- the same order
    api.transitous.interchanges() / the plan-time LiveTransfer list use.

    Inbound delay is the arriving train's arrival lateness; outbound delay is the
    departing train's departure lateness; the 'now' tracks are realtime tracks.
    """
    legs = _transit_legs(itin)
    updates: List[DelayUpdate] = []
    for a, b in zip(legs, legs[1:]):
        a_to, b_from = a.get("to") or {}, b.get("from") or {}
        updates.append(DelayUpdate(
            inbound_delay_s=_delay_s(a.get("endTime"), a.get("scheduledEndTime")),
            outbound_delay_s=_delay_s(b.get("startTime"), b.get("scheduledStartTime")),
            arr_track_now=a_to.get("track"),
            dep_track_now=b_from.get("track"),
            cancelled=bool(a.get("cancelled") or b.get("cancelled")),
        ))
    return updates


def reassess_journey(
    transfers: List[LiveTransfer],
    updates: List[DelayUpdate],
    *,
    conn=None,
) -> List[LiveVerdict]:
    """Re-score every transfer against its live update. `updates` is aligned to
    `transfers` by position; a shorter list leaves the rest scored on schedule."""
    out: List[LiveVerdict] = []
    for i, t in enumerate(transfers):
        u = updates[i] if i < len(updates) else DelayUpdate()
        out.append(reassess(
            t,
            inbound_delay_s=u.inbound_delay_s,
            outbound_delay_s=u.outbound_delay_s,
            arr_track_now=u.arr_track_now,
            dep_track_now=u.dep_track_now,
            conn=conn,
        ))
    return out


def refresh_itinerary(itinerary_id: str, *,
                      base_url: str = MOTIS_BASE,
                      session: Optional[requests.Session] = None,
                      timeout: float = 15.0) -> Dict[str, Any]:
    """Fetch a realtime-refreshed itinerary from MOTIS. Experimental endpoint
    (per MOTIS's own docs); the itineraryId comes from the original /plan result."""
    sess = session or requests.Session()
    r = sess.get(f"{base_url}/api/v6/refresh-itinerary",
                 params={"itineraryId": itinerary_id}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # refresh returns either a bare itinerary or a {itinerary: ...}/{itineraries:[...]} wrapper
    if "legs" in data:
        return data
    if data.get("itinerary"):
        return data["itinerary"]
    its = data.get("itineraries") or []
    return its[0] if its else {}


class LiveMonitor:
    """Poll for realtime and re-score a journey's transfers on an interval.

    `fetch_updates` is injected (so it's testable and source-agnostic): it returns
    a list of DelayUpdate aligned to `transfers`. A MOTIS-backed one is:

        mon = LiveMonitor(transfers,
                          lambda: updates_from_motis_itinerary(refresh_itinerary(itin_id)))

    The loop checkpoints the latest verdicts to `state_path` after every tick and
    on Ctrl-C, so a long-running monitor never loses its last known state.
    """

    def __init__(
        self,
        transfers: List[LiveTransfer],
        fetch_updates: Callable[[], List[DelayUpdate]],
        *,
        interval_s: float = 30.0,
        conn=None,
        state_path: Optional[str] = None,
        on_update: Optional[Callable[[List[LiveVerdict]], None]] = None,
    ):
        self.transfers = transfers
        self.fetch_updates = fetch_updates
        self.interval_s = interval_s
        self.conn = conn
        self.state_path = state_path
        self.on_update = on_update
        self.last: List[LiveVerdict] = []

    def tick(self) -> List[LiveVerdict]:
        """One poll -> reassess -> callback -> checkpoint. Never raises on a
        transient fetch failure; keeps the previous verdicts instead."""
        try:
            updates = self.fetch_updates()
        except Exception as e:  # noqa: BLE001 -- a flaky poll shouldn't kill the monitor
            print(f"[live] fetch failed, keeping last verdicts: {type(e).__name__}: {e}", flush=True)
            return self.last
        self.last = reassess_journey(self.transfers, updates, conn=self.conn)
        if self.on_update:
            self.on_update(self.last)
        self._checkpoint()
        return self.last

    def _checkpoint(self) -> None:
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": datetime.now().isoformat(),
                       "verdicts": [asdict(v) for v in self.last]}, f, indent=1)
        os.replace(tmp, self.state_path)

    def run(self, *, max_ticks: Optional[int] = None) -> None:
        """Loop until interrupted (or `max_ticks`). Ctrl-C checkpoints and exits
        cleanly rather than tearing down mid-write."""
        n = 0
        try:
            while max_ticks is None or n < max_ticks:
                self.tick()
                n += 1
                if max_ticks is not None and n >= max_ticks:
                    break
                time.sleep(self.interval_s)
        except KeyboardInterrupt:
            print("\n[live] interrupted; last verdicts checkpointed.", flush=True)
            self._checkpoint()
