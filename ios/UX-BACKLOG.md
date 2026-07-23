# iOS UX backlog — found, not fixed

Everything surfaced by the 2026-07-22 review that is **still open**. Items already
fixed on `claude/ios-app-ux-review-f46650` are not repeated here.

Sources: two cold reviewers (drove the app on simulators with no source access,
7 routes + 10 walks), plus a live-payload-vs-render-path audit.

Confidence markers:

- **[V]** verified in source or against the live API
- **[R]** single reviewer observation, not independently reproduced — **reproduce before spending time**

---

## 1. Needs a decision, not just a fix

### 1.1 Times render in the device's timezone, not the station's **[V]**

`Fmt.hhmm` sets a locale but no `timeZone`, so it falls back to `TimeZone.current`.
The wire carries UTC. No station-local timezone exists anywhere in either Swift
package — the only `timeZone` assignments are `.current`.

Measured from an EDT machine against the live API:

| Train | Wire (UTC) | App showed | Station clock (CEST) |
|---|---|---|---|
| ICE 18 | 03:10 | 23:10, 21 Jul | 05:10, 22 Jul |
| ICE 618 | 02:46 | 22:46, 21 Jul | 04:46, 22 Jul |

Wrong hour **and** wrong date. Self-consistent (ask for 08:34 local, get 08:34
local back), so it looks fine until checked against a ticket or departure board.

Not a one-line fix — the payload carries no station timezone. Options:

1. Derive it from the stop coordinates client-side (needs a tz lookup).
2. Add a timezone to `Place` / `Leg` server-side. Cleanest, but a contract change.
3. Hardcode CET. Wrong for UK/PT, and silently wrong, which is the current failure mode.

Bites whenever a European trip is planned from another continent, and on any
journey crossing a timezone.

### 1.2 Station names differ between the two walk doors **[V]**

Walk-only shows the name you searched (`Zürich HB`, `Düsseldorf Hbf`); the journey
door shows the name the transfer resolved to (`Sihlquai/HB`,
`Düsseldorf Hauptbahnhof`). Reviewer B: *"I would not be sure I was looking at the
same place."*

Host-level, so the `WalkDetail` unification deliberately left it. Suggested: the
searched name wins, with the resolved name as a subtitle when they differ.

### 1.3 "Best" is still positional

`isBest: idx == 0`. The fix pass stopped it claiming the badge before verdicts
resolve, but it still means "earliest", not "best" — it was awarded to a 34-minute
bus over a 16-minute train. Either rank on something defensible or rename the badge.

---

## 2. Server and engine

### 2.1 Frankfurt Hbf platform 1 → 13 returns a route that leaves the building **[V]**

Two platforms in the same train shed. Verified directly against the API:

```
walk_distance_m: 1173.9    walk_time_s: 872.3   (14m 32s)
path bbox 495 m × 706 m,  furthest point 517 m from the station origin
first point z = -12.0 with floor_height_m 4.0  →  level -3
```

Platform "1" resolves to an **underground** platform, not the main-line platform 1.
From there, 1.17 km and nine transitions is a correct answer to the wrong question.
Same family as the Köln renumbering issue: an ambiguous platform label inside one
relation.

The UI renders it confidently across all three tabs. It only surfaced because the
3D view leaked a street label (*"Platz der Republik"*, a tram stop hundreds of
metres away). **The highest-value open item in this document.**

### 2.2 The deployed API does not enforce authentication **[V]**

```
GET api.trans-fr.com/stations   no key            → HTTP 200
GET api.trans-fr.com/stations   deliberately wrong key → HTTP 200
```

Endpoints are declared protected; auth is not enforced. The service is queryable
by anyone who knows the hostname. Needs a careful change plus a deploy — breaking
auth on a live service the app depends on deserves its own pass.

### 2.3 `walk_time_s: 0.0` for platforms that are metres apart **[V]**

Utrecht Centraal 5→7, coordinates ~13 m apart, returns `walk_time_s: 0.0` and
`walk_distance_m: 0.0`, and the verdict `feasible` is computed from that zero.
The client no longer fabricates a spare figure from it, but the server side is
unresolved: is this a genuine cross-platform change reported with zero distance,
or an unresolved walk defaulting to zero? They need different answers.

### 2.4 Walking pace has nowhere to go **[V]**

`SettingsStore.pace` is declared, persisted and bound to a control, and read by
nothing. `/journeys` has **no pace parameter**, so the setting cannot work without
a server change. Implied pace across measured walks is 1.26–1.40 m/s — a brisk,
unencumbered adult, with no way to say otherwise.

### 2.5 Station name matching is brittle **[V]**

