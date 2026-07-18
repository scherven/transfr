# 3D connector language — stairs / escalator / lift

Status: **shipped into iOS** on branch `claude/3d-visualizer-berlin-compare-71c8c7`
(2026-07-17). This note is the handoff for further tweaks — every visual knob below
says exactly where it lives, so feedback like "faster lift" or "sparser chevrons" maps
to one line.

All of it lives in the 3D renderer `IsoGeometryCanvas` in
`ios/TransfrApp/Sources/TransfrUI/Screens/WalkGeometryViews.swift`, plus the soft
colour tokens in `ios/TransfrApp/Sources/TransfrUI/Theme/Theme.swift`. The 2D
**Section** and **Levels** tabs are unchanged (see Open questions).

## What it is

Two things layered together, driven by real `viz_export` geometry:

1. **"Recessed" base** — the blue route is the hero (heavier + a soft glow); the
   stair/escalator/lift risers are softened ~18 % toward the panel, drawn thinner, and
   backed by a paper casing so crossings stay legible. Chosen by the user over
   *Current* (loud), *Tinted* (very muted), and *Monoline* (neutral).
2. **Iconographic + animated glyphs** — each connector is drawn as the *shape* of the
   real thing, and escalators/lifts *move*:
   - **Stairs** — a stepped silhouette (tread, riser, tread…). No motion.
   - **Escalator** — a faint band with chevrons that ride steadily from the low end to
     the high end.
   - **Lift** — a masked shaft with side rails and landing arrows, and a car that eases
     up and down.

Prototypes (private Artifacts) the port matches 1:1:
- Treatment comparison (Current / **Recessed** / Tinted / Monoline):
  <https://claude.ai/code/artifact/bd736402-95c6-4482-8d9b-a417f1329649>
- Iconographic + animated on the Recessed base:
  <https://claude.ai/code/artifact/a0474633-9a1c-44f6-b401-731db30efceb>

## Tuning knobs

Everything is in `WalkGeometryViews.swift` unless noted. Search the symbol.

| Want to change | Knob | Where |
| --- | --- | --- |
| How muted the connectors are | `mix(base, panel, 0.18)` → raise/lower `0.18` | `Theme.swift` `stairSoft` / `escSoft` / `elevSoft` |
| Route weight / glow | `lineWidth: 4.5`, `.shadow(color: Theme.accent.opacity(0.55), radius: 4)` | `draw(…)` "Recessed treatment" |
| Context vs. on-route emphasis | `let alpha = (isRiser ? 1.0 : 0.62) * alphaMul` | `connectorSegment(…)` |
| **Escalator chevron density** | `connChevronGap: CGFloat = 18` (bigger = fewer) | top of glyphs section |
| **Escalator speed** | `connChevronSpeed: Double = 15` (px/s) | top of glyphs section |
| Chevron size / weight | `size: 4.4`, `1.7` | `glyphEscalator` |
| **Lift speed** | `liftCyclePeriod: Double = 5.6` (s, full up+down) | top of glyphs section |
| Lift shaft width / car size | `r = big ? 4.4 : 3.2`, `ch: CGFloat = big ? 9 : 6.5` | `glyphLift` |
| Lift static resting position | `0.62` (0 = bottom, 1 = top) | `glyphLift`, `let t = motion ? … : 0.62` |
| Stair step count | `max(3, min(7, Int((len / 8).rounded())))` | `glyphStairs` |
| Riser vs. context line weights | `w: isRiser ? 3 : 1.9` (stairs), `2.4 : 1.7` (esc) | `connectorSegment` dispatch |
| Casing strength | the `Theme.paper.opacity(0.8–0.92)` + `w + 3` strokes | each `glyph*` |

## Motion

Driven by `TimelineView(.animation)` (same idiom as `RouteMap` / `LaunchView`). Two
gates fall back to a **static** frame (chevrons evenly spaced, car parked at 0.62):

- **System** `reduceMotion` (`@Environment(\.accessibilityReduceMotion)`).
- **`animated:` init param** on `IsoGeometryCanvas` (default `true`; the headless
  snapshot test passes `false`). NB `accessibilityReduceMotion` is read-only — it
  cannot be forced through `.environment(\.…, true)`; that's why the flag exists.

Motion also auto-pauses off-screen (TimelineView) — no battery tax.

## Framing (the reason it doesn't look like a vertical stick anymore)

`IsoFit` frames the 3D on `WalkScene.walkFramingBox` — the path's XY bbox grown by
`max(w,h)*0.22 + 18 m` — not the whole-station box. A short walk in a vast station
(Berlin 1→16 is ~50×52 m in a ~510×806 m station) used to collapse to ~5 % of canvas
width; it's now ~25 %. Browse (station-map) mode and pathless walks still frame the
whole station (`worldBounds`). Mirrors `PlanFit.forLevel` (issue #53).

## Preview / verify without the Simulator UI

`WalkSceneTests.rendersAllThreeCanvases` rasterises `IsoGeometryCanvas` via
`ImageRenderer`. Dump the PNGs by passing a render dir to xcodebuild:

```
cd ios/TransfrApp
TEST_RUNNER_WALK_RENDER_DIR=/tmp/renders xcodebuild -scheme TransfrApp \
  -destination 'platform=iOS Simulator,id=<booted-udid>' \
  -only-testing:TransfrUITests test
# → /tmp/renders/walkrender_iso_*.png   (also section_, level*_)
```

Fixture note: `viz_berlin_1_16.json` (in `TransfrUITests/Fixtures/`) routes 1→16 up a
**4-escalator** chain — no lift. The **lift** glyph is only exercised by
`viz_berlin_1_16_details.json` (escalator + elevator), which currently lives in
`TransfrCoreTests/Fixtures/`. Copy it into the UI test bundle to render it. (Plain
`swift test` can't build the iOS-only UI target — use the `xcodebuild` line above.)

## Open questions / candidate tweaks

- **Section & Levels tabs** still draw plain straight risers — the chevron/car language
  is 3D-specific and doesn't map cleanly onto an elevation profile or a top-down plan.
  Decide whether to harmonise (e.g. chevrons along the Section riser) or leave them.
- **Motion only on the route** — today every escalator/lift in view animates (calm at
  the current density, busier in browse/station-map). Could restrict motion to the
  route's own connectors and draw context ones static.
- **Platform slabs / walkways** kept at the existing iOS weights (not the prototype's
  slightly chunkier slabs); revisit if the balance feels off in-app.
- Live data drives the glyphs: whatever transition *kind* the export carries renders as
  that kind, so in-app Berlin 1→16 may show a lift (per the original screenshot) even
  though the test fixture is all-escalator.
