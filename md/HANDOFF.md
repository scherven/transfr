# Handoff notes: transfr platform-pathfinding rewrite

Written mid-session for continuity — this is a working log, not user-facing
documentation. If you're picking this up fresh, read this whole file before
touching code. Everything below is verified against the live system unless
explicitly marked "designed, not yet implemented."

## Goal

Replace the old `pathfind.py`/`test.py`/`server.py` system (which queried a
53GB `openrailwaymap` Postgres DB built from a planet-wide, poorly-scoped
OSM extract) with a solid Python base for platform-to-platform walking
pathfinding at European train stations. Explicitly out of scope for this
phase: the JavaScript/React frontend (untouched, still points at the old
backend) and `server.py`/Flask wiring (the new code has no HTTP layer yet).

All new code lives in `core/`. Nothing in `core/` imports from the legacy
root-level `pathfind.py`, `test.py`, `server.py`, `stations.py`, `journeys.py`
— those still exist and still work against the old `openrailwaymap` DB, but
are not part of this rewrite. `tests/test_transfers.py` (the old test suite)
was deleted per user request; `tests/test_dijkstra.py`, `test_graph.py`,
`test_ground_truth.py`, `test_bidirectional_search.py` are the new suite.

## Data pipeline

**Source**: `server-admin/planet.pbf` (90GB, full planet, already present
locally — never re-downloaded).

**Extraction** (`core/dbgen/extract_europe.sh`): two `osmium` passes, cheapest
reduction first —
1. `osmium tags-filter` on the full planet.pbf with a curated tag list (see
   below), producing a planet-wide-but-tag-scoped intermediate.
2. `osmium extract --polygon core/data/europe.poly` (Geofabrik's actual
   Europe boundary, fetched fresh, not a hand-rolled bbox) to clip to Europe.

Final artifact: `core/data/europe-railway-pedestrian.pbf`, 1.3GB.

**Tag scope — this took three iterations to get right, and the wrong
answer is not obvious in advance. Numbers below are measured (via
`osmium tags-filter -R` + `osmium fileinfo`), not guessed:**

Included (`nwr` = matches as node, way, or relation):
- `railway=platform,platform_edge,station,halt,subway_entrance,buffer_stop,level_crossing`
- `public_transport=stop_area,stop_area_group,station`
- `highway=footway,steps,corridor,pedestrian,elevator`
- `conveying` (any value — escalators/moving walkways)

Deliberately excluded, with the measured reason:
- **`highway=service`** — 62.1M ways worldwide, *larger than footway itself*.
  Not pedestrian-specific (driveways, parking aisles, industrial access).
- **`highway=path`** — 15.7M ways worldwide. Real tag, wrong context (hiking
  trails, rural tracks, not station infrastructure).
- **bare `entrance=*`** on nodes — 4.9M nodes worldwide. Only useful via
  relation membership, which is captured anyway; a standalone building
  entrance node with no path to a station is noise.
- **`public_transport=platform`/`stop_position`** — 3.5M + 1.5M elements,
  but this tagging is mode-agnostic and dominated by bus stops.
  `railway=platform`/`platform_edge` already covers the rail-specific case.
- **bare `nwr/railway`** (any value) — far too broad, matches signals,
  switches, plain track, milestones, everything.
- **`route=train/tram/subway/light_rail` relations** — pulls in
  whole-country track geometry via member expansion; never queried by the
  pathfinding logic, which only cares about `stop_area` membership.
- **`disused:railway`/`abandoned:railway`/`razed:railway`/
  `construction:railway`/`proposed:railway`** — historical/future track
  state, irrelevant to "can I walk this transfer today."

