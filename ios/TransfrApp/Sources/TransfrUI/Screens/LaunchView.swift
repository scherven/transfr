import SwiftUI

// The cold-launch animation: the favicon "t" is held, the camera dollies out to
// the full "transfr" wordmark, and a red "pen" dot writes "ransfr" and lands at
// the end of the r. The end pose IS the main-screen wordmark (green dot on the t,
// red dot at the end of the r), so the handoff to InputView reads as one mark.
//
// Ported 1:1 from agents/design/loading-animation.html (the human-approved preview):
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
    static let a: Double = 0.30          // hold on the "t" (shorter — write starts sooner)
    static let b: Double = 0.90          // camera dolly (t -> wordmark) finishes
    static let writeStart: Double = 0.65 // pen starts writing (sooner; still overlaps the dolly)
    static let writeEnd: Double = 1.85   // pen finishes
    static let settle: Double = 2.02     // red-dot "plant" as it lands (0.17s after writeEnd)
    static let hold: Double = 2.35       // finished wordmark — the END POSE (shorter pause before the fly)
    static let fly: Double = 0.55        // the end pose flies up onto the app title
    static let flyEnd: Double = 2.90     // hold + fly — the hand-off to InputView
    /// The app phases in during the *back* of the fly: the backdrop holds opaque for
    /// this fraction of the fly after it begins (so the mark visibly lifts off before
    /// the main screen shows through), then clears — finishing as the mark lands. Turn
    /// this down for an earlier reveal, up for a later one. See `backdropOpacity`.
    static let revealDelayFraction: Double = 0.35
    /// Reduced-motion: how long to show the static end pose before revealing the app.
    static let reduceHold: Double = 0.8
}

// MARK: - Geometry (wordmark-local units, viewBox 0..360 x 0..440)

enum LaunchGeometry {
    static let stage = CGSize(width: 360, height: 440)
    static let center = CGPoint(x: 180, y: 220)     // stage centre
    static let tFocus = CGPoint(x: 28, y: 34)       // the t's optical centre (Phase A)
    static let wordFocus = CGPoint(x: 134, y: 33)   // the whole wordmark's centre (Phase B)
    static let scaleA: Double = 2.25                // icon scale (Phase A) — the "t" doesn't zoom quite as large before the write
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
    /// Cubic ease-in-out (easeInOutCubic) for the fly-to-title. More pronounced than
    /// the camera's smoothstep, so the hand-off reads as a deliberate *curved* move —
    /// a gentle acceleration out of the end pose, easing into the landing — rather
    /// than a near-linear slide. Monotonic 0->1 with no overshoot, so the mark still
    /// lands pixel-exact on the title.
    static func easeFly(_ u: Double) -> Double {
        let x = clamp(u, 0, 1)
        if x < 0.5 { return 4 * x * x * x }
        let f = -2 * x + 2
        return 1 - f * f * f / 2
    }

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

    // MARK: fly-to-title (the hand-off to InputView)

    /// After the end pose, the finished mark travels from screen-centre up onto the
    /// app's "transfr" title. 0 at `hold` (centred), 1 at `flyEnd` (landed).
    static func landProgress(at t: Double) -> Double {
        if t <= LaunchPhase.hold { return 0 }
        if t >= LaunchPhase.flyEnd { return 1 }
        return easeFly((t - LaunchPhase.hold) / LaunchPhase.fly)
    }

    /// Opacity of the opaque launch backdrop (the paper that hides the app during the
    /// mark). It stays opaque through the write, the end pose, AND a short beat after
    /// the fly begins (`revealDelayFraction` of the fly) — so the mark visibly lifts off
    /// before the app shows through — then clears over the back of the fly, reaching 0
    /// exactly as the mark lands. So the main screen phases in *slightly after* the
    /// movement starts and is fully shown the instant the mark touches down. The mark is
    /// drawn separately and stays fully opaque, so it still visibly lands and then hands
    /// off to InputView's identical (now-revealed) title.
    static func backdropOpacity(at t: Double) -> Double {
        let revealStart = LaunchPhase.hold + LaunchPhase.fly * LaunchPhase.revealDelayFraction
        if t <= revealStart { return 1 }
        if t >= LaunchPhase.flyEnd { return 0 }
        return 1 - smooth((t - revealStart) / (LaunchPhase.flyEnd - revealStart))
    }