The spellings printed on tickets and platform signs are the ones that fail:

| Typed | Result |
|---|---|
| `Frankfurt(Main)Hbf` | 404 — only `Frankfurt (Main) Hbf` resolves |
| `Amsterdam Centraal` | no match — the index has `Amsterdam-Centraal` |
| `Paris Nord` | no match — requires `Paris Gare du Nord` |

Umlauts dropped by an input method also produce silence. Whitespace/punctuation-
insensitive matching, or a "did you mean", would fix all of these.

---

## 3. Client defects

### 3.1 Turn-by-turn contains no walking **[R]**

Berlin Hbf 1→16 lists an escalator and a lift; the horizontal leg that is most of
the 107 m is not a step. Frankfurt 1→13 lists **seven vertical hops and zero
horizontal instructions** for 1174 m.

### 3.2 Turn-by-turn emits self-contradictory steps **[R]**

At Frankfurt: *"Take the stairs down to L0"* while standing on L0, immediately
followed by *"Take the stairs up to L0"*. May be downstream of 2.1 — recheck once
the platform resolution is fixed.

### 3.3 The 3D level axis does not track the model **[R]**

Verified by the reviewer through rotation: the geometry moves, the axis labels stay
put. After a rotate, a lift the app says runs L−2 → L+2 visually spans L−2 to about
L0. The axis measures nothing. One-finger drag also pans unboundedly — both
reviewers dragged the model off-canvas and needed the reset button.

### 3.4 The Levels tab rescales silently per floor **[R]**

Scale bars of 10 m, 25 m, 200 m and 400 m within single walks. Two paths drawn the
same length can differ by 40×. Makes effort unreadable across levels, and defeats
the tab's main advantage (it is the only view with a scale bar).

### 3.5 Legends do not match the drawing, in both directions **[R]**

- Purple "Stairs" listed where none is drawn; purple "Connector" never appears on real-data walks.
- Teal escalator drawn in 3D and not listed.
- **Grey — the unmapped level change — is drawn everywhere and appears in no legend.** It is the most safety-relevant colour on the screen and the only undefined one.
- At Frankfurt L−3 the route *itself* renders as a dashed grey line, in no legend.
- "Lift" (3D legend) vs "Elevator" (Section legend) vs "Take the lift" (step) — three terms, one thing.

### 3.6 Units mix within one screen **[R]**

With distance set to Imperial, the header reads `352 ft` while the Levels scale bar
reads `10 m`.

### 3.7 A lift is never rendered **[R]**

Across ten walks between both reviewers, no lift element was ever drawn, despite
"Elevator"/"Lift" appearing in both legends. Where the app routed up two flights it
offered no step-free alternative and never said whether a lift exists.

### 3.8 Boarding guidance names the routing ref **[V]**

`BoardingViews` uses `BoardingGuidance.departurePlatform` in five strings. That is
the server's routing ref, so at a renumbered station like Köln the boarding cue can
contradict the platform pill beside it. Different type from `Transfer`, so it fell
outside the accessor sweep — it needs the same treatment.

### 3.9 `WalkView` cannot tell it is on the offline sample tier **[V]**

It shows the generic *"this station isn't mapped in enough detail"* box there,
blaming the map for our own bundled data. `WalkLookupView` keys on
`relationId == 0`; the transfer door would need the fetch to surface
`WalkResult.reason == "sample_no_geometry"`.

### 3.10 "Working out the best door…" has no timeout **[R]**

`BoardingCard`'s else-branch is an unbounded spinner — no timeout, no failure copy.
Observed running 40+ seconds on two transfers and never resolving.
*"Classifying Köln Hbf's platform pairs…"* behaves the same way.

### 3.11 Boarding copy compares a thing to itself **[R]**

*"Board toward **the far end** — stepping off there saves up to ~4 min over **the
far end**."* The marker also sits at the near end.

### 3.12 Header and timeline disagree **[V]**

`JourneyView` filters out walking legs but the header uses `legs.first.departure`.
Header 00:22 vs first timeline row 00:30; the 514 m / 8-minute walk explaining the
gap is never drawn. **Expected to be resolved by the interleaved-card redesign**,
where a walk is simply another leg row — leaving it until that lands.

### 3.13 Raw internal identifiers reach the user **[R]**

- `Pl 13030 → 13030` in the results list and journey detail.
- OSM refs as labels: `1;1a`, `102`.
- Junk platform picker entries: `Platform DB`, `Platform S`, `Platform W1` — with `W1` as the **default** selection.
- Köln's picker omits platforms 4, 7, 8, 9 and 10 while exposing `83–91` and `A–F`.
- Dev string surfaced: *"Rotatable floors render once this walk's geometry loads from `/walk`"*.

