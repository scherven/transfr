import SwiftUI

// The cold-launch animation: the favicon "t" is held, the camera dollies out to
// the full "transfr" wordmark, and a red "pen" dot writes "ransfr" and lands at
// the end of the r. The end pose IS the main-screen wordmark (green dot on the t,
// red dot at the end of the r), so the handoff to InputView reads as one mark.
//
// Ported 1:1 from design/loading-animation.html (the human-approved preview):
// same timeline, camera, stroke plan, and colours. SwiftUI mapping (from that
// file's notes):
//   • CAMERA     -> a scale+offset (CGAffineTransform) on the mark canvas.
//   • INK REVEAL -> per-stroke Shape + .trim(from:0,to:progress).
//   • RED-DOT    -> point-on-path sampling (Path.trimmedPath(...).currentPoint).
//   • PHASES     -> one TimelineView(.animation) clock.
// Colours are the app's Theme tokens (accent / go / miss on paper), so the mark
// is legible and on-brand in both light and dark. There is NO white icon tile —
// the "t" sits directly on the paper background.

// MARK: - Timeline (seconds)

enum LaunchPhase {
    static let a: Double = 0.35          // hold on the "t"
    static let b: Double = 0.95          // camera dolly (t -> wordmark) finishes
    static let writeStart: Double = 0.90 // pen starts writing (overlaps the dolly)
    static let writeEnd: Double = 2.05   // pen finishes
    static let settle: Double = 2.22     // red-dot "plant" as it lands
    static let hold: Double = 3.05       // finished wordmark — the END POSE
    /// Reduced-motion: how long to show the static end pose before revealing the app.
    static let reduceHold: Double = 0.8
}

// MARK: - Geometry (wordmark-local units, viewBox 0..360 x 0..440)

enum LaunchGeometry {
    static let stage = CGSize(width: 360, height: 440)
    static let center = CGPoint(x: 180, y: 220)     // stage centre
    static let tFocus = CGPoint(x: 28, y: 34)       // the t's optical centre (Phase A)
    static let wordFocus = CGPoint(x: 134, y: 33)   // the whole wordmark's centre (Phase B)
    static let scaleA: Double = 2.60                // icon scale (Phase A)
    static let scaleB: Double = 1.20                // wordmark scale (Phase B)
    static let start = CGPoint(x: 43, y: 58)        // red dot home: the t's bottom-right tip
    static let rest = CGPoint(x: 253, y: 33)        // red dot destination: end of the final r
    static let greenCenter = CGPoint(x: 28, y: 10)  // green dot: fixed on the t
    static let dotRadius: Double = 6.5
    static let strokeWidth: Double = 8

    // MARK: helpers

    static func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double { min(max(v, lo), hi) }
    static func lerp(_ x: Double, _ y: Double, _ u: Double) -> Double { x + (y - x) * u }
    static func lerp(_ p: CGPoint, _ q: CGPoint, _ u: Double) -> CGPoint {
        CGPoint(x: p.x + (q.x - p.x) * CGFloat(u), y: p.y + (q.y - p.y) * CGFloat(u))
    }
    /// ease-in-out (camera dolly)
    static func smooth(_ u: Double) -> Double { let x = clamp(u, 0, 1); return x * x * (3 - 2 * x) }
    /// ease-in-out sine (pen)
    static func easeWrite(_ u: Double) -> Double { let x = clamp(u, 0, 1); return -(cos(.pi * x) - 1) / 2 }

    // MARK: camera / writing / settle as pure functions of time

    struct Camera { var scale: Double; var focus: CGPoint }

    /// Phase A holds on the t; A->B dollies out to the wordmark (with a small
    /// "expand" pulse as the mark opens); after B it rests on the wordmark.
    static func camera(at t: Double) -> Camera {
        if t <= LaunchPhase.a { return Camera(scale: scaleA, focus: tFocus) }
        if t < LaunchPhase.b {
            let u = smooth((t - LaunchPhase.a) / (LaunchPhase.b - LaunchPhase.a))
            var s = lerp(scaleA, scaleB, u)
            let focus = lerp(tFocus, wordFocus, u)
            let pulse = (t - LaunchPhase.a) / 0.26
            if pulse < 1 { s *= 1 + 0.075 * sin(.pi * clamp(pulse, 0, 1)) }
            return Camera(scale: s, focus: focus)
        }
        return Camera(scale: scaleB, focus: wordFocus)
    }