    /// The finished wordmark's tight bounding box in STAGE (360x440) coords at the
    /// end pose — every stroke (incl. its round cap width) plus the two brand dots.
    /// This is the box we map onto the InputView title so the landing lines up.
    static let wordmarkStageBounds: CGRect = {
        let m = localToStage(camera(at: LaunchPhase.hold))
        let halfStroke = CGFloat(strokeWidth) / 2 * CGFloat(scaleB)
        var box = CGRect.null
        for stroke in LaunchStroke.allCases {
            box = box.union(stroke.path.applying(m).boundingRect.insetBy(dx: -halfStroke, dy: -halfStroke))
        }
        let r = CGFloat(dotRadius) * CGFloat(scaleB)
        for centre in [greenCenter, rest] {
            let p = centre.applying(m)
            box = box.union(CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r))
        }
        return box
    }()

    /// The stage->view transform for the current frame: the aspect-fit `fit` while
    /// centred, lerped toward "the wordmark box aspect-fits onto `target`" as the
    /// mark flies up. Uniform scale throughout, so `.trim`/stroking stay correct.
    static func stageToView(fit: CGAffineTransform, target: CGRect?, landProgress p: Double) -> CGAffineTransform {
        guard let target, p > 0 else { return fit }
        let box = wordmarkStageBounds
        let s = min(target.width / box.width, target.height / box.height)
        let ox = target.midX - box.width * s / 2 - box.minX * s
        let oy = target.midY - box.height * s / 2 - box.minY * s
        let landed = CGAffineTransform(a: s, b: 0, c: 0, d: s, tx: ox, ty: oy)
        return lerpAffine(fit, landed, CGFloat(p))
    }

    /// Component-wise lerp of two translate+scale transforms (no rotation/shear).
    static func lerpAffine(_ A: CGAffineTransform, _ B: CGAffineTransform, _ u: CGFloat) -> CGAffineTransform {
        CGAffineTransform(a: A.a + (B.a - A.a) * u, b: A.b + (B.b - A.b) * u,
                          c: A.c + (B.c - A.c) * u, d: A.d + (B.d - A.d) * u,
                          tx: A.tx + (B.tx - A.tx) * u, ty: A.ty + (B.ty - A.ty) * u)
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
    /// When set (from RootView), the finished mark flies up and aspect-fits onto
    /// this rect — the InputView "transfr" title's frame, in the same full-screen
    /// coordinate space this view fills.
    var targetRect: CGRect? = nil
    var ink: Color = Theme.accent
    var green: Color = Theme.go
    var red: Color = Theme.miss

    var body: some View {
        let cam = LaunchGeometry.camera(at: t)
        let state = LaunchGeometry.writeState(at: LaunchGeometry.writeProgress(at: t))
        let pop = LaunchGeometry.dotPop(at: t)
        let landP = LaunchGeometry.landProgress(at: t)
        GeometryReader { geo in
            // Centre the stage in the screen with a small inset (the old padding);
            // then it flies onto `targetRect` as the launch hands off to the app.
            let inset: CGFloat = 24
            let fit = min(max(geo.size.width - 2 * inset, 1) / LaunchGeometry.stage.width,
                          max(geo.size.height - 2 * inset, 1) / LaunchGeometry.stage.height)
            let ox = (geo.size.width - LaunchGeometry.stage.width * fit) / 2
            let oy = (geo.size.height - LaunchGeometry.stage.height * fit) / 2
            let fitTransform = CGAffineTransform(translationX: ox, y: oy).scaledBy(x: fit, y: fit)
            let stageToView = LaunchGeometry.stageToView(fit: fitTransform, target: targetRect, landProgress: landP)
            let viewScale = stageToView.a   // uniform (b == c == 0)
            let transform = LaunchGeometry.localToStage(cam).concatenating(stageToView)
            let lineWidth = CGFloat(LaunchGeometry.strokeWidth) * CGFloat(cam.scale) * viewScale
            let dotScale = CGFloat(cam.scale) * viewScale
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
    /// The InputView title's frame (full-screen coords) the finished mark flies onto.
    private let targetRect: CGRect?
    private let onFinished: () -> Void
    @State private var startDate = Date()
    @State private var didFinish = false

    public init(targetRect: CGRect? = nil, onFinished: @escaping () -> Void) {
        self.targetRect = targetRect
        self.onFinished = onFinished
    }

    public var body: some View {
        // Fills the screen (no padding) so the mark's coordinate space matches the
        // resolved `targetRect`; the inset that keeps the mark off the edges lives
        // inside LaunchMark's fit.
        GeometryReader { geo in
            Group {
                if reduceMotion {
                    // No motion to sync with: keep the opaque backdrop and let the
                    // reveal be the in-place crossfade at `finish()`.
                    ZStack {
                        Theme.paper
                        LaunchMark(t: LaunchPhase.flyEnd, targetRect: targetRect)
                    }
                } else {
                    TimelineView(.animation) { timeline in
                        let t = min(timeline.date.timeIntervalSince(startDate), LaunchPhase.flyEnd)
                        ZStack {
                            // The backdrop fades out *as the mark flies onto the title*,
                            // so the main screen loads into view during the move and is
                            // fully shown the moment the mark lands. The mark stays fully
                            // opaque (drawn on top), so it still visibly lands and then
                            // hands off to InputView's identical title underneath.
                            Theme.paper.opacity(LaunchGeometry.backdropOpacity(at: t))
                            LaunchMark(t: t, targetRect: targetRect)
                        }
                    }
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .ignoresSafeArea()
        .accessibilityElement()
        .accessibilityLabel("transfr")
        .onAppear {
            startDate = Date()
            let delay = reduceMotion ? LaunchPhase.reduceHold : LaunchPhase.flyEnd
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { finish() }
        }
    }

    private func finish() {
        guard !didFinish else { return }
        didFinish = true
        onFinished()
    }
}

/// InputView publishes its "transfr" title frame under this key; RootView resolves
/// it and hands it to `LaunchView` as the fly-to-title target. First writer wins —
/// there is only ever the one title.
struct WordmarkAnchorKey: PreferenceKey {
    static let defaultValue: Anchor<CGRect>? = nil
    static func reduce(value: inout Anchor<CGRect>?, nextValue: () -> Anchor<CGRect>?) {
        value = value ?? nextValue()
    }
}

/// True while the cold-launch overlay is still playing (RootView drives it from
/// `showLaunch`). InputView reads it to keep its OWN "transfr" title hidden until the
/// flying launch mark lands — so during the fly the mark is the only wordmark on
/// screen (no doubling as the app reveals underneath), and the two swap at the
/// hand-off. Defaults to false, so the title is visible whenever there's no launch.
struct LaunchingKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues {
    var isLaunching: Bool {
        get { self[LaunchingKey.self] }
        set { self[LaunchingKey.self] = newValue }
    }
}

/// The brand wordmark as a title: the launch mark's END POSE (blue, green dot on
/// the t, red dot on the r), rendered tightly into its own bounds. It is the very
/// same `LaunchMark` the launch animation lands (`t == flyEnd` ⇒ fully written and
/// "landed" onto its frame), so when the flying mark settles onto this view the
/// hand-off is pixel-identical — same letterforms, stroke width, and dots — and the
/// crossfade shows no seam. Sized by `height`; width follows the mark's aspect.
struct Wordmark: View {
    var height: CGFloat = 40
    private var aspect: CGFloat {
        LaunchGeometry.wordmarkStageBounds.width / LaunchGeometry.wordmarkStageBounds.height
    }
    var body: some View {
        GeometryReader { geo in
            LaunchMark(t: LaunchPhase.flyEnd, targetRect: CGRect(origin: .zero, size: geo.size))
        }
        .frame(width: height * aspect, height: height)
        .accessibilityElement()
        .accessibilityLabel("transfr")
    }
}