**Result**: 73,109,532 nodes / 16,117,893 ways / 371,739 relations, loaded
into a **new** Postgres database `transfr_eu` (NOT the old `openrailwaymap`
DB — that's untouched and still backs the legacy system). Total size: 15GB,
versus the legacy system's 53GB for planet-wide, un-scoped data plus ~16GB
of never-queried map-rendering tables.

## Schema (`core/dbgen/schema.sql`)

Four tables, no rendering-oriented cruft:
- `osm_nodes(id PK, lat, lon, tags jsonb)`
- `osm_ways(id PK, nodes bigint[], tags jsonb)` + GIN on `nodes`, GIN on
  `tags`, partial btree on `(tags->>'ref')`/`(tags->>'railway:track_ref')`
  where `railway='platform_edge'` (the platform-lookup hot path)
- `osm_relations(id PK, tags jsonb)` + GIN on tags, btree on
  `(tags->>'name')` and `(tags->>'public_transport')`
- `osm_relation_members(relation_id, sequence, member_type, member_ref,
  member_role)` — flattened at load time, no `jsonb_array_elements()` at
  query time like the old `station_platform_ways` view needed
- `node_way_ids(node_id PK, way_ids bigint[])` — added later, see
  "Efficiency improvements" below. NOT in the original schema design; if
  you rebuild from scratch, `core/dbgen/build_node_way_ids.py` must be run after
  `core/dbgen/etl.py`.

Loading: `core/dbgen/etl.py`, pyosmium-based, batched commits (nodes every 50k,
ways every 20k, relations every 5k), upsert semantics (`ON CONFLICT DO
UPDATE`), graceful `KeyboardInterrupt` handling that flushes buffered rows
before exit — re-running the script from scratch is always safe (per user's
global CLAUDE.md instruction about long-running processes never losing
progress). Full load took ~55 minutes. `core/dbgen/build_node_way_ids.py` (the
adjacency table) takes ~5 minutes on top of that and must be re-run after
any `osm_ways` reload — it's a clean `TRUNCATE`+`INSERT`, no incremental
staleness state to reason about.

**Environment note**: Postgres `shared_buffers` is stuck at the default
128MB against a workload with a ~7GB `osm_ways` table. Confirmed via
`EXPLAIN ANALYZE` that this causes real cold-cache disk I/O, not just query
planning overhead. Cannot be changed — it's a system-level `postgresql.conf`
edit outside the repo, and the permission system blocks it (confirmed: a
direct edit attempt and a follow-up read-only warm-up query were both
denied). This is why `node_way_ids` (see below) matters as much as it does
— it's a workaround for a config knob we can't turn. If the user ever
raises `shared_buffers` themselves, re-benchmark; the relative advantage of
`node_way_ids` may shrink (though it should still win, being a strictly
cheaper query shape).

## A real bug found in the data, not the code

`public_transport=station` ways represent the outline of an **entire
station building**, not a walkway. Confirmed by hand (user manually checked
OSM links): Strasbourg's "Gare de Strasbourg" (way 1154073483, 128 nodes)
and Colmar's "Colmar" (way 1314402541, 90 nodes) are both direct
`stop_area` relation members tagged this way. Before this was excluded, the
pathfinder was "shortcutting" through the building's perimeter as if it
were a single corridor. Fixed via `graph.py`'s `is_walkable_way()` /
`NOT_WALKABLE_WAY_SQL`, applied everywhere a way gets added to a walkable
graph — both `graph.py`'s eager `load_station_ways` and
`search_context.py`'s `SearchContext` (seed loading AND the `expand()`
on-demand query). If you add a new way-discovery code path, it needs this
filter too, or the same bug comes back.

There's a second, separate, *genuine* data gap, not a bug: Colmar's
platform E ("Quai D-E", way 53506915, a closed-polygon platform area) is
mapped with no connecting footway/corridor to the rest of the station in
the source OSM data — verified by hand via direct SQL (no other way shares
any of its nodes except the two platform_edge ways for D and E themselves).
`find_shortest_path(conn, 6365739, "A", "E")` correctly returns
`{"found": False, "reason": "disconnected"}`. This is intentional,
tested behavior (`test_colmar_platform_e_correctly_disconnected`), not
something to "fix" by loosening the search.

## Algorithm evolution (chronological, each stage's lesson matters)

