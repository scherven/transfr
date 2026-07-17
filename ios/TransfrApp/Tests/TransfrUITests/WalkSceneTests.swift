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

    /// The 3D's floors must actually separate — "exploded floors" is the feature.
    ///
    /// `levelUnit` was a constant 20 while the thing it has to clear (the iso-
    /// projected footprint) is data- and angle-dependent, so Berlin's −2/−1/0 fused
    /// into one plate. Projected through the real `IsoFit`, not the formula: two
    /// adjacent floors' plates must not share any screen-y, at any orbit angle.
    @Test func exploded3DFloorsDoNotFuse() throws {
        let s = try Self.scene("viz_berlin_1_16")
        let size = CGSize(width: 360, height: 300)
        for angle in [0.0, 0.5, 1.0, 2.2, 3.9] {
            let iso = IsoFit(scene: s, size: size, angle: angle, zoom: 1, pad: 24)
            // The screen-y band a floor's plate occupies, from the scene's own box.
            func plateY(_ level: Int) -> (lo: CGFloat, hi: CGFloat) {
                var lo = CGFloat.greatestFiniteMagnitude, hi = -CGFloat.greatestFiniteMagnitude
                for x in [s.minX, s.maxX] {
                    for y in [s.minY, s.maxY] {
                        let p = iso.map(Point3(x: Float(x), y: Float(y),
                                               z: Float(CGFloat(level) * s.floorHeight)))
                        lo = min(lo, p.y); hi = max(hi, p.y)
                    }
                }
                return (lo, hi)
            }
            // Screen-y grows downward, so the upper floor's plate must end above
            // where the lower floor's plate begins.
            for level in s.levelsAsc.dropLast() {
                let lower = plateY(level), upper = plateY(level + 1)
                #expect(upper.hi < lower.lo,
                        "angle \(angle): L\(level+1) plate (…\(upper.hi)) overlaps L\(level) (\(lower.lo)…)")
            }
        }
    }

    // MARK: - The canvases actually rasterise (projection runs, no NaNs/crash)

    @Test func rendersAllThreeCanvases() throws {
        for name in ["viz_berlin_1_16", "viz_dortmund_11_4"] {
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
            try rasterize(IsoGeometryCanvas(scene: s),
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
