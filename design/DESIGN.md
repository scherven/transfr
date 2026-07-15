# Transfr — Design Document

> **Status:** living design doc · v0.3 · 2026-07-15
> **Prototype:** [`design/prototype.html`](prototype.html) (open it in any browser; no build step). Live copy: `https://claude.ai/code/artifact/4657fc18-96b4-497a-b05d-3cc238985bd0` (private).
> **Backlog / product ideas:** [`../IMPROVEMENTS.md`](../IMPROVEMENTS.md)

This document captures the design decisions behind the Transfr mobile prototype and the reasoning for each, so it can be argued with and iterated on. It is the design source of truth; the prototype is its executable sketch. When the two disagree, this doc wins and the prototype should be updated.

The eventual client is Swift/SwiftUI + ARKit. The prototype is HTML because it let us settle interaction and hierarchy fast; none of the HTML is meant to ship.

---

## Table of contents

0. [How to use this doc](#0-how-to-use-this-doc)
1. [Product thesis](#1-product-thesis)
2. [Grounding: how the UI maps to the engine](#2-grounding-how-the-ui-maps-to-the-engine)
3. [The prototype: what exists](#3-the-prototype-what-exists)
4. [Visual system](#4-visual-system)
5. [Information architecture & flow](#5-information-architecture--flow)
6. [Screen-by-screen](#6-screen-by-screen)
7. [Core design decisions](#7-core-design-decisions)
8. [Content & voice](#8-content--voice)
9. [Data-coverage constraints](#9-data-coverage-constraints-these-bound-the-design)
10. [Feasibility & build sequencing](#10-feasibility--build-sequencing)
11. [Open questions](#11-open-questions--to-revisit)
12. [Decision log](#12-decision-log)
13. [Translating to a Swift app](#13-translating-to-a-swift-app)
14. [Appendix: design tokens](#appendix-design-tokens)

---

## 0. How to use this doc

- **Sections 1–3** are orientation (what we're building and why, and how it sits on the existing engine).
- **Sections 4–6** are the concrete design system and screens.
- **Section 7** is the important part: the *decisions*, each with rationale and status (`locked` / `provisional` / `open`).
- **Sections 9–11** are the honest edges — what bounds the design and what's unresolved.
- **Section 12** is a scannable decision log; **Section 13** maps the design onto Swift/iOS frameworks; the **Appendix** has the raw tokens.

Status tags: **locked** = we're confident, change only with reason; **provisional** = reasonable default, expected to be revisited; **open** = flagged, not yet decided.

---

## 1. Product thesis

**The connection makes it — but can *you*?**

A journey planner tells you a transfer "works" because the timetable says the arriving and departing trains overlap at a station. It says nothing about the 78 metres, two flights of stairs, and an underpass between platform 4 and platform 5 — or that your booked seat is at the far end of a 400 m platform. Transfr owns exactly that gap: **turning each change-of-train into a concrete, walkable, timed platform-to-platform navigation, and answering "will I make it?" before *and* during the trip.**

- **Audience:** anyone changing trains in a region with platform data (initially DACH + BeNeLux — see §9), especially under time pressure or with luggage/mobility needs.
- **The one job:** remove the "will I make my connection, and where the hell is the platform?" anxiety.
- **The moat:** the verdict (`feasible`/`tight`/`infeasible`) and the real walk — not journey search (everyone has that) and not, initially, AR (hard; see §7.7). The boarding-position and step-off guidance (§7.3–7.4) are the novel, defensible surface.

**Design consequence:** the *verdict* is the hero on every screen, and the app should minimise input (you already chose your train elsewhere) and be glanceable (you live in it via a countdown, not by staring at it).

---

## 2. Grounding: how the UI maps to the engine

Every screen is a view over data the backend already produces. This is deliberate — the design is constrained to what's real, so it can't drift into fiction.

| UI concept | Backend source |
|---|---|
| Journey list, legs, times, delays | `api/` `/journeys` → `journeys.py` (Transitous/MOTIS) → `schemas.Journey`/`Leg` |
| Transfer verdict (`feasible`/`tight`/`infeasible`/`unknown`) + walk time/distance | `api/transfers.py`, per change-of-train, rolled up worst-wins |
| Platform-to-platform walk (distance, level changes) | `core/` pathfinder (`find_shortest_path`, `search_context.py`) |
| 3D walk geometry (levels, stairs/escalator/elevator risers) | `core/viz_export.py` JSON contract (the "AR contract", see `core/VIZ.md`) |
| Direct walk lookup (station + two platform refs → walk view, no journey) | `core/` pathfinder + `viz_export` directly, bypassing `/journeys` and the verdict (see §6.9/§7.10) |
| Boarding position (which coach/sector → where you alight) | `core/boarding.py` (seat→offset→point) + `core/formation_model.py` (sector map) |
| "no platform feed" / `unknown` reasons | `api/transfers.py` reasons; `no_platform_data`, `cross_station`, `platform_not_found` |
| Station autocomplete | `/stations` |

Key properties inherited from the engine that the design must respect:
- **Platform refs are arbitrary strings** (`'Gl 1'`, `'5a'`, `'Regio 3'`), never integers.
- **Z is `level`, not elevation** — OSM has effectively no indoor `ele`; the 3D view shows evenly-spaced level planes, not true heights (`core/VIZ.md`).
- **Connectors are typed** — stairs / escalator / elevator / ramp (`viz_export.node_kind`). This is what makes step-free routing and the coloured risers nearly free.
- **The export JSON is self-contained and georeferenced** (local ENU metres + origin) — it's the offline/AR unit.

---

## 3. The prototype: what exists

A single, coherent, click-through mobile prototype (not scattered mockups). 15 screens, all wired — every back chevron, button, the carousel swipe, the theme toggle, the settings controls, the live countdown, and the Advanced tools work.

1. **Plan** — link-paste, type, or **walk-only** (§6.9); everything editable. Gear → Settings.
2. **Connections** — journey list, verdict-first.
3. **The connection** — vertical timeline of legs + transfer cards.
4. **Transfers** — swipeable carousel, one card per change (boarding + step-off cue).
5. **Walk views** — the three §7.6 representations in one screen (section overview · per-level plans · rotatable 3D), coloured risers, turn-by-turn. The 3D is a live orthographic orbit (real geometry, drag to rotate) standing in for the embedded `viz_render` scene.
6. **AR** — mocked camera with the path overlaid + step-off instruction.
7. **Live** — map, moving position, countdown to next transfer.
8. **Settings** — step-free, walking pace, makeable %, buffer, theme, units, Live Activity, auto-AR; **Power tools → Advanced** and **About → Attributions**.
9. **Walk lookup** — station + two platform refs → the walk view directly, verdict-free (the §6.9 door; Berlin Hbf 1→16 as the multi-level example).
10. **Advanced** (hub) — power tools over the same `core/` engine (§6.10), reached from Settings.
11. **Full station walk** — one platform → every other platform (distance · time · level Δ · step-free), with a source selector; **tap a row → the full walk view (§6.5)**.
12. **Nearest facility** — nearest toilets / lift / exit / tickets / coffee / ATM / taxi from a platform, routed, from the OSM POI layer; **tap a facility → the full walk view**.
13. **Map health** — per-database connected / stitchable / island connectivity (§6.10, §7.11), region selector + an all-databases comparison; not user-editable.
14. **Offline & regions** — install / update / remove regional DBs (Europe, Korea, Japan), prefetch a station's 3D detail, storage & freshness.
15. **Attributions** — data sources & licences, led by **Map data © OpenStreetMap contributors** (ODbL) (§6.11), reached from Settings.

The **Hamburg → Stuttgart** ICE journey is the running example, with **Göttingen 7→8** (feasible, cross-platform) and **Mannheim 4→5** (tight; stairs → underpass → stairs) as the deliberate throughline — the change that's fine on paper but tight in practice, and therefore the one that justifies the 3D map and AR.

---

## 4. Visual system

### 4.1 Palette

Two colour families, kept strictly separate so they never fight:

- **The path (brand accent) — azure.** One bold colour, and it always means *your route / the way to go*: the route line on the map, the walk path in 3D, the AR arrow, active states, primary actions. `#0A63F0` on light, `#4EA6FF` on dark (luminous enough on a near-black ground).
- **Semantic verdict colours — green / amber / red.** Reserved for `feasible`/`tight`/`infeasible`, plus a neutral slate for `unknown`. These live in small pills and rings, spatially apart from the big azure path, so "the blue thing" (where to go) and "the green/amber thing" (can you make it) never collide.
- **Connector kinds** — stairs = purple, escalator = teal, elevator = coral. Chosen to be distinct from both the azure path and the verdict trio; they answer "which thing do I take between floors."
- **Neutrals are cool and blue-biased**, not default grey — picked to sit under the azure accent and evoke signage/night-platform lighting.

Full hex in the [Appendix](#appendix-design-tokens). **Decision:** the accent is the *only* saturated brand colour; everything else is neutral or semantic. `[locked]`

### 4.2 Typography

- **UI/display:** the native system stack (`-apple-system` / SF Pro). **Two reasons, both deliberate:** (1) the Artifact/its host CSP blocks webfont CDNs, so a linked font would silently fall back; (2) SF *is* what a real iOS transit app ships, so it makes the mock read as native rather than as a web page. This is a choice, not a fallback. `[locked for prototype]` — the Swift client inherits SF for free; revisit only if we want a custom display face and are willing to license/bundle it.
- **Numerals — times, platforms, distances, countdowns:** monospaced, tabular (`ui-monospace`/SF Mono). This is the departure-board vernacular and keeps digits from jittering as they update. `[locked]`
- **Micro-labels** (`ARRIVE`, `DEPART`, `LAYOVER`): uppercase, letter-spaced, tiny. Transit-signage texture. (Note: the inline chat widgets use sentence case because that host's design system forbids all-caps; the app itself keeps the uppercase micro-labels.) `[provisional]`
- Headings use `text-wrap: balance`; running text kept comfortable.

### 4.3 Iconography

Inline SVG line icons, ~1.5–2.4 stroke, no fills — consistent weight with the type. No icon library dependency in the prototype. The Swift client should use SF Symbols (same visual language). `[locked]`

### 4.4 Motion

- **Screen router:** screens are stacked and slide + fade. Forward pushes the incoming screen in from the right and the outgoing out to the left; **back reverses direction** (in from the left, out to the right). 380–400 ms, eased. This directional consistency is the main way the prototype communicates depth/hierarchy. `[locked]`
- **Reduced motion:** `prefers-reduced-motion` collapses transitions to a near-instant fade and stops the AR scan-line and the live animation loop. `[locked]`
- Micro-interactions (button lift on hover, toggle knob slide) are small and functional, never decorative.

### 4.5 Layout & the phone frame

- The prototype renders a **pixel iPhone frame** (Dynamic-Island status bar, home indicator) on a soft studio backdrop, with a **screen navigator** beside it (a labelled list of the 15 screens, grouped with an *Advanced* section) so a reviewer can jump anywhere *or* walk the real flow. On narrow viewports the navigator becomes a horizontal strip and the phone fills the width.
- Inside the phone: a fixed status bar, a clipped `viewport` holding the stacked screens, and a home bar. Each screen scrolls internally.
- **Everything that looks editable is editable** — station names, times, the pasted link are real inputs/`contenteditable`. A prototype principle: don't show a field you can't touch. `[locked as prototype principle]`

---

## 5. Information architecture & flow

```
Plan ─┬▶ Connections ──▶ The connection ──▶ Transfers ──▶ 3D walk ⇄ AR
      │                       │                              ▲
      ├▶ Settings ─┬▶ Advanced ─┬▶ Full station walk ─┐      │
      │            │            ├▶ Nearest facility ──┼──────┤  (tap → walk view)
      │            │            ├▶ Map health         │      │
      │            │            └▶ Offline & regions  │      │
      │            └▶ Attributions                    │      │
      │                       └▶ Live ──▶ (Preview) ──▶ Transfers
      │                                                      │
      └▶ Walk lookup (station + platform A + platform B) ────┘
```

- The **spine** is Plan → Connections → The connection. From the connection you branch to the **transfer carousel** (browse all changes) or **Live** (the on-trip mode).
- **3D walk ⇄ AR** are two representations of the same transfer and toggle between each other.
- **Live** is reachable directly (it's where the app spends most of its time) and deep-links into a transfer preview.
- **Walk lookup** is a **second entry door on Plan** (§6.9): pick a station and two platform refs and jump *straight* to the walk view, bypassing the journey spine entirely. It reuses the exact walk screen the transfer path lands on; it just arrives there without a journey, layover, or verdict behind it.
- **Settings** hangs off the home screen (gear) — not in the main flow. It also holds the two secondary areas: **Advanced** (§6.10) and **Attributions** (§6.11).
- **Advanced** is a hub of power tools that answer *station* questions rather than *journey* questions (full station walk, nearest facility, map health, offline/regions). Its walk-producing tools (station walk, nearest facility) **rejoin the main flow at the walk view (§6.5)** — they reuse the exact screen the transfer path lands on, arriving without a journey.

**Decision:** browsing (carousel) and doing (Live's single "next thing") are separate modes. The carousel is for pre-trip understanding; Live collapses to just the next action. `[provisional]`

---

## 6. Screen-by-screen

### 6.1 Plan
- Two ways in — **paste a link** (Google/Apple Maps, DB Navigator) or **type it** (from/to with `/stations` autocomplete). Link-paste is intended as the primary door (see §7 zero-input). Recent trips for one-tap repeat.
- Rationale: the value is downstream of trip choice; minimise typing.

### 6.2 Connections
- Each journey is a card: time range, duration, changes, **a single verdict badge**, and a compact flow showing the interchange stations with their platform pairs colour-coded by per-transfer verdict.
- Includes a **`tight`** option and an **`unknown` (no platform feed)** option on purpose, so the verdict system and the honest-gaps handling are both visible.

### 6.3 The connection (timeline)
- Vertical timeline: origin → legs (train cards with direction + platform) → **transfer cards** (highlighted, verdict-bordered) → destination. Times are mono; delays shown inline (`+3 min` in red).
- Transfer cards summarise the change (Pl 4→5, walk, time left, level note, a one-line boarding hint) and tap through to the carousel.

### 6.4 Transfers (carousel)
- One card per change, horizontally swipeable, scroll-snapped. The hero surface. Each card: station, arrive/depart platform, a **feasibility ring** (walk vs layover, coloured by verdict), the **boarding module** (§7.3–7.4), a level/stairs description, stats (distance / level Δ / spare), and **3D / AR** buttons.
- Adapts per transfer: Göttingen (cross-platform) is relaxed; Mannheim (tight) is the full treatment.

### 6.5 The walk view — three representations
The transfer's walk is shown three complementary ways (§7.6), with the default chosen by how many levels the path spans:
- **Section overview** — a stylised side elevation: level bands, the azure path threading up, risers coloured by connector kind, endpoints labelled with platform + level. The fastest read of "how far up/down, and what do I take between floors." Plus a **turn-by-turn** list whose first step is the step-off cue.
- **Per-level plans** — one clean top-down per floor, switched with a picker, with paired "from L−1 / to L+1" transition markers. Precise wayfinding.
- **Rotatable 3D** — the real `core/viz_render` scene (WebGL, orthographic orbit): translucent per-level planes, walkways/platforms, and the whole path with coloured risers. Spatial overview and the pre-AR model.
All reflect `viz_export`'s "Z = level, not elevation" honestly (labelled, evenly-spaced planes, not fake heights).

### 6.6 AR
- Mocked camera: a receding floor grid, the glowing azure path with chevrons toward a vanishing point, a floating destination pill, a distance badge, and a top **instruction banner** ("Walk toward sector C — the stairs down are there"). Controls to recenter / drop to the 3D map.
- Framed in the doc and copy as anchored from the georeferenced export — and flagged as the hard, later feature (§7.7).

### 6.7 Live
- A simplified map with the route, a pulsing "you", and stations (next transfer flagged). A **next-transfer card**: countdown, verdict, the platform move, the step-off cue, and a Preview button. Copy notes the ~90 s pre-arrival AR nudge.

### 6.8 Settings
- Grouped: **Getting around** (step-free toggle, walking pace, prefer-escalators), **Making the connection** (the makeable % slider + boarding buffer), **Appearance** (theme, units), **On the move** (Live Activity, auto-AR lead time).

### 6.9 Walk lookup (direct platform-to-platform)
A minimal, verdict-free door for "I just want to see the walk." A second tab/mode on **Plan** (§6.1): **one station** (with `/stations` autocomplete) and **two platform refs** — free-text/pickers over the arbitrary-string refs (`'Gl 1'`, `'5a'`, `'Regio 3'`), never integer steppers. A single **Show walk** action resolves both refs and lands directly on the walk view (§6.5) — section overview / per-level / rotatable 3D — with turn-by-turn.
- **Why it exists:** the walk view is fed by one `viz_export` JSON, which `core/` produces from `(station, platform A, platform B)` alone. None of the journey machinery (timetable, layover, arriving/departing trains) is needed to draw a walk, so exposing it directly is nearly free and serves a real use — scoping out an unfamiliar station, checking a step-free route, or answering "how bad is that change?" without booking anything.
- **What it drops vs the transfer path:**
  - **No verdict.** With no layover there's nothing for `feasible/tight/infeasible` to measure, so the feasibility ring is absent. The screen leads with the *facts* instead — distance, walk time (at the Settings pace), level Δ, connector kinds. This is the one place the walk view appears without a verdict driving it (§7.10).
  - **No boarding / step-off** (§7.3–7.4): no arriving train ⇒ no seat offset or sector to aim for.
- **Reuses everything else:** step-free routing, walking-pace, units and theme (§7.9) all apply unchanged, since they live in `core/` routing + presentation, not in the verdict.
- **Honest gaps still hold** (§7.5): an unmapped or `disconnected` platform pair reports *why* (`platform_not_found`, `disconnected`) rather than inventing a path — the same reasons the transfer path surfaces.

### 6.10 Advanced (power tools)
A hub reached from **Settings → Power tools**, holding tools that answer *station* questions instead of *journey* questions. All run on the same `viz_export` / pathfinder as a transfer walk — pointed at a different question, with no verdict or train.
- **Full station walk** — pick a source platform; get distance / walk time / level Δ to **every** other platform (one pathfind per platform), sorted nearest-first, with a step-free marker per row (respecting the Settings step-free toggle). **Tapping any row opens the full walk view (§6.5)** for that pair — the same section / per-level / 3D screen a transfer lands on.
- **Nearest facility** — category chips (toilets · lift · exit · tickets · coffee · ATM · taxi) → the nearest one **routed** from the current platform, plus every instance in the station ranked by distance. Facilities come from the OSM `amenity`/`shop` POI layer (`viz_export`); **tapping one opens the full walk view** to it. If a station maps none, it says so rather than guessing (§7.5).
- **Map health** — see §7.11. Per-database connectivity (connected / stitchable / island) with a region selector and an all-databases comparison; purely diagnostic.
- **Offline & regions** — install / update / remove regional databases (Europe, Korea, Japan; each built offline from an OSM extract via `extract_europe.sh`, so an installed region works with no network), prefetch a station's 3D detail cache for offline, and a storage/freshness panel. A not-installed region (Great Britain) demonstrates the download flow.
- **Why here, not in the spine:** these are lookups a power user reaches for occasionally; folding them behind Settings keeps the Plan → verdict spine uncluttered while giving the walk engine a second life. `[provisional]`

### 6.11 Attributions
A required, plain **data-sources & licences** page reached from **Settings → About**. Leads with the hero credit **"Map data © OpenStreetMap contributors"** and the **ODbL** licence, because every map, platform, footway, level and facility in Transfr derives from OSM. Also lists Transitous/MOTIS (journeys, live delays, platform assignments), Deutsche Bahn IRIS (real-time platform/track), and coach-formation providers, plus a "built with `osmium`, no run-time tiles" note. `[locked — attribution is a licence obligation, not a design choice]`

---

## 7. Core design decisions

### 7.1 Verdict system & worst-wins  `[locked]`
Four states, from `api/transfers.py`: **feasible / tight / infeasible / unknown**. Surfaced as coloured pills/rings, verbally as **Makeable / Tight / Missed / Unknown**.
- **A journey's badge is the worst of its transfers.** "Makeable" is shown only if *every* transfer is makeable; one tight transfer makes the whole journey read **Tight**. This matches the backend roll-up and was a correction during design — a green "Makeable" on a journey containing a tight change is a lie.
- **Why verbal + colour:** colour for glance, a word for certainty and accessibility (never colour alone).

### 7.2 The "makeable" threshold  `[provisional]`
A user-tunable comfort setting (Settings). Model: a transfer is **makeable** when the walk consumes under **X%** of the layover, **tight** between X% and 100%, a **miss** above 100%. Default **70%**. The setting shows a live 3-zone bar and a worked example ("on an 8-min connection, up to 5:36 of walking").
- **Why a percentage:** it scales with layover length and is intuitive as "how much margin do I insist on."
- **Alternative to weigh:** absolute spare seconds, or a "confidence" label (relaxed/normal/brisk already overlaps). Open whether % or seconds is the primary knob. Maps onto the existing buffer check in `transfers.py`.

### 7.3 Boarding position on the transfer card  `[locked as direction, data provisional]`
The card tells you **which coach/sector to be in so you alight nearest the exit**, because a mainline platform is 300–430 m long and "you arrive on platform 4" hides a multi-minute walk difference. Direct surfacing of `boarding.py` (seat→offset→point) + `formation_model.py` (sector map / resolution ladder).
- Rendered as a **sector strip (A–E)** with the target sector lit and a stairs marker, plus a line contrasting the recommended coach with the user's booked seat ("booked in coach 9, rear; coach 3 lands you at C").
- **It adapts:** when boarding barely matters (Göttingen, 18 m cross-platform) the module goes relaxed ("any coach"). Showing that it knows *when it doesn't matter* is what makes it feel intelligent rather than nagging.
- **Data caveat:** coach-formation feeds vary by operator (DB metres; SBB/ÖBB sectors; NS/SNCF order-only) and the DB formation host is geo-blocked from non-DE egress (see memory `formation-api-reachability`). Sector-level is the honest common denominator.

### 7.4 The step-off cue  `[locked as direction; wording open]`
The **first line** of the boarding module is a directional instruction: **"Doors open → walk toward sector C."**
- **Why it leads:** the painted **sector letter is the first thing you can see** the instant you're on the platform — before stairs, signage, or the underpass. So the most immediately actionable cue names the sector to head for. It threads through the 3D first step, the AR banner, and the Live "doors about to open" card.
- **Computed, not hand-waved:** compare the alighting offset (`boarding.py`) with the stairs' entry offset; the smaller tells you which end; map that offset back to a letter via `PlatformSectorMap`.
- **Only when needed:** if there's no sector to aim for (cross-platform), we don't mention one — trailing "no sector to aim for" was removed as noise.
- **Open — direction wording:** "toward the front of the train" reads naturally but depends on the train's orientation (the formation feed's `reversed` flag). The orientation-independent "toward sector A / the low end" is always correct without knowing orientation. Decide per §11.

### 7.5 Honest gaps  `[locked]`
When platform data is missing, the app **says so** (`unknown` / "no platform feed") rather than guessing. Verdict badges, journey cards, and reasons all carry this. An app that admits "I don't know this one" earns more trust than one that fabricates a walk — and our coverage genuinely is uneven (§9).

### 7.6 The walk view — section overview + per-level plans + rotatable 3D  `[locked]`
A complex transfer is the test: **Berlin Hbf Pl 1 → 16** is a 4-storey climb — L−2 up to L+2, an escalator then a single elevator, 107 m / 122 s across 9 mapped levels (real `viz_export`, relation 5688517, verified from `core/viz_out/5688517_1_16.json`). No single view serves it, so the walk screen carries **three complementary representations**, and picks the default by how many levels the path spans:

1. **Section overview** — a stylised side elevation (2D, SwiftUI-drawable): level bands, the path threading up, risers coloured by connector kind, endpoints labelled with platform + level. It is the *fastest read* of the vertical story ("how far up/down, how many transitions, what do I take"), needs no interaction, and stays legible on a phone. **First-class — not just a fallback for simple transfers.**
2. **Per-level plans** — one clean top-down per floor, switched with a picker; the paired "from L−1 / to L+1" transition markers are the connective tissue that stitch the floors back into one journey. This is where turn-by-turn lives.
3. **Rotatable 3D of the whole path** — the *actual* `core/viz_render.py` scene (Plotly/WebGL today, orthographic orbit): translucent per-`level` planes, context walkways/platforms, and the path with vertical circulation drawn as risers coloured by connector kind (stairs/escalator/elevator/ramp), start/end platforms marked. The spatial overview and the pre-visualisation for AR.

They divide cleanly — **section = glance, per-level = precision, 3D = spatial model** — which is why all three are kept rather than one chosen. **Z is level-derived** (evenly-spaced, ×3 exaggerated for legibility), stated honestly per `VIZ.md`. All three fall out of one `viz_export` JSON, so they never drift (see §13.3).

**In-app it's embedded, not reinvented** (no backend work — the export already exists): a `WKWebView` over the self-contained `viz_render` HTML ships the real rotatable viewer immediately; a SceneKit/RealityKit port reading the same JSON is the native follow-up. See §13.3–13.4.

**Rejected:** a hand-built CSS/stacked-plate "3D overview" — it is neither real geometry nor legible under rotation. The rotatable view must be the real scene.

### 7.7 AR — the hard frontier  `[open]`
Framed throughout as the flagship *v2*, not the launch centrepiece.
- **Outdoor** geo-anchoring (ARKit Location Anchors) is feasible. **Indoor** — where the transfer actually happens — is the hard problem: GPS is unreliable inside, and our Z is level-not-elevation, so a true world-anchored indoor path needs visual positioning (VPS) or image/QR anchors on platform signage.
- **Decision:** ship 2D/3D first; unlock AR station-by-station as positioning allows. The `viz_export` JSON is already the georeferenced contract an ARKit client would read.

### 7.8 Live & Live Activity  `[provisional]`
The app's real home is the lock screen / Dynamic Island: a glanceable countdown to the next transfer with the platform pair and verdict, plus a single **~90 s pre-arrival nudge** to open AR. In-app Live is the expanded version. Depends on **live re-assessment** (re-verdict on real-time delays — the keystone in `IMPROVEMENTS.md`).

### 7.9 Settings  `[provisional]`
Prioritised to the cheap, high-value ones given the data:
- **Step-free routing** — near-free: connectors are already typed, so it's a routing weight profile that excludes/penalises stairs. Big accessibility + luggage win.
- **Makeable %** (§7.2), **boarding buffer** (the backend's ~60 s), **walking pace** (scales walk time), **prefer escalators**, **units**, **theme** (System/Light/Dark, actually functional), **Live Activity**, **auto-AR lead time**.

### 7.10 Direct walk lookup — the verdict-free door  `[provisional]`
A second, minimal entry point (§6.9): station + two platform refs → the walk view, with no journey, layover, or verdict. It falls out of the same seam that makes §7.6 cheap — the walk view depends only on a `viz_export` JSON, and `core/` produces that from two resolved platform endpoints without any timetable input.
- **Why keep it separate from the journey spine rather than folding it in:** the two modes answer different questions — the spine answers *"will I make this connection?"* (verdict-first, §1), the lookup answers *"what does this walk look like?"* (wayfinding-first). Bolting a fake layover onto a lookup just to force a verdict would violate §7.5 (honest gaps) and §7.1 (verdict is the hero *when there is one to earn*).
- **The design tension it introduces:** every other screen makes the verdict the hero (§1, D1). The lookup is the one screen where the walk stands alone. Resolution: the walk view already renders fully without a verdict pill (the ring is a *layer*, not the frame), so the lookup simply omits that layer and promotes the raw facts (distance / time / level Δ / connectors) to the top. It reads as "the walk view, minus the countdown pressure," not as a different screen.
- **Reuse, not a new subsystem:** same resolver (the platform-ref resolution ladder from `formation_model`/transfer path), same `viz_export`, same three renderers, same Settings (step-free, pace, units). The only net-new surface is the input mode on Plan.
- **Open:** whether the lookup should optionally accept a coach/seat to restore the step-off cue (§7.4) for a user who *does* know their train but doesn't want the full journey flow — a cheap add, deferred until the base lookup ships. See §11.

### 7.11 Map health & stitching — diagnostic, cross-database, never user-editable  `[locked]`
The Advanced **Map health** screen (§6.10) exposes, honestly, *why* a platform-to-platform walk can or can't be computed — the same three classes from the `disconnected-diagnosis` work:
- **Connected** — the platforms share the pedestrian graph; full walks route end-to-end.
- **Stitchable** — a pedestrian way's node lies *inside* a platform polygon but shares no node with it (e.g. Colmar A→E, a 3.9 m underpass gap). The system **bridges this automatically** in ETL; the screen flags it **amber** only to say the join is *inferred, not mapped* — a confidence signal.
- **Island** — no pedestrian way exists between platforms at all (e.g. Seoul mainline: only tracks between islands). We fall back to a labelled straight-line estimate, never a fabricated path (§7.5).
- **Stitching is a property of the map, not a user control.** An earlier draft offered a "add a manual bridge" button; **removed** — a traveller can't and shouldn't edit the graph. The screen is read-only.
- **It spans databases, not one region.** A region selector (Europe / Korea / Japan) drives the survey + representative stations, and an *all-databases* strip compares them at a glance. This makes the coverage story (§9) visible rather than buried.
- **Numbers are measured where possible.** `transfr_eu` (**71 / 5 / 24** over 1,401 sampled platforms) and `transfr_kr` (**2 / 5 / 93** over 899) come from `stitch_survey.py` sweeps; Japan's figure is illustrative until its sweep runs, and is labelled as such. The gulf between EU and KR is the honest headline: connectivity depends entirely on how well the local concourse is mapped in OSM.

---

## 8. Content & voice

- **Verdict-first and plain.** "Makeable", "Tight", "Missed" — not "feasible per timetable overlap."
- **Instructions are physical and immediate:** "walk toward sector C", "down to the underpass, along, back up", "board coach 3."
- **Honest about uncertainty:** "no platform feed" beats a fake number.
- **Numbers are concrete:** real walk seconds, metres, level deltas — never "a short walk."
- Active voice; a control says what it does. (The prototype leans slightly playful in framing copy; the shipping app should stay plainer.)

---

## 9. Data-coverage constraints (these bound the design)

The design must degrade gracefully because the data is uneven — see memories `transitous-platform-coverage` and `new-api-architecture`:
- **Platform data exists** in DE/CH/AT/BE/NL; **not** domestic FR/IT/ES. Transfr is only fully useful where platform data exists — this bounds the initial market and is why the app must handle `unknown` first-class.
- **MOTIS omits one side's platform** on some legs even in DACH (`no_platform_data`) — asymmetric and common.
- **OSM gaps:** some platforms are unmapped or not connected (a real "disconnected" result, e.g. Olten 9→12). Measured by `stitch_survey.py`: **`transfr_eu` 71 % connected / 5 % stitchable / 24 % island** (1,401 sampled platforms); **`transfr_kr` 2 / 5 / 93** (899). Connectivity tracks how well the concourse is mapped, not the country's size. Surfaced honestly in **Map health** (§6.10, §7.11).
- **Name normalisation gap:** MOTIS station names differ across providers at an interchange; the reliable key is the DELFI/IFOPT stop-id, not the name. `core/` resolves by name — an open integration gap that affects link-import (§7 zero-input).

---

## 10. Feasibility & build sequencing

The "route → transfers → verdict" spine is **done** (a frontend over `/journeys`). Recommended order:
1. **Live re-assessment** — the keystone; re-verdict on live delays. Unlocks Live Activity.
2. **Expose the `viz_export` JSON** per transfer (backend quick win) — de-risks offline + 3D + AR.
3. **Zero-input** (link/ticket import) and **step-free + settings** — independent, high-ROI.
4. **3D walk** — scoped port of `viz_render` → SceneKit/RealityKit on the same JSON.
5. **AR** — v2, station-by-station, gated on positioning.
Detail and code-grounding for each in [`../IMPROVEMENTS.md`](../IMPROVEMENTS.md).

---

## 11. Open questions / to revisit

1. **Step-off wording** — "toward the front" (needs `reversed` per train) vs "toward sector A / low end" (always correct). §7.4.
2. **Makeable threshold** — percentage vs absolute spare seconds as the primary knob. §7.2.
3. **Boarding on Live** — a partial "walk toward C" line is on Live; should Live get the full sector strip, or stay minimal? §5.
4. **AR positioning** — VPS vs image/QR signage anchors; which stations first. §7.7.
5. **Name normalisation** — resolve link-imported stops to stop-ids before hitting `/journeys`. §9.
6. **Uppercase micro-labels** — keep the signage texture, or go sentence-case for calm? §4.2.
7. **Direct vs multi-modal** — the prototype is rail-only; do we show the walk to the *first* platform / from the *last*?
8. **Walk lookup + coach** — should the verdict-free lookup (§6.9/§7.10) optionally take a coach/seat to restore the step-off cue, for a user who knows their train but skips the journey flow? §7.10.

---

## 12. Decision log

| # | Decision | Rationale | Status |
|---|---|---|---|
| D1 | Verdict is the hero on every screen | The product is "can you make it," not journey search | locked |
| D2 | Journey badge = worst transfer (worst-wins) | Green on a tight journey is a lie; matches `transfers.py` | locked |
| D3 | Azure = the single brand accent; = "the path" everywhere | One bold colour, reused as route/3D/AR path identity | locked |
| D4 | Verdict green/amber/red kept separate from the azure path | Avoids colour collision between "where" and "whether" | locked |
| D5 | Native SF stack, no webfont | CSP blocks CDNs + reads as native iOS | locked (prototype) |
| D6 | Tabular mono for all times/platforms/distances | Departure-board vernacular; digits don't jitter | locked |
| D7 | Mannheim 4→5 tight transfer as the throughline | Feasible-on-paper-but-tight is where 3D/AR earn their place | locked |
| D8 | Boarding position folded into the transfer card | Surfaces `boarding.py`; novel & defensible; adapts when irrelevant | locked (data provisional) |
| D9 | Step-off cue ("walk toward sector C") leads the card | Sector label is the first visible reference on a platform | locked (wording open) |
| D10 | Mention a sector only when there is one to aim for | Cross-platform hops have none; otherwise it's noise | locked |
| D11 | Honest `unknown` / "no platform feed" | Trust > fabricated walks; coverage is genuinely uneven | locked |
| D12 | Walk view = section overview + per-level plans + rotatable 3D | Multi-level transfers (Berlin 1→16) need all three; section=glance, levels=precision, 3D=spatial | locked |
| D13 | AR is v2, gated on indoor positioning | Indoor world-anchoring is unsolved; 3D gets ~80% risk-free | open |
| D14 | Makeable = walk under X% of layover (default 70%) | Tunable margin that scales with layover | provisional |
| D15 | Step-free toggle as a headline setting | Near-free (connectors already typed); big accessibility win | provisional |
| D16 | Directional screen router (forward/back reverse) | Communicates depth/hierarchy; the main motion device | locked |
| D17 | Everything editable is editable (prototype) | Don't show a field you can't touch | locked (prototype) |
| D18 | Rotatable 3D = the real `viz_render` scene, embedded (WKWebView → SceneKit) | Reuse the built viewer; one `viz_export` JSON feeds every renderer | locked |
| D19 | Reject the CSS stacked-plate "3D overview" | Not real geometry; illegible under rotation | locked |
| D20 | Z = level, not elevation, stated honestly (×3 exaggeration) | OSM carries no usable indoor `ele`, per `VIZ.md` | locked |
| D21 | Direct walk lookup (station + two platform refs), verdict-free, as a second Plan door | Walk view needs only a `viz_export` from two endpoints — no journey/layover; serves "just show the walk" nearly free (§6.9/§7.10) | provisional |
| D22 | Advanced hub behind Settings for *station* (not journey) tools | Gives the walk engine a second life without cluttering the verdict spine (§6.10) | provisional |
| D23 | Station-walk & nearest-facility rows open the real walk view (§6.5) | Reuse, not a new renderer — same `viz_export` seam as §7.6/§7.10; one 3D screen everywhere | locked |
| D24 | Map health is read-only, cross-database, measured; no manual stitching | Stitching is a map property, not a user control; comparing DBs makes coverage (§9) visible (§7.11) | locked |
| D25 | Attributions page leading with "Map data © OpenStreetMap contributors" (ODbL) | Licence obligation, not a design choice; all geometry derives from OSM (§6.11) | locked |

---

## 13. Translating to a Swift app

The eventual client is **SwiftUI + ARKit**. This section maps each design piece to concrete Apple frameworks. The architectural keystone: the `viz_export` JSON is a single `Codable` contract that feeds every walk renderer, so the three (soon four, with AR) representations never drift.

> **Status (2026-07-15):** the shared logic package **`ios/TransfrCore`** exists — the `Codable` mirrors of `api/schemas.py` and `viz_export`, the `Verdict` worst-wins logic, the async API client (journeys / stations / transfer / **walk / walks**), and a Swift Testing suite that decodes the Python engine's own goldens (14 tests green on the iOS Simulator). The walk-delivery endpoints (`/walk`, `/walks`) are built server-side too (§13.9). The SwiftUI app target is not yet scaffolded. See `ios/README.md`.

### 13.1 App shell & navigation
- **SwiftUI** throughout. Screens are views; the prototype's screen router → **`NavigationStack`** (value-driven), whose default push/pop *is* the directional forward/back slide. The transfer carousel → **`TabView(.page)`** or a horizontal `ScrollView` with `.scrollTargetBehavior(.paging)`. Fluid page transitions → `matchedGeometryEffect`, and on iOS 18+ `.navigationTransition(.zoom)`.
- State: one `@Observable` `TripModel` (Observation framework) holds the journey, its transfers, and live delays.

### 13.2 Data layer
- An async/await **`URLSession`** client over the FastAPI (`ios/TransfrCore/TransfrClient.swift`). `Codable` structs mirror `api/schemas.py` (`Journey`, `Leg`, `Transfer`, `Place`). The verdict is `enum Verdict { case feasible, tight, infeasible, unknown(String?) }`; the journey verdict is a worst-wins `rolledUp()` — a direct port of `api/pipeline.py:_VERDICT_RANK` (`infeasible < unknown < tight < feasible`, empty ⇒ feasible), not a guess (§7.1).
- Near-term the app is a **thin client** over `/journeys` + `/transfer`; `core/` stays server-side. The pure, DB-independent modules (`boarding.py`, `formation_model.py`, ultimately the pathfinder) port cleanly to Swift value types later if on-device routing is wanted.

**Data tiering — what lives where.** The routing brain is a 24 GB Postgres DB (73 M nodes / 16 M ways for EU; Korea separate). That does *not* go on device, and it should *not* be reimplemented in Swift — routing is per-station and its answer is already baked into `viz_export`. So:

| Data | Size | On device? |
|---|---|---|
| EU/KR OSM routing DB | ~24 GB each | **No** — server-only, behind `/journeys` + `/transfer`. |
| `stations.csv` (autocomplete) | 16 MB / 70 k rows | **Yes**, bundled — instant offline station search. |
| Per-transfer `viz_export` JSON | KB–low-MB each | **Yes**, cached on trip-plan — the offline unit (§13.9). |

The consequence: the phone never stores "all the data," it stores the *answers* for trips the user actually has. This is also what makes algorithm churn a non-event for the client (§13.11): the pathfinder can change freely behind the JSON contract without any Swift change.

### 13.3 The `viz_export` JSON — one contract, four renderers  *(the keystone)*
Define `struct VizExport: Codable` matching the JSON (`meta`, `ways`, `path`, `transitions`, `details`, endpoints; coords as `SIMD3<Float>` in local-ENU metres). One decode drives all of:
1. **Section overview** — SwiftUI **`Canvas` / `Path`** (2D). Project each segment to (along-track, level); draw bands + risers. Cheap, offline, no 3D dependency.
2. **Per-level plans** — a `Canvas` per floor; filter segments by `level_raw`; a `Picker` switches floors; markers come from `transitions`.
3. **Rotatable 3D** — **SceneKit** (§13.4).
4. **AR** — **RealityKit** (§13.5).

Add a station or fix geometry once and every view updates — the reason to keep all representations on one export rather than bespoke data per screen.

### 13.4 The rotatable 3D — two stages
- **Stage 0 (ship now):** a **`WKWebView`** loading the self-contained `viz_render` HTML. Zero new rendering code — the exact scene rendered in-chat.
- **Stage 1 (native):** **SceneKit** reading the JSON. `SCNView(allowsCameraControl: true)` for orbit; `camera.usesOrthographicProjection = true` (matches `viz_render`); `SCNGeometry` line primitives from `path` / `ways`, an `SCNPlane` per level, risers coloured by `transition.kind`, start/end as marker nodes. Essentially a 1:1 port of `build_figure`. (Use RealityKit instead if you want to share entity code with AR.)

### 13.5 AR
- **ARKit + RealityKit**, hosted by SwiftUI **`RealityView`**. The JSON's `origin_lat/lon` → an **`ARGeoAnchor`** (Location Anchors) outdoors; indoors an `ARImageAnchor` / `ARWorldMap` seeded from platform signage (VPS). Path → `ModelEntity` tubes/arrows; the step-off arrow → a billboard entity. Z = level (schematic vertical, honest — not surveyed). Gated per §7.7.

### 13.6 Boarding & step-off
- Pure data → SwiftUI views. Compute the coach/sector + "walk toward X" server-side, or port the small `boarding` / `formation_model` logic to Swift structs. The sector strip is an `HStack` of cells; the step-off cue a `Label`.

### 13.7 Live tracking & Live Activity
- **CoreLocation** for position, **MapKit** (`Map`) for the route. The lock-screen / Dynamic-Island countdown → **ActivityKit** Live Activity + **WidgetKit** views, refreshed by background **APNs** driven by the live-delay feed (**live re-assessment**, the §10 keystone). A local notification fires the ~90 s AR nudge.

### 13.8 Theming & type
- Tokens → an **Asset Catalog** colour set per token with light/dark variants (or a `Theme` struct keyed to `ColorScheme`). SF Pro + SF Mono are system fonts — free (D5/D6). Tabular numerals → `.monospacedDigit()`; mono blocks → `.font(.system(.body, design: .monospaced))`. `@Environment(\.colorScheme)` drives the theme; Settings' Theme control writes an `@AppStorage` override.

### 13.9 Offline & connectivity
- The per-transfer `viz_export` JSON is the offline unit (self-contained ENU geometry). Cache it to disk on trip-plan (`FileManager`), plus static route images via `MKMapSnapshotter`. Section, per-level, 3D and AR all render from the cache with no signal.

**What needs a connection, and how we minimise it:**

| Surface | Needs network? | Minimisation |
|---|---|---|
| Station autocomplete | No | Bundle `stations.csv` (§13.2); network only for a fresher list. |
| Journey planning (`/journeys`) | **Yes** — realtime MOTIS/Transitous | Cache each plan result; reopening a planned trip is offline. |
| Walk views (section/per-level/3D/AR) | No | Prefetch every transfer's `viz_export` at plan time. |
| Boarding & step-off | No | Computed server-side, delivered in the plan payload. |
| Step-free re-route | No, if prefetched | Fetch the `--no-elevators` variant alongside the default (one boolean ⇒ 2× exports). |
| Verdict (initial + recompute) | No | `Verdict.rolledUp()` runs on cached/live data locally. |
| Live re-assessment (delays) | **Yes** — realtime | Fails soft: `LiveMonitor` keeps the last verdicts (`api/live.py`); the trip stays usable, just not fresh. |

**Net:** once a trip is planned with signal, the *entire experience for that trip* — verdict spine, all four walk views, boarding — works in airplane mode. Only *new* planning and *fresh* delays need the network, and both degrade gracefully.

**Walk delivery — built (2026-07-15).** `/journeys` stays lean; the drawable geometry is fetched separately and cached:
- **`GET /walk?relation_id=&from_platform=&to_platform=&step_free=`** → one transfer's `viz_export` document (the `WalkResult` envelope), `Cache-Control: public, max-age=86400` (deterministic given the DB).
- **`POST /walks {keys:[…]}`** → the same for a whole journey's transfers in one round trip (the prefetch path), capped at `MAX_WALKS_BATCH`. One bad key fails only itself (`ok:false` + reason).
- A walk is keyed by the exact triple a `Transfer` already carries (`relation_id`, `arrival_platform`, `departure_platform`), so `WalkKey(transfer:)` builds it directly (nil when the transfer never resolved a walk). The default walk uses the *same* pathfinder settings as the verdict, so its time equals `Transfer.walk_time_s`; `step_free` deliberately reroutes and may differ. All in `api/walks.py` + `ios/TransfrCore` (`WalkResult.export: VizExport?`), fixture-tested both sides.

### 13.10 Design piece → framework

| Prototype piece | Swift / iOS |
|---|---|
| Screen router | `NavigationStack` |
| Transfer carousel | `TabView(.page)` |
| Section + per-level views | SwiftUI `Canvas` + `Path` |
| Rotatable 3D | `WKWebView` (now) → SceneKit (native) |
| AR | ARKit + RealityKit (`RealityView`) |
| Verdict system | `enum` + worst-wins `reduce` |
| Times / platforms | `.monospacedDigit()`, SF Mono |
| Theme tokens | Asset Catalog colour sets |
| Live countdown | ActivityKit + WidgetKit |
| Position / map | CoreLocation + MapKit |
| `viz_export` JSON | one `Codable` → 4 renderers |
| API | `URLSession` async/await + `Codable` |

### 13.11 Testing — anchor Swift to the Python engine's own outputs
- **Structure:** keep models/decoding/verdict logic in the `TransfrCore` SwiftPM package (done) so it tests in seconds without a simulator; **Swift Testing** (`@Test`) for logic, XCTest for UI later.
- **The valuable move — golden fixtures generated by the *same* contracts the server serves.** `ios/TransfrCore/Tests/generate_fixtures.py` builds the `/journeys`, `/stations`, `/transfer` goldens straight from the `api/schemas.py` **pydantic** models, and copies a real `core/viz_export.py` output (Berlin Hbf 1→16). The Swift suite decodes those and asserts the worst-wins rollup. So a server-side contract change fails a Swift decode *immediately* — the two can't silently diverge. This is why "the Python might change" is a non-issue: churn lives behind a fixture-guarded JSON seam.
- **Run:** `xcodebuild test -scheme TransfrCore -destination 'platform=iOS Simulator,name=iPhone 16'` from `ios/TransfrCore`. (`swift test` currently trips over Homebrew headers in `/usr/local/include` on this machine — see `ios/README.md`.) Regenerate goldens with `.venv/bin/python ios/TransfrCore/Tests/generate_fixtures.py`.

### 13.12 Rebuild vs. incremental
The contract-first split (§13.2) is precisely what removes the teardown risk. **Algorithm changes** are absorbed server-side behind the JSON contract — zero Swift change. **Design/IA changes** are per-view SwiftUI rewrites, cheap and local. The only thing that would justify a real teardown is a fundamental navigation rethink — and `NavigationStack` value-routing lets even that be swapped destination-by-destination. Build thin now and you're never cornered; the trap to avoid is the opposite (porting the pathfinder into Swift, then having it churn under you).

---

## Appendix: design tokens

CSS custom properties from `design/prototype.html`. Both themes are first-class.

### Light
```
--bg:#E4E9F1  --bg-2:#D3DAE6  --paper:#F6F8FC
--panel:#FFFFFF  --panel-2:#EFF3F9  --panel-3:#E7ECF4
--ink:#0E1626  --ink-2:#55607A  --ink-3:#8A93A6
--line:rgba(14,22,38,.10)  --line-2:rgba(14,22,38,.06)
--accent:#0A63F0   (path / brand)
--go:#0FA968  --tight:#C9820A  --miss:#E0402F  --nodata:#6B7688   (verdicts)
--stair:#8B5CF6  --esc:#0EA5A5  --elev:#E0663A   (connector kinds)
```

### Dark
```
--bg:#05070D  --paper:#0A0E17
--panel:#131A28  --panel-2:#1A2233  --panel-3:#222C40
--ink:#EAF0FA  --ink-2:#98A4BC  --ink-3:#616C86
--line:rgba(255,255,255,.10)  --line-2:rgba(255,255,255,.055)
--accent:#4EA6FF
--go:#2FD39A  --tight:#F5B740  --miss:#FF6A5E  --nodata:#8894AB
--stair:#A78BFA  --esc:#2DD4BF  --elev:#FB8A5C
```

### Type
```
--sans: ui-sans-serif, -apple-system, "SF Pro Display", "SF Pro Text", "Helvetica Neue", system-ui, sans-serif
--mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, "Cascadia Code", monospace
```

### Semantic mapping
- `--accent` → the route line, 3D/AR path, active state, primary action, sector-highlight.
- `--go/--tight/--miss/--nodata` → verdict pills, rings, per-transfer nodes. Never used for the path.
- `--stair/--esc/--elev` → 3D risers, connector legend, level icons.
