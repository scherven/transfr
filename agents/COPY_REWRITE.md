# Copy rewrite worksheet (issue #23 follow-up)

User-facing strings flagged as flowery / AI-sounding — internal-system names, em
dashes, or marketing tone. **None of these are changed in code yet.** Rewrite each
in its `Rewrite:` slot, then hand back for a code pass.

Guidelines (from #23): terse, plain, no hyphens, no em dashes, no internal-system
names (e.g. `core/`, `viz_export`, `pathfinder`, DB or script names). Keep the
meaning intact.

Line numbers are current as of this commit. Preserve any `\(…)` Swift string
interpolations exactly.

---

## 8A — internal-system jargon

### 8A-1 · `ios/TransfrApp/Sources/TransfrUI/Screens/AttributionsView.swift:32`
Current:
> Regions are extracted offline from the OSM planet with osmium; routing runs on the local core/ pathfinder. No map tiles are fetched at run time — an installed region is fully self-contained.

Rewrite:

_(prior suggestion: "Regions are prepared offline from OpenStreetMap data, and routing runs on the device. No map tiles are fetched while you use the app, so an installed region works entirely on its own.")_
_(note: keep the "OpenStreetMap" credit — it's a required ODbL attribution, not an internal system. Only `osmium` and `core/` are the internal-tool references.)_

### 8A-2 · `ios/TransfrApp/Sources/TransfrUI/Screens/AdvancedView.swift:26`
Current:
> Everything here runs on the same viz_export / pathfinder that powers a transfer walk — just pointed at a different question. No verdicts, no train.

Rewrite:

_(prior suggestion: "These tools use the same station maps and routing as a transfer walk, just aimed at a different question. No verdicts, no train.")_

### 8A-3 · `ios/TransfrApp/Sources/TransfrUI/Screens/WalkView.swift:113`
Current:
> Exploded floors, drawn from the walk's real geometry — the same viz_export the Section and Levels tabs project.

Rewrite:

_(prior suggestion: "Exploded floors, drawn from the walk's real geometry, the same data the Section and Levels tabs show." — "Section" and "Levels" are on-screen tab names, safe to keep.)_

### 8A-4 · `ios/TransfrApp/Sources/TransfrUI/Screens/OfflineRegionsView.swift:62`
Current:
> Regions are built offline from an OSM extract (extract_europe.sh) — no live API, so an installed region works fully on a plane.

Rewrite:

_(prior suggestion: "Regions are built offline from OpenStreetMap data, so an installed region works with no internet, even on a plane.")_

### 8A-5 · `ios/TransfrApp/Sources/TransfrUI/Screens/MapHealthView.swift:76`
Current:
> transfr_eu (71 / 5 / 24, 1,401 platforms) and transfr_kr (2 / 5 / 93, 899) are measured stitch_survey.py sweeps; JP is illustrative until its sweep finishes. Query a station to run the same connected / stitchable / island classification over its own platform pairs, live.

Rewrite:

_(prior suggestion: "The Europe and Korea figures are measured; Japan is illustrative until its survey finishes. Query a station to check its own platform pairs the same way, live.")_

### 8A-6 (FORK) · Vocabulary: `connected / stitchable / island`
This trio is domain jargon that repeats across Map Health. Deciding the wording once
lets it be applied consistently everywhere it shows.

Prior suggestion: **connected / bridgeable / isolated** (keep "connected"; `stitchable` → `bridgeable`, `island` → `isolated`).

Rewrite (the three terms):
- connected →
- stitchable →
- island →

Display occurrences in `MapHealthView.swift` (all user-facing):
- `:52`–`:54` — region-overview legend: `legendDot("connected")`, `legendDot("stitchable")`, `legendDot("island")`
- `:68`–`:70` — "All databases" legend: same three `legendDot(...)`
- `:76` — the info line (8A-5 above)
- `:165` — query placeholder: "…the same connected / stitchable / island split as the region sweep…"
- `:190`–`:191` — query-result legend: `legendDot("stitchable \(r.stitchable)")`, `legendDot("island \(r.island)")`
- `:196` — heading "DISCONNECTED PAIRS"

_(note: the same words also appear as non-display code — the `Health` struct fields (`:25`, `:30`, `:32`, `:34`), doc comments (`:5`, `:9`), and the API value comparison at `:214` (`kind == "stitchable"`). Those are internal and can stay as-is, or be renamed separately from the UI copy.)_

---

## 8B — em dash / marketing tone

### 8B-1 · `ios/TransfrApp/Sources/TransfrUI/Screens/InputView.swift:421`
Current (keep the `\(adaptedPlatforms.count)` and `\(lookupStation)` interpolations):
> \(adaptedPlatforms.count) platforms at \(lookupStation). Pick any two — we draw the walk between them, timed at your pace.

Rewrite:

_(prior suggestion: "\(adaptedPlatforms.count) platforms at \(lookupStation). Pick any two to see the walk between them and how long it takes." — note: "timed at your pace" refers to the Settings → Walking pace control; restore that nuance if you want it.)_

### 8B-2 · `ios/TransfrApp/Sources/TransfrUI/Screens/InputView.swift:427`
Current:
> Any two platforms at one station — we draw the walk between them, timed at your pace. Pick a station above to choose from its real platforms; names are free-form (5a, Gl 1).

Rewrite:

_(prior suggestion: "Pick any two platforms at one station to see the walk between them and how long it takes. Choose a station above to load its real platforms, or type any name (5a, Gl 1).")_

### 8B-3 · `ios/TransfrApp/Sources/TransfrUI/Screens/JourneyView.swift:265`
Current (the `.tight` transfer-card title):
> Tight — move promptly

Rewrite:

_(prior suggestion: "Tight, move promptly" — or just "Tight", to match the `Theme.swift` verdict label.)_

### 8B-4 · `ios/TransfrApp/Sources/TransfrUI/Screens/StationWalkView.swift:286`
Current:
> Tap a reachable platform to see the walk as a full 3D view. One pathfind per platform — the same walk cost the transfer verdict uses.

Rewrite:

_(prior suggestion: "Tap a reachable platform to see the walk as a full 3D view. Each platform is routed the same way a transfer walk is.")_
_(note: this string continues at `:288` with "Routed step-free (Stairs-free is on in Settings)…" — align that wording too if you rewrite this, and see 8C-1 re: "Stairs-free".)_

### 8B-5 · `ios/TransfrApp/Sources/TransfrUI/Screens/AdvancedView.swift:15`
Current (the "Walk to nearest…" row subtitle):
> Toilets, lifts, exits, tickets, coffee — routed

Rewrite:

_(prior suggestion: "Walk to toilets, lifts, exits, tickets, or coffee")_

---

## 8C — settings hyphens

### 8C-1 (FORK) · `ios/TransfrApp/Sources/TransfrUI/Screens/SettingsView.swift:17`
Current (the row title):
> Stairs-free routes

Rewrite:

_(prior suggestion: "Step-free routes" — matches the code's `stepFree` and the standard rail term, but keeps a hyphen. Strict no-hyphen alternative: "Routes without stairs". Ripple if you rename: the parenthetical "(Stairs-free is on in Settings)" in `StationWalkView.swift:288` should match.)_

### 8C-2 · `ios/TransfrApp/Sources/TransfrUI/Screens/SettingsView.swift:77`
Current (rendered text; code is `Text("\u{201C}Makeable\u{201D} cut-off")`):
> "Makeable" cut-off

Rewrite:

_(prior suggestion: "Makeable" cutoff — closes up "cut-off" to one word, meaning unchanged.)_
