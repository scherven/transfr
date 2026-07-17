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

    // MARK: - The canvases actually rasterise (projection runs, no NaNs/crash)

    @Test func rendersAllThreeCanvases() throws {
        for name in ["viz_berlin_1_16", "viz_dortmund_11_4"] {
            let s = try Self.scene(name)
            let short = name.replacingOccurrences(of: "viz_", with: "")

            try rasterize(SectionGeometryCanvas(scene: s),
                          size: CGSize(width: 360, height: 220), tag: "section_\(short)")
            // Every floor the walk visits (sorted, so the render set is stable).
            for lvl in s.pathLevels {
                try rasterize(PlanGeometryCanvas(scene: s, level: lvl),
                              size: CGSize(width: 360, height: 260), tag: "level\(lvl)_\(short)")
            }
            try rasterize(IsoGeometryCanvas(scene: s),
                          size: CGSize(width: 360, height: 300), tag: "iso_\(short)")
        }
    }

    /// Render a view to a PNG, assert it produced real pixels, and print the path.
    private func rasterize(_ view: some View, size: CGSize, tag: String) throws {
        let renderer = ImageRenderer(content: view.frame(width: size.width, height: size.height))
        renderer.scale = 2
        let image = try #require(renderer.uiImage, "\(tag): ImageRenderer produced no image")
        let data = try #require(image.pngData(), "\(tag): no PNG data")
        #expect(data.count > 1000, "\(tag): suspiciously small render (\(data.count) bytes)")
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("walkrender_\(tag).png")
        try data.write(to: url)
        print("RENDER_PNG: \(url.path)")
    }
}
