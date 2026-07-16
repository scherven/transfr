import Foundation
import SwiftUI
import Testing
@testable import TransfrUI

/// The cold-launch mark (LaunchView / LaunchMark). Confirms the end pose actually
/// rasterises (the same ImageRenderer path WalkSceneTests uses), and that its
/// geometry lands where the handoff to InputView expects: the wordmark fully
/// written, the red pen at REST (end of the final r).
@MainActor
struct LaunchViewTests {

    /// The end pose draws to real pixels (compiles + projects, no NaNs/crash).
    @Test func rendersEndPose() throws {
        let view = LaunchMark(t: LaunchPhase.hold)
            .frame(width: 360, height: 440)
            .background(Theme.paper)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try #require(renderer.uiImage, "ImageRenderer produced no image")
        let data = try #require(image.pngData(), "no PNG data")
        #expect(data.count > 1000, "suspiciously small render (\(data.count) bytes)")
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("launch_endpose.png")
        try data.write(to: url)
        print("RENDER_PNG: \(url.path)")
    }

    /// An early frame (mid-write) also rasterises — the trim + pen-sampling path runs.
    @Test func rendersMidWrite() throws {
        let view = LaunchMark(t: 1.4)
            .frame(width: 360, height: 440)
            .background(Theme.paper)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        #expect(try #require(renderer.uiImage).pngData().map { $0.count > 1000 } == true)
    }

    /// At the end pose the wordmark is fully written and the pen has landed at REST.
    @Test func endPoseIsFullyWrittenAtRest() {
        #expect(LaunchGeometry.writeProgress(at: LaunchPhase.hold) == 1)
        let state = LaunchGeometry.writeState(at: 1)
        #expect(state.reveal.count == LaunchStroke.drawn.count)
        #expect(state.reveal.values.allSatisfy { $0 >= 0.999 })
        #expect(abs(state.pen.x - LaunchGeometry.rest.x) < 0.5)
        #expect(abs(state.pen.y - LaunchGeometry.rest.y) < 0.5)
    }

    /// The camera holds on the "t" at the start (Phase A) and rests on the wordmark
    /// at the end (Phase B) — the two poses the dolly interpolates between.
    @Test func cameraHoldsThenRests() {
        #expect(LaunchGeometry.camera(at: 0).scale == LaunchGeometry.scaleA)
        #expect(LaunchGeometry.camera(at: 0).focus == LaunchGeometry.tFocus)
        #expect(LaunchGeometry.camera(at: LaunchPhase.hold).scale == LaunchGeometry.scaleB)
        #expect(LaunchGeometry.camera(at: LaunchPhase.hold).focus == LaunchGeometry.wordFocus)
    }

    /// Before writing begins the pen sits at its home (the t's tip) with nothing of
    /// "ransfr" revealed — the pose the animation starts from.
    @Test func startPoseIsUnwrittenAtHome() {
        #expect(LaunchGeometry.writeProgress(at: 0) == 0)
        let state = LaunchGeometry.writeState(at: 0)
        #expect(state.reveal.values.allSatisfy { $0 == 0 })
        #expect(state.pen == LaunchGeometry.start)
    }
}