**V1 — eager, abandoned.** `graph.py`'s `load_station_ways()`: load every
way within a 1000m bounding box of the station's own relation-member
geometry, *then* run Dijkstra on the static result. Correct, but
impractical: Berlin Hauptbahnhof's 1000m closure is ~3,500 ways / ~9,800
nodes and took **17.6 minutes**. Root cause: dense European city centers
are frequently *one single* footway-connected component with no natural
graph boundary — a fixed-radius box still captures a huge, mostly-irrelevant
chunk of the surrounding city. Kept in the codebase (`find_shortest_path_eager`
in `ground_truth.py`) purely as a slow, independently-implemented
cross-check for tests, not as something new code should call.

**V2 — lazy/incremental, the actual fix.** Dijkstra is inherently
incremental: it only needs a node's neighbors when it's actually popped off
the priority queue, and can stop the instant the target is reached.
`search_context.py`'s `SearchContext` fetches a node's touching ways from
Postgres *exactly when the search reaches that node*, not upfront. Same
algorithm, same optimality guarantee — only the data access pattern
changed. Berlin: 17.6 min → ~1.5s.

Bug caught and fixed during this stage: conflating "this node appeared in
some other way's node list" (because a fetched way happened to include it)
with "we've actually run the *find every way touching this specific node*
query for it." A node can pick up a `node_to_ways` entry just by being part
of a neighboring way's geometry, without ever having been used as a query
seed itself — treating "has an entry" as "fully expanded" silently missed
real connections and produced false "disconnected" results. Fixed with a
separate `queried_nodes` set, checked before the expensive query, decoupled
from `node_to_ways`'s incidental population. See `SearchContext.expand()`'s
docstring for the long version.

**V3 — algorithm iteration (current phase).** Two sub-agents were asked,
independently and in parallel, to propose faster methods: one for graph
traversal algorithms, one for systems/DB-level speedups. Both proposals,
and what was built from them:

1. **A\* search** (`core/pathfinding/algo_astar.py`) — heuristic
   `h(u) = min over targets of haversine(u, target) / WALKING_SPEED_MS`.
   Admissible (no edge is ever faster than `WALKING_SPEED_MS`) and
   consistent (derived from a real metric), so provably optimal, not an
   approximation. 1.5x–5.7x faster than Dijkstra on real stations, because
   every node it *doesn't* pop is a Postgres round trip never made.
   Correctly shows near-zero improvement on the genuinely-disconnected
   Colmar A→E case (no heuristic helps when nothing connects — both
   algorithms must exhaust the whole reachable component).

2. **`node_way_ids` materialized adjacency table**
   (`core/dbgen/build_node_way_ids.py`, wired into `SearchContext.expand()` via
   `use_adjacency_table`) — replaces the GIN bitmap-heap-scan
   (`nodes && ARRAY[u]`) with two point lookups: PK lookup on
   `node_way_ids` for candidate way ids, then PK-array lookup on
   `osm_ways`. Attacks cost-per-round-trip rather than round-trip count
   (see the `shared_buffers` note above for why this matters here
   specifically). 1.3x–11.7x faster, exact same results. **Now the
   default** (`use_adjacency_table=True` in both `SearchContext.__init__`
   and `ground_truth.find_shortest_path`).

Both are wired through a pluggable registry (`core/pathfinding/algorithms.py`,
`ALGORITHMS` dict) built on a shared `SearchContext` (relation resolution +
platform-edge lookup + lazy neighbor access — the same for every
algorithm). Adding a new algorithm: write `core/algo_<name>.py` with a
`search(ctx, **kwargs) -> dict` function using `ctx.neighbors(u)`,
`ctx.sources`/`ctx.targets`, and `ctx.build_result()`/`ctx.build_not_found()`
for the return shape, then register it in `algorithms.py`. It is
*automatically* checked against the `dijkstra` baseline by
`tests/test_ground_truth.py::test_algorithm_agrees_with_baseline` and shows
up in `core/tooling/benchmark.py`'s comparison table — no other test-file changes
needed.

**Combined result so far**: Berlin Hbf 1→16, the slowest real test case:
17.6 minutes (V1) → ~0.6s (V2, Dijkstra+GIN) → 0.102s (V2+adjacency table)
→ 0.067s (A*+adjacency table).

## Test stations (confirmed against live `transfr_eu`, not guesses)