    /// Global writing progress p in [0,1] over the writing window.
    static func writeProgress(at t: Double) -> Double {
        if t >= LaunchPhase.writeEnd { return 1 }
        if t > LaunchPhase.writeStart {
            return easeWrite((t - LaunchPhase.writeStart) / (LaunchPhase.writeEnd - LaunchPhase.writeStart))
        }
        return 0
    }

    /// A small "plant" of the red dot as it lands on the final r.
    static func dotPop(at t: Double) -> Double {
        if t > LaunchPhase.writeEnd && t < LaunchPhase.settle {
            let u = clamp((t - LaunchPhase.writeEnd) / (LaunchPhase.settle - LaunchPhase.writeEnd), 0, 1)
            return 1 + 0.18 * sin(.pi * u)
        }
        return 1
    }

    /// local (wordmark) coords -> stage (360x440) coords: translate(C - s*focus) * scale(s).
    static func localToStage(_ cam: Camera) -> CGAffineTransform {
        CGAffineTransform(translationX: center.x - CGFloat(cam.scale) * cam.focus.x,
                          y: center.y - CGFloat(cam.scale) * cam.focus.y)
            .scaledBy(x: CGFloat(cam.scale), y: CGFloat(cam.scale))
    }

    // MARK: the pen's ordered journey (draw a stroke, or travel between them)

    /// Endpoints of the seven invisible "pen-up" travel paths, in PLAN order.
    static let movePairs: [(CGPoint, CGPoint)] = [
        (CGPoint(x: 43, y: 58),  CGPoint(x: 58, y: 58)),   // m1
        (CGPoint(x: 76, y: 34),  CGPoint(x: 110, y: 52)),  // m2
        (CGPoint(x: 110, y: 58), CGPoint(x: 126, y: 58)),  // m3
        (CGPoint(x: 152, y: 58), CGPoint(x: 187, y: 32)),  // m4
        (CGPoint(x: 166, y: 53), CGPoint(x: 209, y: 58)),  // m5
        (CGPoint(x: 222, y: 14), CGPoint(x: 201, y: 26)),  // m6
        (CGPoint(x: 220, y: 26), CGPoint(x: 234, y: 58)),  // m7
    ]

    enum Step { case draw(LaunchStroke); case move(Int) }

    static let plan: [Step] = [
        .move(0), .draw(.r1),
        .move(1), .draw(.a),
        .move(2), .draw(.n),
        .move(3), .draw(.s),
        .move(4), .draw(.fstem),
        .move(5), .draw(.fbar),
        .move(6), .draw(.r2),
    ]

    static func path(for step: Step) -> Path {
        switch step {
        case .draw(let stroke):
            return stroke.path
        case .move(let i):
            let (p0, p1) = movePairs[i]
            var p = Path()
            p.move(to: p0)
            p.addLine(to: p1)
            return p
        }
    }

    /// Approximate a path's length by walking `trimmedPath(...).currentPoint`.
    static func length(of path: Path, samples: Int = 40) -> Double {
        var previous = path.trimmedPath(from: 0, to: 0.0001).currentPoint ?? .zero
        var total = 0.0
        for i in 1...samples {
            let f = CGFloat(i) / CGFloat(samples)
            let point = path.trimmedPath(from: 0, to: f).currentPoint ?? previous
            total += Double(hypot(point.x - previous.x, point.y - previous.y))
            previous = point
        }
        return total
    }

    /// The writing timeline apportioned by stroke length (moves discounted to 0.5x,
    /// so the pen skips briskly between letters). Computed once. Only plain Sendable
    /// numbers are stored; Paths are rebuilt on demand.
    static let segments: [(step: Step, f0: Double, f1: Double)] = {
        let weights = plan.map { step -> Double in
            let len = length(of: path(for: step))
            if case .draw = step { return len }
            return 0.5 * len
        }
        let total = max(weights.reduce(0, +), 0.0001)
        var acc = 0.0
        return zip(plan, weights).map { step, w in
            let f0 = acc / total
            acc += w
            return (step, f0, acc / total)
        }
    }()

