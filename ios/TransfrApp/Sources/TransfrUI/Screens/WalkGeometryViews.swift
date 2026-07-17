import SwiftUI
import TransfrCore

/// Projects a decoded `VizExport` (the keystone `core/viz_export.py` contract) into
/// the walk renderers. **One decode → three views** (Section / per-Level / 3D), so
/// they can never drift (DESIGN.md §13.3). Everything the export carries is in
/// local-ENU metres about `meta.origin*`; Z is `level × floor_height`, not surveyed
/// elevation, so a "level" is `round(z / floorHeight)`.
///
/// `WalkScene` does the once-per-load work (bounding box, level set, endpoints);
/// the three `Canvas` views below are pure projections of it.
struct WalkScene {
    let export: VizExport
    let floorHeight: CGFloat
    let levelsAsc: [Int]          // distinct levels, low → high
    let pathPoints: [Point3]
    let transitions: [VizExport.Transition]
    let found: Bool
    let startRef: String
    let endRef: String
    let startLevel: Int
    let endLevel: Int
    /// World XY bounds over ways + path — shared by every view so switching level
    /// (or rotating the 3D) never rescales the scene under you.
    let minX: CGFloat, maxX: CGFloat, minY: CGFloat, maxY: CGFloat

    init(_ e: VizExport) {
        export = e
        floorHeight = max(CGFloat(e.meta.floorHeightM), 0.1)
        pathPoints = e.path.points ?? []
        transitions = e.path.transitions ?? []
        found = e.path.found

        let fh = floorHeight
        func lvl(_ z: Float) -> Int { Int((CGFloat(z) / fh).rounded()) }

        // Prefer the declared level set; fall back to whatever the geometry touches.
        var levels = Set(e.meta.levelsPresent.map { Int(($0).rounded()) })
        if levels.isEmpty {
            for w in e.ways { for p in w.points { levels.insert(lvl(p.z)) } }
            for p in pathPoints { levels.insert(lvl(p.z)) }
        }
        levelsAsc = levels.sorted()

        startRef = e.path.endpoints?.start.ref ?? e.meta.ref1
        endRef   = e.path.endpoints?.end.ref ?? e.meta.ref2
        startLevel = lvl(e.path.endpoints?.start.xyz.z ?? pathPoints.first?.z ?? 0)
        endLevel   = lvl(e.path.endpoints?.end.xyz.z ?? pathPoints.last?.z ?? 0)

        var lo = CGPoint(x: CGFloat.greatestFiniteMagnitude, y: CGFloat.greatestFiniteMagnitude)
        var hi = CGPoint(x: -CGFloat.greatestFiniteMagnitude, y: -CGFloat.greatestFiniteMagnitude)
        func extend(_ p: Point3) {
            lo.x = min(lo.x, CGFloat(p.x)); hi.x = max(hi.x, CGFloat(p.x))
            lo.y = min(lo.y, CGFloat(p.y)); hi.y = max(hi.y, CGFloat(p.y))
        }
        for w in e.ways { for p in w.points { extend(p) } }
        for p in pathPoints { extend(p) }
        if lo.x > hi.x { lo = .zero; hi = CGPoint(x: 1, y: 1) }  // empty guard
        minX = lo.x; maxX = hi.x; minY = lo.y; maxY = hi.y
    }

    func level(of z: Float) -> Int { Int((CGFloat(z) / floorHeight).rounded()) }

    /// The deepest/highest levels the *path* actually visits (drives the section
    /// bands and the "Levels" stat).
    var pathLevels: [Int] {
        let ls = Set(pathPoints.map { level(of: $0.z) })
        return ls.isEmpty ? [startLevel] : ls.sorted()
    }

    static func label(forLevel lvl: Int) -> String {
        lvl == 0 ? "L0" : (lvl > 0 ? "L+\(lvl)" : "L−\(abs(lvl))")
    }