| Station | relation_id | Refs | Notes |
|---|---|---|---|
| Strasbourg-Ville (FR) | 5347313 | numeric 1-9,25,30-32 | mid-size, ~432 ways closure |
| Berlin Hauptbahnhof (DE) | 5688517 | numeric 1-8,11-16 | large/dense hub, ~3,530 ways closure |
| Colmar (FR) | 6365739 | **letter** A-E | small, ~63 ways closure; E is a genuine disconnected gap (see above) |
| Basel SBB, one wing (CH) | 4272361 | **letter** F-H | Basel SBB is split across several `stop_area` relations in OSM; this is one wing, not the whole complex |

Picked deliberately for size and platform-ref-naming diversity (numeric vs.
letter — the old system's `int(from_platform)` parsing in `server.py` would
have broken on Colmar/Basel; the new code's `str(ref)` comparison in
`find_platform_edges`/`_find_platform_edges_near` handles both).

## Current in-progress work: bidirectional Dijkstra

User's explicit instruction: build a **pure, DB-independent**
implementation first, with rigorous synthetic-data tests, and don't wire it
into the DB-backed path (`core/algo_bidirectional.py`, not yet created)
until the pure version is fully correct. This is because bidirectional
search has a well-known, easy-to-get-subtly-wrong termination condition.

Files: `core/pathfinding/bidirectional_search.py` (implementation),
`tests/test_bidirectional_search.py` (tests — **currently 2 of 15 failing,
do not treat this module as trustworthy yet**).

### The core idea and the classic bug

Run Dijkstra simultaneously from sources (forward) and targets (backward,
over `reverse_graph(forward_graph)`), meeting in the middle. The naive
version stops "as soon as some node has been settled by both searches" and
returns `distF[node] + distB[node]`. **This is wrong**: the true shortest
path's meeting point is frequently an *edge* whose two endpoints are
settled on *different* sides, never a single node settled on both.
`tests/test_bidirectional_search.py::_make_meeting_edge_graph` constructs
exactly this case (true shortest path S→A→B→T = 3, meeting via edge A-B
where A is only ever forward-settled and B only ever backward-settled; a
"distractor" path S→C→T = 4 that a node-overlap-only rule can find first).

Correct rule (Goldberg & Harrelson): track `mu`, the best complete
source-to-target path length found so far; update it whenever a
cross-side match is found; stop once `topF + topB >= mu` (both queues'
next-smallest tentative distance already sum to at least the best found —
nothing remaining can improve on it).

### Bug 1 — FIXED: premature loop exit on exhaustion

Original loop: `while heapF and heapB:` — requires **both** queues
non-empty. As soon as either side's reachable set is fully exhausted (a
leaf node, or a small/simple graph), that condition goes false and the
loop exits immediately, even though the other side might still have
useful work pending (including the trivial "shared source/target node is
still sitting unpopped in the other queue" case).

Fix (applied): `while heapF or heapB:`, treating an empty heap's "top" as
contributing **0** (not infinity) to the `topF + topB` sum. Proof sketch:
once a heap is empty, that side's distances are final/complete for its
entire reachable set, and every future settle/relax on the *other* side
already checks its discoveries against that now-static settled set — so
continuing to drain the non-empty side is both safe and necessary, and the
exhausted side isn't waiting on anything, hence contributes 0, not ∞ (∞
would wrongly force immediate termination).

This fix resolved `test_directed_edge_wrong_reverse_graph_would_be_caught_by_ground_truth_diff`
and `test_matches_forward_dijkstra_on_random_directed_graphs_with_oneways`.

### Bug 2 — ROOT CAUSE IDENTIFIED, FIX DESIGNED, NOT YET IMPLEMENTED

Still failing: `test_matches_forward_dijkstra_on_random_graphs` (6
mismatches out of 200 random graphs) and `test_naive_same_node_termination_would_be_wrong`
(the counterexample graph turned out to be order-sensitive — see below,
lower priority than the random-graph failures).