    /// Per-stroke reveal fractions + the red pen's point, for a global progress p.
    /// Mirrors the preview's applyWrite().
    static func writeState(at p: Double) -> (reveal: [LaunchStroke: Double], pen: CGPoint) {
        if p <= 0 {
            return (Dictionary(uniqueKeysWithValues: LaunchStroke.drawn.map { ($0, 0) }), start)
        }
        if p >= 1 {
            return (Dictionary(uniqueKeysWithValues: LaunchStroke.drawn.map { ($0, 1) }), rest)
        }
        var reveal: [LaunchStroke: Double] = [:]
        var pen = start
        for seg in segments {
            if case .draw(let stroke) = seg.step {
                if p >= seg.f1 { reveal[stroke] = 1 }
                else if p < seg.f0 { reveal[stroke] = 0 }
                else { reveal[stroke] = (p - seg.f0) / (seg.f1 - seg.f0) }
            }
            if p >= seg.f0 && p < seg.f1 {
                let local = (p - seg.f0) / max(seg.f1 - seg.f0, 0.0001)
                pen = path(for: seg.step).trimmedPath(from: 0, to: CGFloat(local)).currentPoint ?? pen
            }
        }
        for stroke in LaunchStroke.drawn where reveal[stroke] == nil { reveal[stroke] = 0 }
        return (reveal, pen)
    }
}

// MARK: - Letterforms (verbatim from favicon.svg / the preview's SVG paths)

enum LaunchStroke: Int, CaseIterable, Hashable {
    case tBar, tStem            // the leading "t" — always fully shown
    case r1, a, n, s, fstem, fbar, r2   // "ransfr" — revealed as the pen passes

    /// The seven revealed strokes, in the order the pen writes them.
    static let drawn: [LaunchStroke] = [.r1, .a, .n, .s, .fstem, .fbar, .r2]

    var path: Path {
        var p = Path()
        switch self {
        case .tBar:
            p.move(to: CGPoint(x: 13, y: 24)); p.addLine(to: CGPoint(x: 43, y: 24))
        case .tStem:
            p.move(to: CGPoint(x: 28, y: 10)); p.addLine(to: CGPoint(x: 28, y: 48))
            p.addQuadCurve(to: CGPoint(x: 43, y: 58), control: CGPoint(x: 28, y: 58))
        case .r1:
            p.move(to: CGPoint(x: 58, y: 58)); p.addLine(to: CGPoint(x: 58, y: 30))
            p.addQuadCurve(to: CGPoint(x: 66, y: 26), control: CGPoint(x: 58, y: 26))
            p.addQuadCurve(to: CGPoint(x: 76, y: 34), control: CGPoint(x: 74, y: 26))
        case .a:
            p.move(to: CGPoint(x: 110, y: 52))
            p.addQuadCurve(to: CGPoint(x: 100, y: 58), control: CGPoint(x: 110, y: 58))
            p.addQuadCurve(to: CGPoint(x: 84, y: 42), control: CGPoint(x: 84, y: 58))
            p.addQuadCurve(to: CGPoint(x: 100, y: 26), control: CGPoint(x: 84, y: 26))
            p.addQuadCurve(to: CGPoint(x: 110, y: 32), control: CGPoint(x: 110, y: 26))
            p.addLine(to: CGPoint(x: 110, y: 58))
        case .n:
            p.move(to: CGPoint(x: 126, y: 58)); p.addLine(to: CGPoint(x: 126, y: 30))
            p.addQuadCurve(to: CGPoint(x: 139, y: 26), control: CGPoint(x: 126, y: 26))
            p.addQuadCurve(to: CGPoint(x: 152, y: 30), control: CGPoint(x: 152, y: 26))
            p.addLine(to: CGPoint(x: 152, y: 58))
        case .s:
            p.move(to: CGPoint(x: 187, y: 32))
            p.addQuadCurve(to: CGPoint(x: 176, y: 26), control: CGPoint(x: 182, y: 26))
            p.addQuadCurve(to: CGPoint(x: 166, y: 35), control: CGPoint(x: 166, y: 26))
            p.addQuadCurve(to: CGPoint(x: 177, y: 42), control: CGPoint(x: 166, y: 42))
            p.addQuadCurve(to: CGPoint(x: 188, y: 49), control: CGPoint(x: 188, y: 42))
            p.addQuadCurve(to: CGPoint(x: 178, y: 58), control: CGPoint(x: 188, y: 58))
            p.addQuadCurve(to: CGPoint(x: 166, y: 53), control: CGPoint(x: 170, y: 58))
        case .fstem:
            p.move(to: CGPoint(x: 209, y: 58)); p.addLine(to: CGPoint(x: 209, y: 18))
            p.addQuadCurve(to: CGPoint(x: 215, y: 9), control: CGPoint(x: 209, y: 9))
            p.addQuadCurve(to: CGPoint(x: 222, y: 14), control: CGPoint(x: 222, y: 9))
        case .fbar:
            p.move(to: CGPoint(x: 201, y: 26)); p.addLine(to: CGPoint(x: 220, y: 26))
        case .r2:
            p.move(to: CGPoint(x: 234, y: 58)); p.addLine(to: CGPoint(x: 234, y: 30))
            p.addQuadCurve(to: CGPoint(x: 242, y: 26), control: CGPoint(x: 234, y: 26))
            p.addQuadCurve(to: CGPoint(x: 253, y: 33), control: CGPoint(x: 250, y: 26))
        }
        return p
    }
}