    /// Turn-by-turn derived from the real `transitions` + endpoints — replaces the
    /// synthesized copy the schematic uses. `boarding`, when present, sharpens the
    /// first step from "step off on Platform X" to *where* on it to step off.
    func turnByTurn(imperial: Bool = false, boarding: BoardingGuidance? = nil) -> [WalkStep] {
        let stepOff: WalkStep
        if let b = boarding, b.hasPosition, b.band != .low {
            stepOff = WalkStep(icon: "figure.walk", color: Theme.go,
                               title: "Step off toward \(BoardingCopy.end(b))",
                               sub: "Platform \(startRef) · saves up to \(Fmt.approxSaved(b.timeSavedS)) over the far end")
        } else {
            stepOff = WalkStep(icon: "figure.walk", color: Theme.go,
                               title: "Step off on Platform \(startRef)",
                               sub: "Level " + String(Self.label(forLevel: startLevel).dropFirst()))
        }
        var steps: [WalkStep] = [stepOff]
        for t in transitions {
            let toLvl = level(of: t.to.z)
            let up = t.to.z > t.from.z
            steps.append(WalkStep(
                icon: WalkConnector.icon(t.kind, up: up),
                color: WalkConnector.color(t.kind),
                title: WalkConnector.instruction(t.kind, up: up, to: Self.label(forLevel: toLvl)),
                sub: WalkConnector.label(t.kind)))
        }
        let arrive = WalkStep(icon: "checkmark", color: Theme.accent,
                              title: "Board on Platform \(endRef)",
                              sub: found
                                ? "\(Fmt.distance(export.path.walkingDistanceMeters, imperial: imperial)) · \(Fmt.walkTime(export.path.walkingTimeSeconds))"
                                : "these platforms aren't connected on the map")
        steps.append(arrive)
        return steps
    }
}

/// A turn-by-turn row, shared by the schematic and geometry paths.
struct WalkStep: Identifiable {
    let id = UUID()
    let icon: String
    let color: Color
    let title: String
    let sub: String
}

/// Maps a connector `kind` (way / transition) to its theme colour, legend label,
/// SF Symbol, and verb. `vertical` is core/'s catch-all for an unclassified level
/// change; we render it neutrally rather than claim a specific mode.
enum WalkConnector {
    static func color(_ kind: String) -> Color {
        switch kind {
        case "stairs":              return Theme.stair
        case "escalator":           return Theme.esc
        case "elevator", "lift":    return Theme.elev
        case "ramp":                return Theme.accent
        default:                    return Theme.elev   // "vertical" / unknown
        }
    }
    static func label(_ kind: String) -> String {
        switch kind {
        case "stairs": "Stairs"; case "escalator": "Escalator"
        case "elevator", "lift": "Elevator"; case "ramp": "Ramp"
        default: "Level change"
        }
    }
    static func verb(_ kind: String) -> String {
        switch kind {
        case "stairs": "Stairs"; case "escalator": "Escalator"
        case "elevator", "lift": "Lift"; case "ramp": "Ramp"
        default: "Change level"
        }
    }
    /// Action-forward turn-by-turn instruction: leads with the verb ("Take the
    /// stairs down to L−1") rather than naming the connector.
    static func instruction(_ kind: String, up: Bool, to label: String) -> String {
        let dir = up ? "up" : "down"
        switch kind {
        case "stairs":            return "Take the stairs \(dir) to \(label)"
        case "escalator":         return "Ride the escalator \(dir) to \(label)"
        case "elevator", "lift":  return "Take the lift \(dir) to \(label)"
        case "ramp":              return "Follow the ramp \(dir) to \(label)"
        default:                  return "Change level \(dir) to \(label)"
        }
    }
    static func icon(_ kind: String, up: Bool) -> String {
        switch kind {
        case "stairs":            return "figure.stairs"
        case "escalator":         return up ? "arrow.up.forward" : "arrow.down.forward"
        case "elevator", "lift":  return "arrow.up.arrow.down"
        case "ramp":              return up ? "arrow.up.right" : "arrow.down.right"
        default:                  return "arrow.up.arrow.down"
        }
    }
}

