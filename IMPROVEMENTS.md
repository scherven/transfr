# Transfr — product & UX backlog

**See also:** [`design/DESIGN.md`](design/DESIGN.md) (the design doc + decision log) and [`design/prototype.html`](design/prototype.html) (the interactive prototype).

Ideas surfaced while prototyping the mobile app (the interactive click-through:
plan → connections → transfer carousel → 3D walk → AR → live). Each one is
grounded in what `core/` and `api/` already provide, and ordered roughly by
value-to-effort.

**Already folded into the prototype (done, not tracked here):** verdict-first UI
(`feasible`/`tight`/`infeasible`/`unknown` from `api/transfers.py`), the
swipeable transfer carousel, the 3D platform walk (from `core/viz_export.py`),
the AR mock, and **boarding position on the transfer card** (which coach/sector
to sit in so you alight nearest the exit — from `core/boarding.py` +
`core/formation_model.py`). That last one leads with a **directional step-off
cue** — "doors open → walk toward sector C" — because the painted sector letter
is the first thing you can see on the platform, before any stairs or signage.
It's computable today: compare the alighting offset (`boarding.py`) with the
stairs' offset and map it back to a sector via `PlatformSectorMap`; it threads
through the 3D walk's first step, the AR banner, and the live "doors about to
open" moment.

**Standing caveat for all of this:** platform data only exists where the feed
publishes it — DACH + BeNeLux yes, domestic FR/IT/ES no; MOTIS also omits one
side's platform on some legs (see `memory: transitous-platform-coverage` and the
`no_platform_data`/`unknown` reasons in `api/transfers.py`). Every feature below
must degrade honestly to "we don't have this one" rather than guess.

---

## 1. Zero-input focus

**What.** Minimise typing. The front door is *import an itinerary you already
have* — an iOS share-sheet from DB Navigator, a pasted Google/Apple Maps link, or
a ticket — and Transfr reconstructs the journey and its transfers. Manual entry
(station autocomplete) becomes the fallback, not the primary path.

**Why.** The value is the transfer intelligence, not trip search — the user has
already chosen their train in another app. Every typed field is friction and a
chance to mismatch a station name.

**How it lands on the current codebase.** `api/` already exposes `/stations`
(CSV autocomplete) and `/journeys` (origin + destination + time → journeys with
transfers). The only new piece is a link/ticket parser that yields
`(origin, destination, departure_time)` — or better, the concrete leg list — and
hands it to `/journeys`. Mind the **name-normalisation gap**: MOTIS station names
differ across providers at an interchange, so resolve to DELFI/IFOPT stop-ids,
not names (`memory: transitous-platform-coverage`). Client side: an iOS share
extension + deep link.

**Effort.** Small–medium. The pipeline exists; the parser is the only real work.

---

## 2. Live re-assessment

**What.** The verdict is not static. As real-time delays arrive, re-run each
transfer's feasibility and flip `feasible ↔ tight ↔ infeasible` live. When a
change becomes unmakeable, proactively surface the next viable option.

**Why.** This is the highest-value moment in the whole app. The anxiety is
"will this delay make me miss my connection?" — and only a *live* answer helps.
It's also the clearest differentiator from a plain journey planner.

**How it lands on the current codebase.** `api/transfers.py` already computes a
per-transfer verdict from `layover_s` vs `walk_time_s` + a boarding buffer, and
rolls the journey up to its worst transfer. Feed it *live* times instead of
planned ones: `journeys.py`/MOTIS already parse `departure_delay_s` /
`arrival_delay_s` (`schemas.Leg`), so recompute
`layover = (planned_dep + dep_delay) − (planned_arr + arr_delay)` and re-verdict.
For "next option," re-query `/journeys` from the interchange station at the new
(delayed) arrival time. Needs a refresh loop + push channel.

**Effort.** Medium. The verdict logic exists; the live-refresh loop, push, and
alternative-search are new. **Underpins #5.**

---

## 3. Step-free toggle & trip settings

**What.** A settings surface. Headline switch: **"avoid stairs"** (step-free /
elevator-only routing) for luggage, wheelchairs, strollers, cyclists. Plus:
walking pace, minimum transfer buffer, escalator-vs-stairs preference, notify
lead time, fewest-changes-vs-fastest, units/language, home station.

**Why.** Step-free is a large accessibility + everyday-convenience win and it is
**nearly free given the data** — `core/` already classifies every connector as
`stairs` / `escalator` / `elevator` / `ramp` (`viz_export.node_kind`, `VIZ.md`).
A personal buffer respects that some people need more margin than the default
~60 s.

**How it lands on the current codebase.** The connector classification and a
per-node vertical cost already exist (see `core/ISSUE-node-vertical-cost.md`).
Add a **weight profile** to the pathfinder: exclude (or heavily penalise) stairs
edges when step-free is on, and expose it as a parameter through
`core.find_shortest_path` → `/journeys` and `/transfer`. The transfer buffer is
already a constant in `api/transfers.py` — promote it to a setting. Some
transfers will go `infeasible`/`disconnected` under step-free (a stair-only
underpass, e.g. the kind of gap seen at Olten 9→12) — surface that honestly.

**Effort.** Small–medium. Classification is done; wire a weight profile + param
+ the settings UI.

---

## 4. Offline cache  *(largely handled at the data layer)*

**What.** A resolved transfer — walk geometry, times, the 3D/AR scene — must work
with **no signal**, because underground platforms are exactly where connectivity
is worst and where you need the map. Cache it on-device the moment a journey is
planned.

**Why.** You open the walk view in the one place your phone has no bars.

**How it lands on the current codebase — already mostly designed for this.**
`core/viz_export.py` is deliberately a two-step contract (`VIZ.md`): resolve +
export a **self-contained JSON** (local-ENU geometry, levels, transitions,
georef origin) → render. That JSON *is* the offline unit — DB- and
OSM-independent, explicitly built so "the Swift/AR client reads the same shape."
The detail layer already caches per-bbox under `core/viz_out/_detail_cache/`.
Remaining work is client-side: on journey-plan, pre-fetch each transfer's export
JSON (+ a static map-tile set for the walk bbox), persist it, and fall back to
the cached copy when offline. Backend change is minimal: an endpoint that returns
the export JSON for a `(relation_id, ref1, ref2)`.

**Effort.** Small on the backend (expose the export JSON); medium on the client
(prefetch + persistence).

---

## 5. Live Activity / glanceable countdown

**What.** The app's real home is the lock screen / Dynamic Island:
*"Next transfer — Mannheim in 9:12 · Pl 4 → 5 · tight,"* updating live, tappable
straight into AR. You should almost never need to open the app.

**Why.** 90 % of a trip is waiting. A glanceable countdown plus a single nudge
~90 s before arrival ("open AR now") is the right interaction model — it's the
surface mocked on the prototype's Live screen.

**How it lands on the current codebase.** An iOS ActivityKit Live Activity fed by
the **same live re-assessment loop as #2**: content is the next transfer's
station, a countdown (itinerary + live delays), the platform pair, and the
verdict colour. Schedule a local notification to prompt AR just before arrival.

**Effort.** Medium (ActivityKit + the live loop). **Depends on #2.**

---

## Sequencing

- **#2 live re-assessment** is the keystone — it unlocks **#5** and sharpens the
  whole product. Build it first.
- **#4 backend** (expose export JSON) is a quick win that de-risks the offline
  and AR story.
- **#1 zero-input** and **#3 step-free/settings** are independent, high-ROI
  quick wins that can land in parallel.