**Reproduced and understood precisely** (see the trace in conversation
history if you need the full derivation; summary follows). Concrete
failing case: `sources={n1}`, `targets={n1,n3,n0}`, where `n1` is both a
source and a target (trivial answer: distance 0). Actual graph from the
failure: `fwd = {'n0': [('n1', 9.84, ...)], 'n1': [('n0', 16.63, ...)], ...}`.

What happens: `n1` gets forward-settled almost immediately (it's a
source, distance 0), then forward relaxation discovers `n0` at distance
16.63 via the `n1->n0` edge. Meanwhile `n1`'s *own* entry is still sitting
unpopped in the backward queue (`heapB`) at distance 0 — because `n0` (also
a target, distance 0 from init) happens to get popped from `heapB` first.
When `n0` is backward-settled and its backward edges are relaxed, one of
them relaxes into `n1` — and since `n1` is already forward-settled, this
correctly-implemented "edge crosses into an already-settled opposite node"
check fires and sets `mu = 16.63` (a *real* but suboptimal path: n1 --16.63--> n0,
both of which happen to be valid endpoints). The termination check
`topF + topB >= mu` (16.63 + 0 >= 16.63) then fires **before `n1`'s own
pending entry in `heapB` is ever popped** — which would have triggered the
`distF[n1] + distB[n1] = 0 + 0 = 0` check and found the true answer.

**Root cause, precisely**: the `topF + topB >= mu` termination bound is
only a valid lower bound on *future improvement* when `topF` and `topB`
describe nodes that are genuinely *not yet known* on their respective
sides. It breaks when a specific node is already fully known (settled) on
one side but is still sitting, unprocessed, in the other side's queue at
a cheap tentative distance — that pending entry represents a "free" (or
very cheap) potential improvement that the generic bound doesn't see,
because `topF` in this scenario describes a *different* node (`n0`, cost
16.63) than the one that actually matters (`n1`, cost 0 combined with its
already-known distF).

**Fix, designed but not yet written into the module** (I was mid-rewrite
when interrupted for this handoff — do this next): stop gating the
cross-side check on `settled` sets entirely. Instead, check cross-side
existence against the **full tentative distance dicts** (`distF`/`distB`),
not just `settledF`/`settledB`. This is provably safe: a tentative
(not-yet-finalized) Dijkstra distance is always an upper bound on the true
shortest distance (standard Dijkstra invariant — tentative distances only
ever decrease before settling), so using one in a `mu` candidate can only
ever produce a valid-but-possibly-not-yet-tightest upper bound, never an
incorrect too-low value. Concretely:

1. At **initialization**, for every source `s`: if `s in distB` already
   (i.e., `s` is also a target), immediately register
   `mu = min(mu, distF[s] + distB[s])`. Symmetric for every target `t`
   already in `distF`. This directly and immediately handles the
   source/target-overlap case without depending on queue-processing order
   at all.
2. On **every relaxation** (not just when a node's own distance improves,
   and not just at settle-time): when relaxing edge `(u, v)` with weight
   `w` on side X, if `v` has *any* known distance on the opposite side
   (`v in other_dist`, tentative or final), register
   `mu = min(mu, dist_X[u] + w + other_dist[v])` — **regardless of
   whether this relaxation actually improves `dist_X[v]`**. This is the
   part the current code gets wrong: it only fires this check tied to
   settle-time processing of specific nodes, which can be skipped over by
   queue-ordering races exactly like the one reproduced above. Doing the
   check on every relaxation attempt (edge-by-edge, not node-by-node)
   removes the ordering dependency, because it no longer matters *which*
   side's queue happens to pop a shared node first — the check fires the
   moment *either* side's relaxation touches a node the other side already
   has *any* opinion about.

   Worked through by hand and confirmed algebraically consistent: for a
   backward relaxation relaxing `backward_graph[u] = [(v, w, label)]`
   (which by `reverse_graph`'s transpose definition means the *original*
   forward graph has edge `v -> u` with weight `w`), the candidate
   `distB[u] + w + distF[v]` equals (by commutativity)
   `distF[v] + w + distB[u]`, which is exactly the cost of the real path
   `source -> ... -> v -> [edge v->u] -> u -> ... -> target`. Confirmed
   this also subsumes the old node-settle check as a special case (when
   the edge relaxation is the one that originally establishes a node's
   distance, `nd = dist[u] + w` becomes that node's own `dist[v]`, so the
   edge check and the "same node, both sides now known" check compute the
   identical value).

   Given the volume of cross-checks this implies (every relaxation, not
   just every settle), this is more computation per relaxation than the
   original design, but still O(1) extra work per edge, not asymptotically
   worse.