// MARK: - Section (longitudinal elevation profile)

/// The real section: horizontal axis is distance walked, vertical axis is level.
/// Flat stretches sit on their platform band; transitions are the risers, coloured
/// by kind. Generalises to any station (not the schematic's fixed two-level shape).
struct SectionGeometryCanvas: View {
    let scene: WalkScene

    var body: some View {
        Canvas { ctx, size in
            guard scene.found, scene.pathPoints.count >= 2 else {
                drawUnavailable(ctx, size, scene.found ? "No path geometry" : "Platforms not connected")
                return
            }
            let w = size.width, h = size.height
            let padX: CGFloat = 30, padY: CGFloat = 26
            let pts = scene.pathPoints

            // Cumulative horizontal distance along the path.
            var cum: [CGFloat] = [0]
            for i in 1..<pts.count {
                let dx = CGFloat(pts[i].x - pts[i-1].x), dy = CGFloat(pts[i].y - pts[i-1].y)
                cum.append(cum[i-1] + (dx*dx + dy*dy).squareRoot())
            }
            let total = max(cum.last ?? 1, 0.001)

            let levels = scene.pathLevels
            let zLo = CGFloat(levels.min() ?? 0) * scene.floorHeight
            let zHi = CGFloat(levels.max() ?? 0) * scene.floorHeight
            let zSpan = max(zHi - zLo, 0.001)
            let flat = (zHi - zLo) < 0.001

            func mapX(_ d: CGFloat) -> CGFloat { padX + (d / total) * (w - 2*padX) }
            func mapY(_ z: CGFloat) -> CGFloat {
                flat ? h * 0.5 : padY + (zHi - z) / zSpan * (h - 2*padY)
            }

            // Level bands + labels.
            for lvl in levels {
                let y = mapY(CGFloat(lvl) * scene.floorHeight)
                var line = Path(); line.move(to: CGPoint(x: padX, y: y)); line.addLine(to: CGPoint(x: w - padX, y: y))
                ctx.stroke(line, with: .color(Theme.line), style: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                ctx.drawGeoText(WalkScene.label(forLevel: lvl),
                                .system(size: 9, weight: .bold, design: .monospaced),
                                Theme.ink3, at: CGPoint(x: 14, y: y), anchor: .leading)
            }

            // Transition lookup so each riser is coloured by its real kind.
            let riserKinds = transitionKinds(scene)

            // Path, segment by segment.
            for i in 1..<pts.count {
                let a = CGPoint(x: mapX(cum[i-1]), y: mapY(CGFloat(pts[i-1].z)))
                let b = CGPoint(x: mapX(cum[i]),   y: mapY(CGFloat(pts[i].z)))
                var seg = Path(); seg.move(to: a); seg.addLine(to: b)
                let riser = abs(pts[i].z - pts[i-1].z) > 0.4
                if riser {
                    let kind = riserKinds[riserKey(pts[i-1], pts[i])] ?? "vertical"
                    ctx.stroke(seg, with: .color(WalkConnector.color(kind)),
                               style: StrokeStyle(lineWidth: 6, lineCap: .round, lineJoin: .round))
                } else {
                    ctx.stroke(seg, with: .color(Theme.accent),
                               style: StrokeStyle(lineWidth: 4.5, lineCap: .round, lineJoin: .round))
                }
            }

            // Endpoints.
            endpoint(ctx, CGPoint(x: mapX(0), y: mapY(CGFloat(pts.first!.z))), Theme.go)
            endpoint(ctx, CGPoint(x: mapX(total), y: mapY(CGFloat(pts.last!.z))), Theme.accent)
            ctx.drawGeoText("Pl \(scene.startRef)", .system(size: 10, weight: .bold, design: .monospaced),
                            Theme.ink, at: CGPoint(x: mapX(0) + 2, y: mapY(CGFloat(pts.first!.z)) - 14), anchor: .leading)
            ctx.drawGeoText("Pl \(scene.endRef)", .system(size: 10, weight: .bold, design: .monospaced),
                            Theme.ink, at: CGPoint(x: mapX(total) - 2, y: mapY(CGFloat(pts.last!.z)) - 14), anchor: .trailing)
        }
    }
}

// MARK: - Levels (per-floor plan)

/// A top-down floor plan for one level: platforms as slabs, walkways/connectors as
/// lines, and the portion of the path on that level, with markers where it drops or
/// climbs to another floor.
struct PlanGeometryCanvas: View {
    let scene: WalkScene
    let level: Int

