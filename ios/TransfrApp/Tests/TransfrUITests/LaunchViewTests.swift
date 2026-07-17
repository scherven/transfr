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

    // MARK: fly-to-title hand-off

    /// After the end pose the mark flies up: `landProgress` runs 0 (at `hold`,
    /// centred) → 1 (at `flyEnd`, landed), monotonically, and never overshoots.
    @Test func landProgressRunsHoldToFlyEnd() {
        #expect(LaunchGeometry.landProgress(at: LaunchPhase.hold) == 0)
        #expect(LaunchGeometry.landProgress(at: LaunchPhase.hold - 1) == 0)   // still centred at the end pose
        #expect(LaunchGeometry.landProgress(at: LaunchPhase.flyEnd) == 1)
        #expect(LaunchGeometry.landProgress(at: LaunchPhase.flyEnd + 1) == 1)
        let a = LaunchGeometry.landProgress(at: LaunchPhase.hold + 0.15)
        let b = LaunchGeometry.landProgress(at: LaunchPhase.hold + 0.40)
        #expect(a > 0 && a < b && b < 1)
    }

    /// At the landing the finished wordmark aspect-fits *and* centres on the app
    /// title's frame — the geometry that makes the crossfade read as one mark.
    @Test func landedMarkAspectFitsAndCentersOnTitle() {
        let target = CGRect(x: 20, y: 120, width: 160, height: 42)
        let landed = LaunchGeometry.stageToView(fit: .identity, target: target, landProgress: 1)
        let box = LaunchGeometry.wordmarkStageBounds.applying(landed)
        #expect(abs(box.midX - target.midX) < 0.5)          // centred…
        #expect(abs(box.midY - target.midY) < 0.5)
        #expect(box.width <= target.width + 0.5)            // …fits inside…
        #expect(box.height <= target.height + 0.5)
        #expect(box.width >= target.width - 0.5 || box.height >= target.height - 0.5)  // …touching one axis
    }

    /// The opaque backdrop holds through the write, the end pose, AND a short beat into
    /// the fly (so the mark lifts off before the app shows), then clears over the back
    /// of the fly — reaching 0 exactly as the mark lands. So the main screen phases in
    /// slightly after the movement begins and is fully shown the moment it touches down.
    @Test func backdropClearsInTheBackOfTheFly() {
        #expect(LaunchGeometry.backdropOpacity(at: LaunchPhase.writeEnd) == 1)  // opaque while writing
        #expect(LaunchGeometry.backdropOpacity(at: LaunchPhase.hold) == 1)      // opaque as the fly begins
        // Still fully opaque a touch into the fly — the reveal is deliberately delayed.
        let delayed = LaunchPhase.hold + LaunchPhase.fly * LaunchPhase.revealDelayFraction * 0.5
        #expect(LaunchGeometry.backdropOpacity(at: delayed) == 1)
        #expect(LaunchGeometry.backdropOpacity(at: LaunchPhase.flyEnd) == 0)    // fully cleared on landing
        #expect(LaunchGeometry.backdropOpacity(at: LaunchPhase.flyEnd + 1) == 0)
        // Then it clears monotonically over the back of the fly.
        let early = LaunchGeometry.backdropOpacity(at: LaunchPhase.hold + LaunchPhase.fly * 0.6)
        let late = LaunchGeometry.backdropOpacity(at: LaunchPhase.hold + LaunchPhase.fly * 0.85)
        #expect(early > late && late > 0 && early < 1)
    }

    /// With no target (anchor not yet resolved) the mark stays put — the fly is a
    /// no-op and the launch still falls through to its centred pose.
    @Test func noTargetLeavesMarkCentred() {
        let fit = CGAffineTransform(translationX: 12, y: 34).scaledBy(x: 0.9, y: 0.9)
        #expect(LaunchGeometry.stageToView(fit: fit, target: nil, landProgress: 1) == fit)
    }

    /// A mid-fly frame rasterises when a target is supplied (the lerped transform
    /// projects with no NaNs/crash).
    @Test func rendersMidFly() throws {
        let view = LaunchMark(t: LaunchPhase.hold + LaunchPhase.fly / 2,
                              targetRect: CGRect(x: 20, y: 120, width: 160, height: 42))
            .frame(width: 393, height: 852)
            .background(Theme.paper)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        #expect(try #require(renderer.uiImage).pngData().map { $0.count > 1000 } == true)
    }
}