3. `settledF`/`settledB` are still needed, but *only* for the standard
   lazy-deletion "don't reprocess a stale heap entry" mechanism — not for
   any correctness-critical mu logic anymore.

**Next steps, in order**:
1. Rewrite `bidirectional_shortest_path()` in `core/pathfinding/bidirectional_search.py`
   per the design above (checking `other_dist` instead of `other_settled`,
   both at init and on every relaxation).
2. Re-run `tests/test_bidirectional_search.py` — expect
   `test_matches_forward_dijkstra_on_random_graphs` and
   `test_matches_forward_dijkstra_on_random_directed_graphs_with_oneways`
   to pass (200 random graphs each, fixed seeds `20260710` and `99182337`
   — do not change these seeds without a reason, they're what already
   found real bugs).
3. Re-examine `test_naive_same_node_termination_would_be_wrong`: the
   `_make_meeting_edge_graph()` counterexample turned out to be sensitive
   to Python's per-process hash randomization (set iteration order for
   `sources`/`targets` affects heap tie-breaking, hence which "wrong"
   answer — if any — the deliberately-naive reference implementation
   converges to). This test is secondary/pedagogical, not the primary
   correctness evidence — the random cross-validation tests are. If it
   can't be made deterministic easily (e.g. by using ordered lists instead
   of sets for tie-breaking-sensitive inputs in that one test), consider
   simplifying or removing it rather than spending much more time on it;
   don't let it block progress on the real correctness signal.
4. Once all of `tests/test_bidirectional_search.py` passes: write
   `core/algo_bidirectional.py`, wiring the *proven* pure algorithm into
   `SearchContext`. This needs a genuine `reverse_neighbors()` method on
   `SearchContext` (mirroring `neighbors()` but flipping the
   `way_direction()` filter for oneway/escalator edges) — the sub-agent
   that originally proposed bidirectional search flagged this as the one
   place a naive port would silently mishandle directed edges, since
   `SearchContext.neighbors()` only ever yields *outgoing* edges today.
5. Register it in `algorithms.py`, and it's automatically covered by
   `test_algorithm_agrees_with_baseline` and `benchmark.py` — no other
   test-file changes needed, per the registry design above.
6. Only after real-data correctness is confirmed (same pattern as A* and
   the adjacency table: cross-check against the `dijkstra` baseline on
   every `TRANSFER_CASES` fixture) should timing claims be made.

## Miscellaneous things worth knowing

- A `.venv` exists at the repo root (`Documents/GitHub/transfr/.venv`);
  use `.venv/bin/python`, not bare `python3` — the system Python doesn't
  have `psycopg2`/`osmium`/`pytest` installed, and there's a confusing
  shell alias (`python` → `python3.8` via pyenv) that bypasses venv
  activation if you rely on `source .venv/bin/activate` alone. Always
  invoke via the explicit path.
- `core/tooling/generate_verification_report.py` regenerates the hand-verification
  report (way IDs + `openstreetmap.org` links) used to sanity-check results
  against the real map — this is how the `public_transport=station` bug
  was originally caught (by the user clicking through the links).
- Full test suite (`test_dijkstra.py` + `test_graph.py` + `test_ground_truth.py`)
  currently: 67 passed, runs in ~85s. Don't run the legacy
  `tests/test_transfers.py` — it's deleted; if it reappears, that's a sign
  something restored stale state, not a file to fix.
- There's an unrelated, harmless auto-checkpoint commit in git history
  (`509da65 "starting over"`) capturing an early mid-session state of
  `core/`. Not something to worry about or build on; the working tree
  has since diverged significantly and is the source of truth.