    var body: some View {
        Canvas { ctx, size in
            let fit = PlanFit(scene: scene, size: size, pad: 18)

            // Context ways on this level.
            for way in scene.export.ways where wayTouches(way, level: level, scene: scene) {
                guard way.points.count >= 2 else { continue }
                if way.walkRelevant == false { continue }   // hide connectors the walk doesn't use
                var p = Path()
                p.move(to: fit.map(way.points[0]))
                for pt in way.points.dropFirst() { p.addLine(to: fit.map(pt)) }
                switch way.kind {
                case "platform":
                    ctx.stroke(p, with: .color(Theme.panel3), style: StrokeStyle(lineWidth: 9, lineCap: .round, lineJoin: .round))
                    ctx.stroke(p, with: .color(Theme.line), style: StrokeStyle(lineWidth: 1))
                case "walkway":
                    ctx.stroke(p, with: .color(Theme.ink3.opacity(0.35)), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                case "stairs", "escalator", "ramp", "elevator":
                    ctx.stroke(p, with: .color(WalkConnector.color(way.kind)), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                default:
                    ctx.stroke(p, with: .color(Theme.line2), style: StrokeStyle(lineWidth: 1))
                }
            }

            guard scene.found, scene.pathPoints.count >= 2 else {
                if !scene.found { drawUnavailable(ctx, size, "Platforms not connected") }
                return
            }

            // Path segments whose both ends are on this level.
            let pts = scene.pathPoints
            var path = Path()
            var started = false
            for i in 1..<pts.count {
                guard scene.level(of: pts[i-1].z) == level, scene.level(of: pts[i].z) == level else { started = false; continue }
                if !started { path.move(to: fit.map(pts[i-1])); started = true }
                path.addLine(to: fit.map(pts[i]))
            }
            ctx.stroke(path, with: .color(Theme.accent), style: StrokeStyle(lineWidth: 4, lineCap: .round, lineJoin: .round))

            // Where the path enters/leaves this level.
            for t in scene.transitions {
                let fromL = scene.level(of: t.from.z), toL = scene.level(of: t.to.z)
                if fromL == level || toL == level {
                    let side = fromL == level ? t.from : t.to
                    let up = toL > fromL
                    let onThisLevelGoing = (fromL == level) ? (up ? "▲" : "▼") : "•"
                    connectorMark(ctx, fit.map(side), WalkConnector.color(t.kind), onThisLevelGoing)
                }
            }

            // Endpoints if they sit on this level.
            if scene.startLevel == level, let s = pts.first { endpoint(ctx, fit.map(s), Theme.go) }
            if scene.endLevel == level, let e = pts.last { endpoint(ctx, fit.map(e), Theme.accent) }
        }
    }
}

// MARK: - 3D (draggable axonometric)

/// The interactive exploded-floor 3D of a station — drag to rotate, pinch or the
/// +/− buttons to zoom. Platforms are labelled and lifted to their real floor and
/// level tabs run down the left edge. Default (walk) mode draws the route and only
/// the connectors the walk uses; `browse` mode (the station map) shows every
/// connector and no route. One renderer, used by the walk views and the map.
struct IsoGeometryCanvas: View {
    let scene: WalkScene
    var browse: Bool = false
    @State private var yaw: Double = 0.5
    @GestureState private var twist: Double = 0
    @State private var zoom: CGFloat = 1
    @GestureState private var pinch: CGFloat = 1
    @State private var pan: CGSize = .zero
    @GestureState private var dragPan: CGSize = .zero
    @State private var focusLevel: Int?

    var body: some View {
        let liveYaw = yaw + twist
        let liveZoom = min(max(zoom * pinch, 0.5), 8)
        let livePan = CGSize(width: pan.width + dragPan.width, height: pan.height + dragPan.height)
        let focus = focusLevel
        Canvas { ctx, size in
            let iso = IsoFit(scene: scene, size: size, angle: liveYaw, zoom: liveZoom, pan: livePan, pad: 24)
            drawLevelTabs(ctx, iso)
            func alpha(_ lvl: Int) -> Double { focus == nil || focus == lvl ? 1 : 0.10 }

            // Ways, drawn low floors first so upper floors overlay. A selected level
            // dims the rest.
            let ways = scene.export.ways.sorted { wayLevel($0, scene) < wayLevel($1, scene) }
            for way in ways where way.points.count >= 2 {
                if !browse && way.walkRelevant == false { continue }   // walk view: only the connectors it uses
                let a = alpha(wayLevel(way, scene))
                var p = Path()
                p.move(to: iso.map(way.points[0]))
                for pt in way.points.dropFirst() { p.addLine(to: iso.map(pt)) }
                switch way.kind {
                case "platform":
                    ctx.stroke(p, with: .color(Theme.panel3.opacity(a)), style: StrokeStyle(lineWidth: 6, lineCap: .round, lineJoin: .round))
                case "walkway":
                    ctx.stroke(p, with: .color(Theme.ink3.opacity(0.28 * a)), style: StrokeStyle(lineWidth: 1.5))
                case "stairs", "escalator", "ramp", "elevator":
                    ctx.stroke(p, with: .color(WalkConnector.color(way.kind).opacity(0.9 * a)), style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                default:
                    ctx.stroke(p, with: .color(Theme.line2.opacity(a)), style: StrokeStyle(lineWidth: 1))
                }
            }

            // Every platform the export carries, marked with its ref and lifted to
            // its floor — not just the two the walk connects. Hidden on other floors
            // when a level is isolated.
            for way in ways where way.kind == "platform" {
                guard let ref = way.ref, !way.points.isEmpty else { continue }
                let lvl = way.level ?? wayLevel(way, scene)
                if focus != nil && focus != lvl { continue }
                let n = CGFloat(way.points.count)
                let c = Point3(
                    x: Float(way.points.reduce(0) { $0 + CGFloat($1.x) } / n),
                    y: Float(way.points.reduce(0) { $0 + CGFloat($1.y) } / n),
                    z: Float(way.points.reduce(0) { $0 + CGFloat($1.z) } / n))
                platformTag(ctx, iso.map(c), ref)
            }

            // Route + its level changes (walk mode only; browse shows the station).
            guard !browse else { return }
            guard scene.found, scene.pathPoints.count >= 2 else {
                if !scene.found { drawUnavailable(ctx, size, "Platforms not connected") }
                return
            }
            let pts = scene.pathPoints
            var route = Path()
            route.move(to: iso.map(pts[0]))
            for pt in pts.dropFirst() { route.addLine(to: iso.map(pt)) }
            ctx.stroke(route, with: .color(Theme.accent), style: StrokeStyle(lineWidth: 3.5, lineCap: .round, lineJoin: .round))
            for t in scene.transitions {   // colour each stair / escalator / lift riser on the path
                var r = Path(); r.move(to: iso.map(t.from)); r.addLine(to: iso.map(t.to))
                ctx.stroke(r, with: .color(WalkConnector.color(t.kind)), style: StrokeStyle(lineWidth: 5, lineCap: .round))
            }
            endpoint(ctx, iso.map(pts.first!), Theme.go, r: 5)
            endpoint(ctx, iso.map(pts.last!), Theme.accent, r: 5)
        }
        .contentShape(Rectangle())
        // One-finger drag pans; pinch zooms; two-finger twist rotates. All three
        // combine, the way a map does.
        .gesture(
            DragGesture(minimumDistance: 0)
                .updating($dragPan) { v, s, _ in s = v.translation }
                .onEnded { pan.width += $0.translation.width; pan.height += $0.translation.height }
        )
        .simultaneousGesture(
            MagnificationGesture()
                .updating($pinch) { v, s, _ in s = v }
                .onEnded { zoom = min(max(zoom * $0, 0.5), 8) }
        )
        .simultaneousGesture(
            RotationGesture()
                .updating($twist) { v, s, _ in s = v.radians }
                .onEnded { yaw += $0.radians }
        )
        .overlay(alignment: .topTrailing) { controls }
        .overlay(alignment: .bottom) { levelChips }
        .overlay(alignment: .bottomLeading) {
            Label("drag · pan   pinch · zoom   twist · rotate", systemImage: "hand.draw")
                .font(.system(size: 9.5)).foregroundStyle(Theme.ink3).padding(6)
        }
    }

    private var controls: some View {
        VStack(spacing: 6) {
            ctlButton("plus") { zoom = min(zoom * 1.3, 8) }
            ctlButton("minus") { zoom = max(zoom / 1.3, 0.5) }
            ctlButton("arrow.counterclockwise") { yaw = 0.5; zoom = 1; pan = .zero; focusLevel = nil }
        }
        .padding(8)
    }
    private func ctlButton(_ icon: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 12, weight: .semibold))
                .frame(width: 30, height: 30)
                .background(Theme.panel, in: RoundedRectangle(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(Theme.line, lineWidth: 1))
        }
        .foregroundStyle(Theme.ink2).buttonStyle(.plain)
    }

    /// Tap a floor to isolate it (others dim); tap again or "All" to restore.
    @ViewBuilder
    private var levelChips: some View {
        if scene.levelsAsc.count > 1 {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    levelChip("All", on: focusLevel == nil) { focusLevel = nil }
                    ForEach(scene.levelsAsc.reversed(), id: \.self) { lvl in
                        levelChip(WalkScene.label(forLevel: lvl), on: focusLevel == lvl) {
                            focusLevel = (focusLevel == lvl ? nil : lvl)
                        }
                    }
                }
                .padding(.horizontal, 10)
            }
            .padding(.bottom, 6)
        }
    }
    private func levelChip(_ text: String, on: Bool, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(text).font(.system(size: 11, weight: .semibold, design: .monospaced))
                .foregroundStyle(on ? Color.white : Theme.ink2)
                .padding(.horizontal, 9).padding(.vertical, 5)
                .background(RoundedRectangle(cornerRadius: 8).fill(on ? Theme.accent : Theme.panel2))
        }
        .buttonStyle(.plain)
    }

    /// Level reference tabs down the left edge (L+2 … L−2), each at its floor's
    /// projected height so you can read which band is which.
    private func drawLevelTabs(_ ctx: GraphicsContext, _ iso: IsoFit) {
        for lvl in scene.levelsAsc {
            let ref = iso.map(Point3(x: Float(scene.minX),
                                     y: Float((scene.minY + scene.maxY) / 2),
                                     z: Float(CGFloat(lvl) * scene.floorHeight)))
            var t = ctx.resolve(Text(WalkScene.label(forLevel: lvl))
                .font(.system(size: 9, weight: .bold, design: .monospaced)))
            t.shading = .color(Theme.ink3)
            ctx.draw(t, at: CGPoint(x: 12, y: ref.y), anchor: .leading)
        }
    }
}