/// A stroke Path pushed through the (camera + view-fit) affine transform, so it can
/// be `.trim`med and `.stroke`d directly. Uniform scale, so trim-by-fraction is
/// preserved.
struct TransformedStroke: Shape {
    let base: Path
    let transform: CGAffineTransform
    func path(in rect: CGRect) -> Path { base.applying(transform) }
}

// MARK: - The animated mark (pure function of time `t`)

struct LaunchMark: View {
    let t: Double
    var ink: Color = Theme.accent
    var green: Color = Theme.go
    var red: Color = Theme.miss

    var body: some View {
        let cam = LaunchGeometry.camera(at: t)
        let state = LaunchGeometry.writeState(at: LaunchGeometry.writeProgress(at: t))
        let pop = LaunchGeometry.dotPop(at: t)
        GeometryReader { geo in
            let fit = min(geo.size.width / LaunchGeometry.stage.width,
                          geo.size.height / LaunchGeometry.stage.height)
            let ox = (geo.size.width - LaunchGeometry.stage.width * fit) / 2
            let oy = (geo.size.height - LaunchGeometry.stage.height * fit) / 2
            let stageToView = CGAffineTransform(translationX: ox, y: oy).scaledBy(x: fit, y: fit)
            let transform = LaunchGeometry.localToStage(cam).concatenating(stageToView)
            let lineWidth = CGFloat(LaunchGeometry.strokeWidth) * CGFloat(cam.scale) * fit
            let dotScale = CGFloat(cam.scale) * fit
            let style = StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
            ZStack {
                // the leading "t" — always fully drawn
                ForEach([LaunchStroke.tBar, .tStem], id: \.self) { stroke in
                    TransformedStroke(base: stroke.path, transform: transform).stroke(ink, style: style)
                }
                // "ransfr" — revealed as the pen passes
                ForEach(LaunchStroke.drawn, id: \.self) { stroke in
                    TransformedStroke(base: stroke.path, transform: transform)
                        .trim(from: 0, to: CGFloat(state.reveal[stroke] ?? 0))
                        .stroke(ink, style: style)
                }
                // the two brand dots
                dot(at: LaunchGeometry.greenCenter, radius: CGFloat(LaunchGeometry.dotRadius) * dotScale,
                    color: green, transform: transform)
                dot(at: state.pen, radius: CGFloat(LaunchGeometry.dotRadius * pop) * dotScale,
                    color: red, transform: transform)
            }
        }
    }

    private func dot(at local: CGPoint, radius: CGFloat, color: Color, transform: CGAffineTransform) -> some View {
        let p = local.applying(transform)
        return Circle().fill(color).frame(width: radius * 2, height: radius * 2).position(p)
    }
}

// MARK: - The launch screen (plays once, then reveals the app)

/// Shown over the app on cold launch. Plays the mark once, then calls `onFinished`
/// so the host can reveal InputView. Robust by construction: the completion is a
/// wall-clock timer that ALWAYS fires, so the overlay is always removed and the
/// app is never blocked — even if the drawing somehow misbehaves. Respects
/// reduced motion by showing the static end pose and skipping the animation.
public struct LaunchView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private let onFinished: () -> Void
    @State private var startDate = Date()
    @State private var didFinish = false

    public init(onFinished: @escaping () -> Void) {
        self.onFinished = onFinished
    }

    public var body: some View {
        ZStack {
            Theme.paper.ignoresSafeArea()
            Group {
                if reduceMotion {
                    LaunchMark(t: LaunchPhase.hold)   // static end pose, no motion
                } else {
                    TimelineView(.animation) { timeline in
                        let t = min(timeline.date.timeIntervalSince(startDate), LaunchPhase.hold)
                        LaunchMark(t: t)
                    }
                }
            }
            .padding(24)
        }
        .accessibilityElement()
        .accessibilityLabel("transfr")
        .onAppear {
            startDate = Date()
            let delay = reduceMotion ? LaunchPhase.reduceHold : LaunchPhase.hold
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { finish() }
        }
    }

    private func finish() {
        guard !didFinish else { return }
        didFinish = true
        onFinished()
    }
}
