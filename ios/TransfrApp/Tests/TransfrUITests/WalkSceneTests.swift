import Foundation
import SwiftUI
import Testing
@testable import TransfrCore
@testable import TransfrUI

/// Exercises the keystone: real `core/viz_export.py` output → `WalkScene` → the
/// three drawable canvases. Decodes committed goldens (Berlin Hbf 1→16, the
/// 9-level reference case, and Dortmund Hbf 11→4) and both (a) asserts the
/// derivations the renderers depend on and (b) rasterises each canvas so the
/// projection actually runs. Rendered PNGs are written to the temp dir and their
/// paths printed (grep `RENDER_PNG:`), so the drawing can be eyeballed too.
@MainActor
struct WalkSceneTests {

    static func scene(_ name: String) throws -> WalkScene {
        let url = try #require(
            Bundle.module.url(forResource: "Fixtures/\(name)", withExtension: "json"),
            "missing fixture \(name).json"
        )
        let export = try TransfrJSON.decode(VizExport.self, from: try Data(contentsOf: url))
        return WalkScene(export)
    }

    // MARK: - Derivations the renderers read

    @Test func berlinSceneDerivations() throws {
        let s = try Self.scene("viz_berlin_1_16")

        #expect(s.found)
        #expect(s.startRef == "1")
        #expect(s.endRef == "16")
        // levels_present carries fractional mezzanines (-1.7, -0.5, …); the scene
        // collapses them to the nearest integer floor for the picker/plan.
        #expect(s.levelsAsc.contains(-2))
        #expect(s.levelsAsc.contains(2))
        #expect(s.levelsAsc.allSatisfy { $0 >= -2 && $0 <= 2 })
        // The path really changes floors here.
        #expect(s.pathLevels.count > 1)

        // Turn-by-turn = step-off + one row per transition + board.
        let steps = s.turnByTurn()
        #expect(steps.count == s.transitions.count + 2)
        #expect(steps.first?.title.contains("Platform 1") == true)
        #expect(steps.last?.title.contains("Platform 16") == true)
        // Every step has copy (no blank rows).
        #expect(steps.allSatisfy { !$0.title.isEmpty && !$0.sub.isEmpty })
    }

    @Test func dortmundSceneDerivations() throws {
        let s = try Self.scene("viz_dortmund_11_4")
        #expect(s.found)
        #expect(s.startRef == "11")
        #expect(s.endRef == "4")
        #expect(s.startLevel == 0)   // both platforms at street/track level
        #expect(s.endLevel == 0)
        #expect(s.pathLevels.min()! < 0)   // dips through the underpass
        #expect(s.turnByTurn().count >= 3)
    }

    // MARK: - The step-free claim must agree with the geometry it sits over

    /// A synthetic walk whose path runs along +x at the given heights (metres), with
    /// NO transitions — the shape that exposes the disagreement between the drawing
    /// and the banner.
    static func straightWalk(z: [Float]) -> WalkScene {
        let pts = z.enumerated().map { Point3(x: Float($0.offset) * 10, y: 0, z: $0.element) }
        let meta = VizExport.Meta(
            relationId: 1, ref1: "1", ref2: "2", algorithm: "test", contextMode: "test",
            stitched: false, nStitches: 0, floorHeightM: 4, zIsLevelNotElevation: true,
            originLat: 52, originLon: 13, levelsPresent: [], nContextWays: 0,
            hasDetails: false, detailRadiusM: 0, nDetails: 0)
        let path = VizExport.Path(found: true, points: pts, transitions: [],
                                  walkingTimeSeconds: 60, walkingDistanceMeters: 80)
        return WalkScene(VizExport(meta: meta, ways: [], path: path, details: []))
    }

    /// "One level, step-free. Walk straight across — no stairs." may only be said when
    /// the **geometry** is flat too, not merely when `transitions` is empty.
    ///
    /// The Section renderer derives its risers from the path polyline and only looks
    /// the *kind* up in `transitions`, falling back to `vertical`. So a level change
    /// the geometry contains but `transitions` doesn't describe is drawn as a grey
    /// riser while `transitions.isEmpty` still reads true — a green step-free claim
    /// directly above a picture of the path changing floors.
    @Test func stepFreeClaimRequiresFlatGeometryNotJustEmptyTransitions() throws {
        // Genuinely flat: nothing describes a change, and nothing is drawn.
        let flat = Self.straightWalk(z: [0, 0, 0])
        #expect(flat.transitions.isEmpty)
        #expect(!flat.pathChangesLevel, "a flat path is the one honest step-free case")

        // The bug's shape: the path drops a floor, `transitions` says nothing.
        let undescribed = Self.straightWalk(z: [0, 0, -4, -4])
        #expect(undescribed.transitions.isEmpty, "nothing describes the change…")
        #expect(undescribed.pathChangesLevel, "…but the drawn path changes level")

        // The threshold is the renderer's own, so the copy and the drawing agree on
        // what counts: a sub-threshold wobble is not a level change, just over is.
        #expect(!Self.straightWalk(z: [0, 0.39, 0]).pathChangesLevel)
        #expect(Self.straightWalk(z: [0, 0.41, 0]).pathChangesLevel)

        // Real fixtures whose paths really do change floors report it.
        #expect(try Self.scene("viz_berlin_1_16").pathChangesLevel)
        #expect(try Self.scene("viz_dortmund_11_4").pathChangesLevel)
    }

    // MARK: - Issue #53: the level view's framing, picker and honesty

    /// The picker must offer exactly the floors the path visits — no blank tabs.
    ///
    /// `levelsAsc` is the union of levels over every *context* way the search
    /// touched, so on Dortmund 11→4 it tabs L−2: the walk never goes there, and the
    /// canvas (which draws the path) had nothing of *your route* to show. Both
    /// pickers now read `pathLevels`.
    @Test func pickerOffersOnlyFloorsThePathVisits() throws {
        let dortmund = try Self.scene("viz_dortmund_11_4")
        // The bug, pinned: the old source really does offer a floor the path misses.
        #expect(dortmund.levelsAsc.contains(-2))
        #expect(dortmund.pathLevels == [-1, 0, 1])
        #expect(!dortmund.pathLevels.contains(-2), "L−2 would render a routeless panel")
        // Every floor the picker offers has route geometry to draw.
        for lvl in dortmund.pathLevels {
            #expect(dortmund.routeBounds(level: lvl) != nil, "L\(lvl) has no route to frame")
        }
        #expect(dortmund.routeBounds(level: -2) == nil)

        // Berlin's picker was already honest (levels_present == the path's floors);
        // it must stay that way.
        let berlin = try Self.scene("viz_berlin_1_16")
        #expect(berlin.pathLevels == [-2, -1, 0, 1, 2])
        for lvl in berlin.pathLevels { #expect(berlin.routeBounds(level: lvl) != nil) }
    }

    /// The "Level changes" stat is a COUNT of crossings, not the net delta.
    ///
    /// Both walk doors used to report `end − start`, which reads a route that drops to
    /// an underpass and climbs back out as no level change at all: Dortmund 11→4
    /// crosses four floors and nets 0. The facility fixture is the mirror image — two
    /// crossings, net −4. The count is read off the path's own level sequence (the
    /// geometry the section strokes its risers from), so it also sees a change that
    /// `transitions` never described.
    @Test func levelChangesCountsCrossingsNotTheNetDelta() throws {
        let dortmund = try Self.scene("viz_dortmund_11_4")
        #expect(dortmund.endLevel - dortmund.startLevel == 0, "the net delta is the bug")
        #expect(dortmund.levelChangeCount == 4)

        let facility = try Self.scene("viz_berlin_facility")
        #expect(facility.endLevel - facility.startLevel == -4)
        #expect(facility.levelChangeCount == 2)

        // Where the map classified every change, the count agrees with the list the
        // turn-by-turn prints — the stat and the steps can never contradict.
        for name in ["viz_berlin_1_16", "viz_dortmund_11_4", "viz_essen_10_6"] {
            let s = try Self.scene(name)
            #expect(s.levelChangeCount == s.transitions.count, "\(name)")
        }

        // A flat walk changes level zero times. An undescribed drop still counts:
        // the section draws that riser, so a stat of 0 beside it would be the same
        // contradiction the step-free banner used to make.
        #expect(Self.straightWalk(z: [0, 0, 0]).levelChangeCount == 0)
        let undescribed = Self.straightWalk(z: [0, 0, -4, -4])
        #expect(undescribed.transitions.isEmpty)
        #expect(undescribed.levelChangeCount == 1)
    }

    /// Each floor is framed on its OWN route, not the whole scene's box.
    ///
    /// The scene box spans every context way — 510 × 814 m on Berlin — so framing
    /// every floor on it drew each route at well under 1% of the canvas. Own-framing
    /// must put a real fraction of the canvas under every floor's route.
    @Test func eachLevelIsFramedOnItsOwnRoute() throws {
        for name in ["viz_berlin_1_16", "viz_dortmund_11_4"] {
            let s = try Self.scene(name)
            let scene = s.worldBounds
            for lvl in s.pathLevels {
                let route = try #require(s.routeBounds(level: lvl))
                // The floor's own box is a strict subset of the scene's, and on these
                // fixtures a *much* smaller one — that gap is the reclaimed canvas.
                #expect(route.width <= scene.width && route.height <= scene.height)
                // The framing is per-level, so two floors must not share a frame.
                for other in s.pathLevels where other != lvl {
                    let b = try #require(s.routeBounds(level: other))
                    #expect(route != b, "L\(lvl) and L\(other) share a frame")
                }
            }
        }
    }

    /// An unclassified `vertical` change must not draw as a lift.
    ///
    /// `WalkConnector.color`'s default arm returned `Theme.elev` — the exact orange
    /// the legend spends on "Lift" — so Dortmund's three `vertical` transitions drew
    /// as three elevators that don't exist. It's a gap, so it gets `Theme.nodata`.
    @Test func unclassifiedLevelChangeIsNotAnElevator() throws {
        #expect(WalkConnector.color("vertical") != Theme.elev)
        #expect(WalkConnector.color("vertical") == Theme.nodata)
        #expect(!WalkConnector.isMapped("vertical"))
        // The kinds the map *does* record keep their own colours.
        #expect(WalkConnector.color("elevator") == Theme.elev)
        #expect(WalkConnector.color("lift") == Theme.elev)
        #expect(WalkConnector.color("stairs") == Theme.stair)
        #expect(WalkConnector.color("escalator") == Theme.esc)
        #expect(WalkConnector.isMapped("stairs") && WalkConnector.isMapped("escalator"))

        // Dortmund is the case that shipped wrong: 3 of its 4 changes are `vertical`.
        let d = try Self.scene("viz_dortmund_11_4")
        let unmapped = d.transitions.filter { !WalkConnector.isMapped($0.kind) }
        #expect(unmapped.count == 3)
        #expect(unmapped.allSatisfy { WalkConnector.color($0.kind) == Theme.nodata })
    }

    /// Essen Hbf 10→6 is the "up-and-over" case (climb to the L+2 footbridge, ride an
    /// escalator back down): the fixture that pins the rendering fixes.
    ///
    /// - Escalator direction: the descending leg (`to.z < from.z`) must read "…down
    ///   to L0", not "up".
    /// - Mechanism attribution (core/viz_export.connector_kind_near): the climb is a
    ///   graph seam at a bare, untagged node, but a mapped escalator connecting L0↔L2
    ///   sits a few metres away, so the change is named "escalator" — NOT the honest
    ///   fallback "vertical". Both legs end up escalators; no step says "not mapped".
    @Test func essenUpAndOverAttributesBothEscalators() throws {
        let s = try Self.scene("viz_essen_10_6")
        #expect(s.found)
        #expect(s.startRef == "10" && s.endRef == "6")

        let up = try #require(s.transitions.first { $0.to.z > $0.from.z })
        let down = try #require(s.transitions.first { $0.to.z < $0.from.z })
        #expect(up.kind == "escalator")     // attributed from the mapped escalator nearby
        #expect(down.kind == "escalator")

        let steps = s.turnByTurn()
        #expect(steps.contains { $0.title == "Ride the escalator up to L+2" })
        #expect(steps.contains { $0.title == "Ride the escalator down to L0" })   // not "up"
        // Attribution succeeded, so no row falls back to the unmapped wording.
        #expect(steps.allSatisfy { !$0.sub.contains("not mapped") })
    }

    /// The honest fallback still stands for a GENUINE gap: a `vertical` transition
    /// with no mechanism to attribute names a direction but no mechanism, and its
    /// subtitle states the gap (never claims a stair or lift the data can't back).
    @Test func unmappedLevelChangeReadsHonestly() throws {
        #expect(WalkConnector.instruction("vertical", up: true, to: "L+2") == "Go up to L+2")
        #expect(WalkConnector.instruction("vertical", up: false, to: "L0") == "Go down to L0")
        #expect(WalkConnector.subtitle("vertical") == "Level change not mapped — follow station signs")
        #expect(!WalkConnector.subtitle("vertical").lowercased().contains("escalator"))
        // A named mechanism keeps its plain label.
        #expect(WalkConnector.subtitle("escalator") == "Escalator")
        #expect(WalkConnector.subtitle("stairs") == "Stairs")
    }

    /// The card's level line must read like a person wrote it — and must never name
    /// a mode the map doesn't record.
    @Test func levelNoteNamesOnlyWhatTheMapRecords() throws {
        // Dortmund shipped as "4 level changes — level change + stairs." — the
        // renderer reading its own enum out loud.
        let d = try Self.scene("viz_dortmund_11_4")
        let dn = LevelNote.make(d.transitions)
        #expect(dn.text == "4 level changes — 1 by stairs, 3 the map doesn't name.")
        #expect(!dn.text.contains("level change +"))
        #expect(!dn.text.lowercased().contains("elevator"), "no lift is mapped here")

        // Berlin is four escalators and nothing else.
        let b = try Self.scene("viz_berlin_1_16")
        let bn = LevelNote.make(b.transitions)
        #expect(bn.text == "4 level changes, all by escalator.")
        #expect(bn.tint == Theme.esc)

        // No transitions at all is the one genuinely step-free case.
        let flat = LevelNote.make([])
        #expect(flat.text.contains("Step-free"))
        #expect(flat.icon == "checkmark")
        #expect(flat.tint == Theme.go)

        // Every change unclassified: say so, don't pick a mode.
        let z = Point3(x: 0, y: 0, z: 0)
        let vertical = [VizExport.Transition(kind: "vertical", wayId: nil, nodeId: nil, from: z, to: z),
                        VizExport.Transition(kind: "vertical", wayId: nil, nodeId: nil, from: z, to: z)]
        let vn = LevelNote.make(vertical)
        #expect(vn.text == "2 level changes — the map doesn't record stairs or a lift.")
        #expect(vn.tint == Theme.nodata)
        #expect(vn.icon == "questionmark.circle")
    }

    /// The 3D's floors must stay clearly stacked — "exploded floors" is the feature.
    ///
    /// `levelUnit` was a constant 20 while the thing it tracks (the iso-projected
    /// footprint) is data- and angle-dependent, so Berlin's −2/−1/0 fused into one
    /// plate (#53). It now scales with the footprint, but lifts by ~a third of it
    /// (not the old full clearance) so risers aren't stretched — floors deliberately
    /// *overlap*, yet each must still sit distinctly above the one below. Projected
    /// through the real `IsoFit`, not the formula: every floor's plate is lifted
    /// above the floor below by a readable fraction of a plate's depth, at any orbit.
    @Test func exploded3DFloorsDoNotFuse() throws {
        let s = try Self.scene("viz_berlin_1_16")
        let size = CGSize(width: 360, height: 300)
        // Frame on the walk box (what the 3D uses in walk mode); the plates are
        // measured over the *same* box, so the stacking test tracks what's on screen.
        let box = try #require(s.walkFramingBox)
        for angle in [0.0, 0.5, 1.0, 2.2, 3.9] {
            let iso = IsoFit(scene: s, box: box, size: size, angle: angle, zoom: 1, pad: 24)
            // The screen-y band a floor's plate occupies, from the framing box.
            func plateY(_ level: Int) -> (lo: CGFloat, hi: CGFloat) {
                var lo = CGFloat.greatestFiniteMagnitude, hi = -CGFloat.greatestFiniteMagnitude
                for x in [box.minX, box.maxX] {
                    for y in [box.minY, box.maxY] {
                        let p = iso.map(Point3(x: Float(x), y: Float(y),
                                               z: Float(CGFloat(level) * s.floorHeight)))
                        lo = min(lo, p.y); hi = max(hi, p.y)
                    }
                }
                return (lo, hi)
            }
            // Screen-y grows downward, so the upper floor sits at smaller y. Each
            // floor's whole plate must be shifted up from the one below (stacked, not
            // fused) by a clear fraction of a plate's depth — the plates now overlap,
            // but a one-floor lift is never so small the floors read as one.
            for level in s.levelsAsc.dropLast() {
                let lower = plateY(level), upper = plateY(level + 1)
                let depth = lower.hi - lower.lo             // one plate's screen-y span
                let lift  = lower.lo - upper.lo             // how far the upper floor rises
                #expect(upper.hi < lower.hi && upper.lo < lower.lo,
                        "angle \(angle): L\(level+1) not stacked above L\(level)")
                #expect(lift >= 0.25 * depth,
                        "angle \(angle): L\(level+1) lift \(lift) < ¼ of plate depth \(depth) — floors nearly fused")
            }
        }
    }

    /// The 3D must frame the WALK, not the whole station — the Berlin 1→16 "stick".
    ///
    /// The scene box spans every context way — ~510 × 814 m on Berlin, partly a
    /// spurious platform-1 fragment ~500 m south of everything — so framing the 3D on
    /// it crushed the ~57 × 55 m walk into a near-vertical line (its iso X-span fell
    /// to ~5 % of the canvas). Walk mode now frames the path's own XY box, grown by a
    /// margin, so the walk spreads across the canvas; browse (station map) mode still
    /// frames the whole station.
    @Test func threeDFramesTheWalkNotTheWholeStation() throws {
        let s = try Self.scene("viz_berlin_1_16")
        let size = CGSize(width: 340, height: 360)   // the app's 3D viewport shape

        // Fraction of the canvas width the projected path spans, under a given frame.
        func pathXFraction(box: CGRect) -> CGFloat {
            let iso = IsoFit(scene: s, box: box, size: size, angle: 0.5, zoom: 1, pad: 24)
            let xs = s.pathPoints.map { iso.map($0).x }
            return (xs.max()! - xs.min()!) / size.width
        }

        // The walk really is a sliver of the whole-station box — that's the bug.
        let walkBox = try #require(s.walkFramingBox)
        #expect(walkBox.width < 0.4 * s.worldBounds.width && walkBox.height < 0.4 * s.worldBounds.height,
                "the walk box should be a small part of the whole-station box")

        let sceneFrac = pathXFraction(box: s.worldBounds)   // old / browse framing
        let walkFrac  = pathXFraction(box: walkBox)          // the fix
        #expect(sceneFrac < 0.12, "whole-scene framing should crush the walk, got \(sceneFrac)")
        #expect(walkFrac > 0.20, "walk framing left it a stick at \(walkFrac) of canvas width")
        #expect(walkFrac > 3 * sceneFrac, "the fix should widen the walk several-fold")

        // Browse mode frames the whole station: the scene's own bounding corners (at
        // every level) all land inside the canvas — nothing clipped — and they fill
        // it, which the tight walk box never could.
        let browse = IsoFit(scene: s, box: s.worldBounds, size: size, angle: 0.5, zoom: 1, pad: 24)
        let corners = [s.minX, s.maxX].flatMap { x in [s.minY, s.maxY].flatMap { y in
            s.levelsAsc.map { browse.map(Point3(x: Float(x), y: Float(y),
                                                z: Float(CGFloat($0) * s.floorHeight))) }
        } }
        let cxs = corners.map(\.x), cys = corners.map(\.y)
        #expect(cxs.min()! >= -0.5 && cxs.max()! <= size.width + 0.5, "station clipped horizontally in browse")
        #expect(cys.min()! >= -0.5 && cys.max()! <= size.height + 0.5, "station clipped vertically in browse")
        #expect((cxs.max()! - cxs.min()!) > 0.5 * size.width || (cys.max()! - cys.min()!) > 0.5 * size.height,
                "browse should fill the frame with the whole station")
    }

    // MARK: - Facility map (every category POI pinned)

    /// The scene surfaces EVERY facility, lifts each to its floor, and frames them.
    ///
    /// The fixture is a real `/facility-map` browse export of Berlin Hbf with four
    /// `focus` toilets attached across floors (L−1, L0, L1) — exactly what the map
    /// fetches. Every POI must be a flagged detail, sit at its own floor, and lie
    /// inside the scene's XY box so no pin is ever drawn just off the canvas.
    @Test func facilityMapExposesAndFramesEveryPOI() throws {
        let s = try Self.scene("viz_berlin_facility")

        let pois = s.export.details.filter { $0.kind == "poi" }
        #expect(pois.count == 4)
        #expect(pois.allSatisfy { $0.focus == true && $0.xyz != nil })
        #expect(pois.allSatisfy { $0.subtype == "toilets" })

        // Spread across floors, each lifted to its own level (not flattened).
        let levels = Set(pois.compactMap { $0.xyz.map { s.level(of: $0.z) } })
        #expect(levels.contains(-1) && levels.contains(0) && levels.contains(1))

        // Every pin is framed inside the box the projection is fit to.
        for xyz in pois.compactMap(\.xyz) {
            #expect(CGFloat(xyz.x) >= s.minX && CGFloat(xyz.x) <= s.maxX)
            #expect(CGFloat(xyz.y) >= s.minY && CGFloat(xyz.y) <= s.maxY)
        }
        // The first focus POI is the scene's `focusPOI` (drives the walk view label).
        #expect(s.focusPOI?.focus == true)
    }

    /// A plain walk (no facility) has no POI details — the flag is opt-in.
    @Test func plainWalkHasNoFacilityPOIs() throws {
        #expect(try Self.scene("viz_berlin_1_16").focusPOI == nil)
        #expect(try Self.scene("viz_berlin_1_16").export.details.allSatisfy { $0.focus != true })
        #expect(try Self.scene("viz_dortmund_11_4").focusPOI == nil)
    }

    /// The map 3D rasterises with a selected pin (all pins + selection run, no crash).
    @Test func facilityMapCanvasRasterises() throws {
        let s = try Self.scene("viz_berlin_facility")
        try rasterize(IsoGeometryCanvas(scene: s, browse: true, animated: false, selectedPOI: 1),
                      size: CGSize(width: 360, height: 340), tag: "iso_facility_map_berlin")
    }

    // MARK: - The canvases actually rasterise (projection runs, no NaNs/crash)

    @Test func rendersAllThreeCanvases() throws {
        for name in ["viz_berlin_1_16", "viz_dortmund_11_4", "viz_essen_10_6"] {
            let s = try Self.scene(name)
            let short = name.replacingOccurrences(of: "viz_", with: "")

            try rasterize(SectionGeometryCanvas(scene: s),
                          size: CGSize(width: 360, height: 220), tag: "section_\(short)")
            // Every floor the picker can offer (sorted, so the render set is stable).
            for lvl in s.pathLevels {
                try rasterize(PlanGeometryCanvas(scene: s, level: lvl),
                              size: CGSize(width: 360, height: 260), tag: "level\(lvl)_\(short)")
            }
            // The floors only `levelsAsc` believed in — no longer offered by the
            // picker (#53), but the canvas must still survive being asked.
            for lvl in s.levelsAsc where !s.pathLevels.contains(lvl) {
                try rasterize(PlanGeometryCanvas(scene: s, level: lvl),
                              size: CGSize(width: 360, height: 260), tag: "ghosttab\(lvl)_\(short)")
            }
            try rasterize(IsoGeometryCanvas(scene: s, animated: false),
                          size: CGSize(width: 360, height: 300), tag: "iso_\(short)")
        }
    }

    /// Render a view to a PNG, assert it produced real pixels, and print the path.
    ///
    /// Writes to `$WALK_RENDER_DIR` when set (pass it through xcodebuild as
    /// `TEST_RUNNER_WALK_RENDER_DIR=…`), else the test process's temp dir — which
    /// the simulator wipes after the run, so anything you want to *look* at needs
    /// the env var.
    @discardableResult
    private func rasterize(_ view: some View, size: CGSize, tag: String) throws -> URL {
        let renderer = ImageRenderer(content: view.frame(width: size.width, height: size.height))
        renderer.scale = 2
        let image = try #require(renderer.uiImage, "\(tag): ImageRenderer produced no image")
        let data = try #require(image.pngData(), "\(tag): no PNG data")
        #expect(data.count > 1000, "\(tag): suspiciously small render (\(data.count) bytes)")
        var dir = FileManager.default.temporaryDirectory
        if let custom = ProcessInfo.processInfo.environment["WALK_RENDER_DIR"], !custom.isEmpty {
            dir = URL(fileURLWithPath: custom, isDirectory: true)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        let url = dir.appendingPathComponent("walkrender_\(tag).png")
        try data.write(to: url)
        print("RENDER_PNG: \(url.path)")
        return url
    }
}