// MARK: - Projections

/// Uniform aspect-fit of the scene's world XY box into a rect, Y flipped (world up
/// → screen down). Shared across levels so the plan never jumps when you switch.
private struct PlanFit {
    let scale: CGFloat, offX: CGFloat, offY: CGFloat, minX: CGFloat, maxY: CGFloat
    init(scene: WalkScene, size: CGSize, pad: CGFloat) {
        let spanX = max(scene.maxX - scene.minX, 0.001)
        let spanY = max(scene.maxY - scene.minY, 0.001)
        scale = min((size.width - 2*pad) / spanX, (size.height - 2*pad) / spanY)
        offX = pad + ((size.width - 2*pad) - spanX*scale) / 2
        offY = pad + ((size.height - 2*pad) - spanY*scale) / 2
        minX = scene.minX; maxY = scene.maxY
    }
    func map(_ p: Point3) -> CGPoint {
        CGPoint(x: offX + (CGFloat(p.x) - minX) * scale,
                y: offY + (maxY - CGFloat(p.y)) * scale)
    }
}

/// Exploded-floor axonometric projection. XY is normalised to a nominal size (so
/// stations of any footprint look alike), rotated by `angle`, iso-projected, then
/// each level is lifted by a fixed screen gap. The fit is computed from the world
/// box corners at the extreme levels, so nothing clips at any rotation.
private struct IsoFit {
    let angle: Double
    let cx: CGFloat, cy: CGFloat, norm: CGFloat, floorHeight: CGFloat
    let scale: CGFloat, scx: CGFloat, scy: CGFloat, qcx: CGFloat, qcy: CGFloat
    let pan: CGSize
    let levelUnit: CGFloat = 20