### 3.14 Walk-only silently rewrites typed platforms **[R]**

Typed `16` became `77`; `4` and `9` became `1` and `F`. No message.

### 3.15 Form and error-state issues **[R]**

- Station fields have no clear (×) button. Both reviewers restarted the app rather than delete two names by hand — A five times, B four.
- The failed-search banner persists across the Plan / Paste link / Walk only tabs and through navigation, on screens with no stations.
- Autocapitalisation is on in a strict-match field (`asdfgh` → `Asdfgh`).
- Autocomplete produces no suggestions *and* no "no matches" on a miss.
- Changing the station leaves the previous station's platform numbers in the pickers.
- The departure sheet clips its own content at the first detent; `Done` needs two taps with the wheel open; the pill shows 24-hour while the wheel shows 12-hour.

### 3.16 Map and layout **[R]**

- Route-map annotations do not scale with Dynamic Type (~6 pt at any size) and overlap the START pin. One map attached a Köln transfer label to the Frankfurt origin pin.
- No zoom-to-fit: on a short hop both pins collapse into one blob.
- Level chips on "Nearest facility" are covered by the floating "Tap a pin for details" pill — unreachable at any type size.
- Axis label clipped to `Le` / `L+` / `L−`.
- 3D platform labels collide (two 18s, two 4s, two 5s); "Platforms not connected" prints through the model.
- An empty chart box shows "Platforms not connected" with the colour legend still drawn beneath it.
- A blue checkmark renders on failure: *"✓ Board on Platform 43/44"* beside "these platforms aren't connected on the map".

### 3.17 Accessibility, at accessibility-extra-large **[R]**

Main flows reflow well — no truncated station names in the results list or
connection detail. But:

- Shield and gear icons collide on the home screen.
- The "Back" label disappears, leaving a bare chevron.
- The last results card runs under the home indicator.
- The `Timetable: 91 → 93` chip wraps mid-arrow.
- The app does not re-lay-out when type size changes while running.

### 3.18 Smaller **[R]**

- Two `ICE 122` rows at 09:30 and 09:33 with different durations — possible duplicate data, unconfirmed.
- `CarouselView.layoverLine` does `Fmt.duration(Int(transfer.layoverS ?? 0))` — harmless today (0 lands on `Fmt`'s own "—") but the same shape as the ring bug already fixed.
- Three separate ad-hoc `HH:mm` `DateFormatter`s: `ResultsView.settledSubtitle`, `InputView.departLabel`, and the private `Fmt.hhmm`. Consolidating them is also step one of fixing 1.1.
- Pre-existing warning: `Formatting.swift:9`, `nonisolated(unsafe)` unnecessary on a `Sendable` `DateFormatter`.
- `ios/README.md` points at a repo-root `TODO.md` that does not exist.
- `ios/README.md` still describes carousel boarding guidance as "illustrative". It is real — derived geometry, sector letters deliberately withheld, with an explicit footnote that coach numbers need a live formation feed.

---

## 4. Design, not defects

Recorded because both reviewers reached them independently, but each is a product
decision rather than something to fix.

- **No orientation cue on any walk view.** No compass, no device heading, no landmarks, no entrance names, no direction of travel. Both reviewers named this the single biggest reason the walk views are not usable while actually walking, and both proposed the same fix — a heading arrow on the Levels plan, using data the phone already has.
- **The app never says what it is for.** Reviewer A took twenty minutes and three screens on a second route to discover that it tells you whether you can make your platform change. The front door says "Find connections", the same as every other planner.
- **The turn-by-turn list is what both reviewers actually read**, and it is not a tab — it sits below all three. Reviewer B: *"The tabs are decoration on top of the thing that does the work."*
- **Per-tab verdicts.** Levels: keep (unanimous — only geographically true view, only one with a scale bar). 3D: cut or demote (unanimous — neither would open it with a train to catch; its one real use was exposing the Frankfurt bug, which argues for a debug flag). Section: split — A called it "a picture of a sentence I already read", B called it essential for seeing a multi-level yo-yo as a shape. Both are right about different walks.
- **The turn-by-turn list is inert.** Tapping a step does nothing — no link to the drawing, no level jump, no highlight.
- **A per-level tab set cannot express a route that revisits a floor.** Zürich 34→6 runs L−4 → L−1 → L−4 → L0 → L−2 → L0; the chips are sorted by level, so revisits are structurally inexpressible. The chips also run in reverse walking order.
- **No earlier/later trains and no arrive-by search.** The list stops at five with no paging.
