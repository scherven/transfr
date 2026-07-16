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
    /// World XY bounds — framed on the **path** (+ a little breathing room), so
    /// the walk fills every view. Shared by all of them so switching level (or
    /// rotating the 3D) never rescales the scene under you.
    let minX: CGFloat, maxX: CGFloat, minY: CGFloat, maxY: CGFloat
    /// The platform slab the walk starts / ends on — the nearest mapped `platform`
    /// way to each endpoint. The one piece of the station's mapped web the plans
    /// keep (faint, for orientation); everything else the search merely touched is
    /// dropped so the walk itself reads (mirrors `core/viz/viz_render.py`).
    let startPlatform: VizExport.Way?
    let endPlatform: VizExport.Way?

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

        // Frame tightly on the PATH (+ margin) — the walk fills the view. We no
        // longer draw the station's full mapped web, so framing on every way (as
        // this once did) would strand a small path in a huge empty frame. Mirrors
        // core/viz/viz_render.py's `_focus_window` (path bbox + margin).
        var lo = CGPoint(x: CGFloat.greatestFiniteMagnitude, y: CGFloat.greatestFiniteMagnitude)
        var hi = CGPoint(x: -CGFloat.greatestFiniteMagnitude, y: -CGFloat.greatestFiniteMagnitude)
        func extend(_ p: Point3) {
            lo.x = min(lo.x, CGFloat(p.x)); hi.x = max(hi.x, CGFloat(p.x))
            lo.y = min(lo.y, CGFloat(p.y)); hi.y = max(hi.y, CGFloat(p.y))
        }
        if !pathPoints.isEmpty { for p in pathPoints { extend(p) } }
        else { for w in e.ways { for p in w.points { extend(p) } } }   // no path: fall back to the web
        if lo.x > hi.x { lo = .zero; hi = CGPoint(x: 1, y: 1) }         // empty guard
        let margin = max(hi.x - lo.x, hi.y - lo.y, 1) * 0.08 + 3        // world-metre breathing room
        minX = lo.x - margin; maxX = hi.x + margin
        minY = lo.y - margin; maxY = hi.y + margin

        // The slabs you step off / board on — nearest platform way to each
        // endpoint, on that endpoint's floor. The only mapped-web geometry kept.
        let startXYZ = e.path.endpoints?.start.xyz ?? pathPoints.first ?? Point3(x: 0, y: 0, z: 0)
        let endXYZ   = e.path.endpoints?.end.xyz   ?? pathPoints.last  ?? Point3(x: 0, y: 0, z: 0)
        startPlatform = found ? Self.nearestPlatform(e.ways, to: startXYZ, level: startLevel, floorHeight: fh) : nil
        endPlatform   = found ? Self.nearestPlatform(e.ways, to: endXYZ,   level: endLevel,   floorHeight: fh) : nil
    }

    /// Nearest mapped `platform` way to a point, among platforms touching `level`
    /// — the slab the walk starts/ends on, for a faint orientation cue.
    private static func nearestPlatform(_ ways: [VizExport.Way], to p: Point3,
                                        level lvl: Int, floorHeight fh: CGFloat) -> VizExport.Way? {
        func L(_ z: Float) -> Int { Int((CGFloat(z) / fh).rounded()) }
        var best: VizExport.Way?
        var bestD = Float.greatestFiniteMagnitude
        for w in ways where w.kind == "platform" && w.points.contains(where: { L($0.z) == lvl }) {
            var d = Float.greatestFiniteMagnitude
            for q in w.points { let dx = q.x - p.x, dy = q.y - p.y; d = min(d, dx*dx + dy*dy) }
            if d < bestD { bestD = d; best = w }
        }
        return best
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

/// A top-down floor plan for one level. Deliberately spare (like
/// `core/viz/viz_render.py`): only the part of the route on this floor, the
/// slab(s) it starts/ends on, and — drawn large and labelled — every point where
/// it changes floor. The station's other platforms, walkways and stairs (dozens
/// to hundreds of context ways) are NOT drawn; they only buried the walk.
struct PlanGeometryCanvas: View {
    let scene: WalkScene
    let level: Int

    var body: some View {
        Canvas { ctx, size in
            let fit = PlanFit(scene: scene, size: size, pad: 24)

            guard scene.found, scene.pathPoints.count >= 2 else {
                drawUnavailable(ctx, size, scene.found ? "No path geometry" : "Platforms not connected")
                return
            }

            // Faint orientation: only the platform slab(s) the walk begins/ends on
            // this floor — not the station's other platforms.
            if scene.startLevel == level, let w = scene.startPlatform { drawPlatformSlab(ctx, w, fit) }
            if scene.endLevel == level, let w = scene.endPlatform, w.id != scene.startPlatform?.id {
                drawPlatformSlab(ctx, w, fit)
            }

            // The route, only where both ends of a segment sit on this floor.
            let pts = scene.pathPoints
            var route = Path()
            var started = false
            for i in 1..<pts.count {
                guard scene.level(of: pts[i-1].z) == level, scene.level(of: pts[i].z) == level else { started = false; continue }
                if !started { route.move(to: fit.map(pts[i-1])); started = true }
                route.addLine(to: fit.map(pts[i]))
            }
            ctx.stroke(route, with: .color(Theme.accent),
                       style: StrokeStyle(lineWidth: 4.5, lineCap: .round, lineJoin: .round))

            // Endpoints on this floor, with their platform ref.
            if scene.startLevel == level, let s = pts.first {
                endpoint(ctx, fit.map(s), Theme.go)
                drawChip(ctx, "Pl \(scene.startRef)", center: CGPoint(x: fit.map(s).x, y: fit.map(s).y - 16),
                         textColor: .white, fill: Theme.go)
            }
            if scene.endLevel == level, let e = pts.last {
                endpoint(ctx, fit.map(e), Theme.accent)
                drawChip(ctx, "Pl \(scene.endRef)", center: CGPoint(x: fit.map(e).x, y: fit.map(e).y - 16),
                         textColor: .white, fill: Theme.accent)
            }

            // The decision this view exists to answer: every place the route drops
            // or climbs to another floor, drawn big and directional and labelled by
            // connector + destination floor (from THIS floor's point of view).
            var marks: [(CGPoint, String, Bool, Int)] = []
            for t in scene.transitions {
                let fromL = scene.level(of: t.from.z), toL = scene.level(of: t.to.z)
                guard fromL != toL, fromL == level || toL == level else { continue }
                let here  = (fromL == level) ? t.from : t.to
                let other = (fromL == level) ? toL : fromL
                marks.append((fit.map(here), t.kind, other > level, other))
            }
            drawTransitionMarks(ctx, marks)

            // Which floor you're looking at (top-left, always on top).
            drawChip(ctx, WalkScene.label(forLevel: level), center: CGPoint(x: 34, y: 18),
                     textColor: Theme.ink2, fill: Theme.panel3, weight: .heavy)
        }
    }
}

// MARK: - 3D (draggable axonometric)

/// An exploded-floor axonometric of the walk — the "3D" renderer, drawn straight
/// from the same export (no WebView/SceneKit needed). Drag to rotate.
///
/// Like `core/viz/viz_render.py`, it draws only the walk: faint reference planes
/// at the floors it uses, the route, and each floor-change as ONE riser coloured
/// by the connector you take. It no longer projects the station's whole mapped
/// web — those context stairs/escalators, each spanning two floors, exploded into
/// a forest of near-vertical lines that buried the route.
struct IsoGeometryCanvas: View {
    let scene: WalkScene
    @State private var rotation: Double = 0.5   // start slightly turned so it reads 3D

    var body: some View {
        Canvas { ctx, size in
            let iso = IsoFit(scene: scene, size: size, angle: rotation, pad: 26)

            // Faint floor reference planes at the levels the walk uses, low → high
            // so upper floors overlay — the vertical scaffold that makes the
            // level-stack legible.
            for lvl in scene.pathLevels { drawLevelPlane(ctx, level: lvl, scene: scene, iso: iso) }

            guard scene.found, scene.pathPoints.count >= 2 else {
                if !scene.found { drawUnavailable(ctx, size, "Platforms not connected") }
                return
            }

            // The route.
            let pts = scene.pathPoints
            var route = Path()
            route.move(to: iso.map(pts[0]))
            for pt in pts.dropFirst() { route.addLine(to: iso.map(pt)) }
            ctx.stroke(route, with: .color(Theme.accent), style: StrokeStyle(lineWidth: 3.5, lineCap: .round, lineJoin: .round))

            // Each level change as one riser in its connector colour, over the
            // route — so "which connector between floors" reads (the plain route is
            // otherwise a single colour through a vertical move).
            for t in scene.transitions {
                var riser = Path(); riser.move(to: iso.map(t.from)); riser.addLine(to: iso.map(t.to))
                ctx.stroke(riser, with: .color(WalkConnector.color(t.kind)),
                           style: StrokeStyle(lineWidth: 5.5, lineCap: .round, lineJoin: .round))
            }

            endpoint(ctx, iso.map(pts.first!), Theme.go, r: 5)
            endpoint(ctx, iso.map(pts.last!), Theme.accent, r: 5)
            ctx.drawGeoText("Pl \(scene.startRef)", .system(size: 10, weight: .bold, design: .monospaced),
                            Theme.ink, at: CGPoint(x: iso.map(pts.first!).x, y: iso.map(pts.first!).y - 12), anchor: .center)
            ctx.drawGeoText("Pl \(scene.endRef)", .system(size: 10, weight: .bold, design: .monospaced),
                            Theme.ink, at: CGPoint(x: iso.map(pts.last!).x, y: iso.map(pts.last!).y - 12), anchor: .center)
        }
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { rotation = $0.translation.width / 90 }
        )
        .overlay(alignment: .bottomTrailing) {
            Label("drag to rotate", systemImage: "hand.draw")
                .font(.system(size: 10)).foregroundStyle(Theme.ink3)
                .padding(6)
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
    let cx: CGFloat, cy: CGFloat, norm: CGFloat, angle: Double, floorHeight: CGFloat
    let scale: CGFloat, offX: CGFloat, offY: CGFloat, ix0: CGFloat, iy0: CGFloat
    let levelUnit: CGFloat = 20

    init(scene: WalkScene, size: CGSize, angle: Double, pad: CGFloat) {
        cx = (scene.minX + scene.maxX) / 2
        cy = (scene.minY + scene.maxY) / 2
        let diag = max((scene.maxX - scene.minX).magnitude, (scene.maxY - scene.minY).magnitude)
        norm = 100 / max(diag, 0.001)
        self.angle = angle
        floorHeight = scene.floorHeight
        let lu = levelUnit

        // Pre-project the 8 bounding corners (XY box × level range) to find extents.
        // Range is the levels the walk actually visits — the only ones now drawn —
        // so the floor stack fills the frame with no empty floors above/below.
        let loL = CGFloat(scene.pathLevels.first ?? 0), hiL = CGFloat(scene.pathLevels.last ?? 0)
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
        let spanX = max(hi.x - lo.x, 0.001), spanY = max(hi.y - lo.y, 0.001)
        scale = min((size.width - 2*pad) / spanX, (size.height - 2*pad) / spanY)
        offX = pad + ((size.width - 2*pad) - spanX*scale) / 2
        offY = pad + ((size.height - 2*pad) - spanY*scale) / 2
        ix0 = lo.x; iy0 = lo.y
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
        return CGPoint(x: offX + (q.x - ix0) * scale, y: offY + (q.y - iy0) * scale)
    }
}

// MARK: - Shared drawing helpers

/// A single platform slab (a `platform` way), faint — the "which slab am I on"
/// orientation the plans keep. Fill-thick stroke + a hairline edge.
private func drawPlatformSlab(_ ctx: GraphicsContext, _ way: VizExport.Way, _ fit: PlanFit) {
    guard way.points.count >= 2 else { return }
    var p = Path(); p.move(to: fit.map(way.points[0]))
    for pt in way.points.dropFirst() { p.addLine(to: fit.map(pt)) }
    ctx.stroke(p, with: .color(Theme.panel3), style: StrokeStyle(lineWidth: 11, lineCap: .round, lineJoin: .round))
    ctx.stroke(p, with: .color(Theme.line), style: StrokeStyle(lineWidth: 1))
}

/// A faint labelled reference plane for one floor in the 3D view — the XY frame
/// box drawn at that level's height, so the exploded stack reads. The floor label
/// stacks up the left edge like numbers up a shaft.
private func drawLevelPlane(_ ctx: GraphicsContext, level lvl: Int, scene: WalkScene, iso: IsoFit) {
    let z = Float(CGFloat(lvl) * scene.floorHeight)
    let corners = [
        Point3(x: Float(scene.minX), y: Float(scene.minY), z: z),
        Point3(x: Float(scene.maxX), y: Float(scene.minY), z: z),
        Point3(x: Float(scene.maxX), y: Float(scene.maxY), z: z),
        Point3(x: Float(scene.minX), y: Float(scene.maxY), z: z),
    ].map { iso.map($0) }
    var quad = Path(); quad.move(to: corners[0])
    for c in corners.dropFirst() { quad.addLine(to: c) }
    quad.closeSubpath()
    ctx.fill(quad, with: .color(Theme.ink3.opacity(0.05)))
    ctx.stroke(quad, with: .color(Theme.line), lineWidth: 1)
    if let left = corners.min(by: { $0.x < $1.x }) {
        ctx.drawGeoText(WalkScene.label(forLevel: lvl),
                        .system(size: 10, weight: .bold, design: .monospaced),
                        Theme.ink3, at: CGPoint(x: left.x - 6, y: left.y), anchor: .trailing)
    }
}

/// Draw the level-change markers for a plan: a bold connector-coloured disc with
/// an up/down chevron at each spot, then a label ("Escalator ↑ L+1") to its right,
/// dodged downward so labels never overlap.
private func drawTransitionMarks(_ ctx: GraphicsContext, _ marks: [(CGPoint, String, Bool, Int)]) {
    let r: CGFloat = 12
    for (p, kind, up, _) in marks {
        ctx.fill(Path(ellipseIn: CGRect(x: p.x-r-2.5, y: p.y-r-2.5, width: 2*r+5, height: 2*r+5)), with: .color(Theme.paper))
        ctx.fill(Path(ellipseIn: CGRect(x: p.x-r, y: p.y-r, width: 2*r, height: 2*r)), with: .color(WalkConnector.color(kind)))
        ctx.stroke(Path(ellipseIn: CGRect(x: p.x-r, y: p.y-r, width: 2*r, height: 2*r)), with: .color(Theme.paper), lineWidth: 1.5)
        ctx.drawGeoText(up ? "▲" : "▼", .system(size: 11, weight: .black), .white, at: p, anchor: .center)
    }
    var placed: [CGRect] = []
    for (p, kind, up, other) in marks {
        let text = "\(WalkConnector.label(kind)) \(up ? "↑" : "↓") \(WalkScene.label(forLevel: other))"
        var resolved = ctx.resolve(Text(text).font(.system(size: 11, weight: .semibold)))
        let ts = resolved.measure(in: CGSize(width: 500, height: 100))
        var center = CGPoint(x: p.x + r + 8 + ts.width/2, y: p.y)
        var rect = chipRect(center: center, textSize: ts)
        var guardN = 0
        while placed.contains(where: { $0.insetBy(dx: -2, dy: -2).intersects(rect) }) && guardN < 16 {
            center.y += rect.height + 3
            rect = chipRect(center: center, textSize: ts)
            guardN += 1
        }
        placed.append(rect)
        ctx.fill(Path(roundedRect: rect, cornerRadius: rect.height/2), with: .color(WalkConnector.color(kind)))
        resolved.shading = .color(.white)
        ctx.draw(resolved, at: center, anchor: .center)
    }
}

private func chipRect(center: CGPoint, textSize ts: CGSize, padX: CGFloat = 6, padY: CGFloat = 3) -> CGRect {
    CGRect(x: center.x - ts.width/2 - padX, y: center.y - ts.height/2 - padY,
           width: ts.width + 2*padX, height: ts.height + 2*padY)
}

/// A small rounded label chip centred at `center` (endpoint refs, the floor tag).
private func drawChip(_ ctx: GraphicsContext, _ text: String, center: CGPoint,
                      textColor: Color, fill: Color, weight: Font.Weight = .semibold) {
    var resolved = ctx.resolve(Text(text).font(.system(size: 11, weight: weight, design: .monospaced)))
    let ts = resolved.measure(in: CGSize(width: 500, height: 100))
    let rect = chipRect(center: center, textSize: ts)
    ctx.fill(Path(roundedRect: rect, cornerRadius: rect.height/2), with: .color(fill))
    resolved.shading = .color(textColor)
    ctx.draw(resolved, at: center, anchor: .center)
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