    init(scene: WalkScene, size: CGSize, angle: Double, zoom: CGFloat, pan: CGSize = .zero, pad: CGFloat) {
        self.angle = angle
        self.pan = pan
        cx = (scene.minX + scene.maxX) / 2
        cy = (scene.minY + scene.maxY) / 2
        let diag = max((scene.maxX - scene.minX).magnitude, (scene.maxY - scene.minY).magnitude)
        norm = 100 / max(diag, 0.001)
        floorHeight = scene.floorHeight
        let lu = levelUnit

        // Pre-project the 8 bounding corners (XY box × level range) to find extents.
        let loL = CGFloat(scene.levelsAsc.first ?? 0), hiL = CGFloat(scene.levelsAsc.last ?? 0)
        var lo = CGPoint(x: CGFloat.greatestFiniteMagnitude, y: CGFloat.greatestFiniteMagnitude)
        var hi = CGPoint(x: -CGFloat.greatestFiniteMagnitude, y: -CGFloat.greatestFiniteMagnitude)
        for x in [scene.minX, scene.maxX] {
            for y in [scene.minY, scene.maxY] {
                for l in [loL, hiL] {
                    let q = Self.project(x: x, y: y, level: l, cx: cx, cy: cy, norm: norm, angle: angle, levelUnit: lu)
                    lo.x = min(lo.x, q.x); hi.x = max(hi.x, q.x)
                    lo.y = min(lo.y, q.y); hi.y = max(hi.y, q.y)
                }
            }
        }
        // Fit the whole box, then zoom about the centre (the iso projection is
        // affine, so the box centre projects to the centre of the projected box).
        let spanX = max(hi.x - lo.x, 0.001), spanY = max(hi.y - lo.y, 0.001)
        scale = min((size.width - 2*pad) / spanX, (size.height - 2*pad) / spanY) * zoom
        scx = size.width / 2; scy = size.height / 2
        let qc = Self.project(x: cx, y: cy, level: (loL + hiL) / 2, cx: cx, cy: cy, norm: norm, angle: angle, levelUnit: lu)
        qcx = qc.x; qcy = qc.y
    }

