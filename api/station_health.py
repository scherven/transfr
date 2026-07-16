"""
Classify a single station's platform connectivity for the Map-health tool.

`/journeys` judges one transfer at a time; this scores a WHOLE station. For every
unordered pair of the station's platform refs, run the platform-to-platform
pathfinder up to twice -- once plain (no stitch bridges) and, only if that fails,
once with synthetic stitch bridges enabled -- and bucket the pair:

  * connected  -- a route is found with plain routing
  * stitchable -- found ONLY once stitch bridges are enabled
  * island     -- not found either way

This is the same connected / stitchable / island split behind the EU/KR survey
numbers in the app's Map-health screen (which sweeps every station), scoped here
to the one station nearest a coordinate.

Combinatorics guard: N platforms -> N*(N-1)/2 pairs, each up to two pathfinds. A
normal mainline station (Berlin Hbf: 14 platforms, 91 pairs) is fine; above
MAX_PLATFORMS the ref list is down-sampled by an even stride to that many
platforms (`sampled=True`) so a pathologically large station cannot blow the
request budget. `platform_count` still reports the true total.
"""

from __future__ import annotations

from itertools import combinations
from typing import List, Tuple

from ground_truth import find_shortest_path
from search_context import list_platform_refs

from api import schemas
from api.bridge import resolve_station
from api.transfers import STATION_UNRESOLVED

# Upper bound on platforms actually pair-tested. 24 -> 276 pairs is the ceiling
# the tool will pathfind per request; a larger station is sampled down to this
# many refs (even stride) so the combinatorics stay bounded.
MAX_PLATFORMS = 24

# How many non-connected (stitchable/island) pairs to surface as worked examples.
MAX_EXAMPLES = 6

CONNECTED = "connected"
STITCHABLE = "stitchable"
ISLAND = "island"


def _sample(refs: List[str], cap: int) -> Tuple[List[str], bool]:
    """At most `cap` refs. Above the cap, take an even stride across the (already
    naturally sorted) list so the sample spans the whole platform range rather than
    just the low numbers -- deterministically, so the result is reproducible and
    testable. Returns (refs, sampled)."""
    if len(refs) <= cap:
        return refs, False
    step = len(refs) / cap
    # int(i*step) is strictly increasing for step > 1, so no duplicates; order kept.
    return [refs[int(i * step)] for i in range(cap)], True


def _classify_pair(conn, relation_id: int, a: str, b: str) -> str:
    """Bucket one unordered platform pair. Plain pass first; the (more expensive)
    stitch pass runs only when the plain one fails, so a connected pair costs a
    single pathfind."""
    plain = find_shortest_path(conn, relation_id, a, b, algorithm="astar",
                               use_stitch_bridges=False)
    if plain.get("found"):
        return CONNECTED
    stitched = find_shortest_path(conn, relation_id, a, b, algorithm="astar",
                                  use_stitch_bridges=True)
    return STITCHABLE if stitched.get("found") else ISLAND


def build_station_health(conn, lat: float, lon: float) -> schemas.StationHealthResponse:
    """Resolve the station nearest (lat, lon), classify every platform pair, and
    roll the buckets up into a `StationHealthResponse`. `found=False` (with a
    reason) when no station sits near the coordinate; a station with fewer than two
    platforms resolves with zero pairs (nothing to connect)."""
    with conn.cursor() as cur:
        match = resolve_station(cur, lat, lon)
        if match is None:
            return schemas.StationHealthResponse(
                lat=lat, lon=lon, found=False, reason=STATION_UNRESOLVED)
        refs = list_platform_refs(cur, match.relation_id)

    sampled_refs, sampled = _sample(refs, MAX_PLATFORMS)

    connected = stitchable = island = 0
    disconnected: List[Tuple[str, str, str]] = []
    for a, b in combinations(sampled_refs, 2):
        kind = _classify_pair(conn, match.relation_id, a, b)
        if kind == CONNECTED:
            connected += 1
        else:
            if kind == STITCHABLE:
                stitchable += 1
            else:
                island += 1
            disconnected.append((a, b, kind))

    total = connected + stitchable + island

    def pct(n: int) -> float:
        return round(100.0 * n / total, 1) if total else 0.0

    # Prefer stitchable pairs in the examples -- the recoverable ones are the more
    # actionable story than yet another island -- then fill with islands.
    disconnected.sort(key=lambda p: 0 if p[2] == STITCHABLE else 1)
    examples = [
        schemas.StationHealthPair(from_platform=a, to_platform=b, kind=kind)
        for a, b, kind in disconnected[:MAX_EXAMPLES]
    ]

    return schemas.StationHealthResponse(
        lat=lat, lon=lon, relation_id=match.relation_id, station=match.name,
        found=True, platform_count=len(refs), sampled=sampled,
        connected=connected, stitchable=stitchable, island=island,
        connected_pct=pct(connected), stitchable_pct=pct(stitchable),
        island_pct=pct(island), examples=examples,
    )