    /// Rotate normalised XY, iso-project, and lift by level. Returns pre-fit coords.
    private static func project(x: CGFloat, y: CGFloat, level: CGFloat,
                                cx: CGFloat, cy: CGFloat, norm: CGFloat,
                                angle: Double, levelUnit: CGFloat) -> CGPoint {
        let nx = (x - cx) * norm, ny = (y - cy) * norm
        let c = CGFloat(cos(angle)), s = CGFloat(sin(angle))
        let rx = nx * c - ny * s, ry = nx * s + ny * c
        let cos30: CGFloat = 0.8660254, sin30: CGFloat = 0.5
        return CGPoint(x: (rx - ry) * cos30,
                       y: (rx + ry) * sin30 - level * levelUnit)
    }

    func map(_ p: Point3) -> CGPoint {
        let level = (CGFloat(p.z) / floorHeight)
        let q = Self.project(x: CGFloat(p.x), y: CGFloat(p.y), level: level,
                             cx: cx, cy: cy, norm: norm, angle: angle, levelUnit: levelUnit)
        return CGPoint(x: scx + pan.width + (q.x - qcx) * scale,
                       y: scy + pan.height + (q.y - qcy) * scale)
    }
}

// MARK: - Shared drawing helpers

/// A way belongs to a level if any of its points sit on it (connectors span two,
/// so they show on both floors they link).
private func wayTouches(_ way: VizExport.Way, level: Int, scene: WalkScene) -> Bool {
    way.points.contains { scene.level(of: $0.z) == level }
}
private func wayLevel(_ way: VizExport.Way, _ scene: WalkScene) -> Int {
    way.points.map { scene.level(of: $0.z) }.min() ?? 0
}

private func riserKey(_ a: Point3, _ b: Point3) -> String {
    func r(_ v: Float) -> Int { Int((v * 2).rounded()) }   // ~0.5 m buckets
    return "\(r(a.x)),\(r(a.y)),\(r(a.z))>\(r(b.x)),\(r(b.y)),\(r(b.z))"
}
private func transitionKinds(_ scene: WalkScene) -> [String: String] {
    var out: [String: String] = [:]
    for t in scene.transitions {
        out[riserKey(t.from, t.to)] = t.kind
        out[riserKey(t.to, t.from)] = t.kind   // path may traverse either way
    }
    return out
}

private func endpoint(_ ctx: GraphicsContext, _ p: CGPoint, _ c: Color, r: CGFloat = 6) {
    ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)), with: .color(c))
    ctx.stroke(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)),
               with: .color(Theme.paper), lineWidth: 1.5)
}

/// A small ref chip drawn at a platform's centroid, so every platform on the
/// exploded model reads as "Platform N", not just the two the walk connects.
private func platformTag(_ ctx: GraphicsContext, _ p: CGPoint, _ ref: String) {
    let font = Font.system(size: 10, weight: .bold, design: .monospaced)
    var text = ctx.resolve(Text(ref).font(font))
    text.shading = .color(Theme.ink)
    let ts = text.measure(in: CGSize(width: 200, height: 40))
    let w = ts.width + 8, h: CGFloat = 15
    let rect = CGRect(x: p.x - w / 2, y: p.y - h / 2, width: w, height: h)
    ctx.fill(Path(roundedRect: rect, cornerRadius: 4), with: .color(Theme.panel))
    ctx.stroke(Path(roundedRect: rect, cornerRadius: 4), with: .color(Theme.line), lineWidth: 1)
    ctx.draw(text, at: p, anchor: .center)
}

private func connectorMark(_ ctx: GraphicsContext, _ p: CGPoint, _ c: Color, _ glyph: String) {
    let r: CGFloat = 8
    ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)), with: .color(c))
    ctx.drawGeoText(glyph, .system(size: 9, weight: .bold), .white, at: p, anchor: .center)
}

private func drawUnavailable(_ ctx: GraphicsContext, _ size: CGSize, _ text: String) {
    ctx.drawGeoText(text, .system(size: 12, weight: .medium), Theme.ink3,
                    at: CGPoint(x: size.width/2, y: size.height/2), anchor: .center)
}

private extension GraphicsContext {
    /// Draw shaded text (resolve, then colour — `Text.foregroundStyle` returns a
    /// View that `draw(_:at:)` won't take).
    func drawGeoText(_ string: String, _ font: Font, _ color: Color,
                     at point: CGPoint, anchor: UnitPoint = .center) {
        var resolved = resolve(Text(string).font(font))
        resolved.shading = .color(color)
        draw(resolved, at: point, anchor: anchor)
    }
}
