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
    /// bands, the level picker, and the "Levels" stat).
    ///
    /// This — not `levelsAsc` — is what the plan can draw. `levelsAsc` is the union
    /// of levels over every *context* way the search touched, so it offers floors
    /// the walk never sets foot on: on the Dortmund fixture it tabs L−2, where the
    /// path has no geometry at all (issue #53).
    var pathLevels: [Int] {
        let ls = Set(pathPoints.map { level(of: $0.z) })
        return ls.isEmpty ? [startLevel] : ls.sorted()
    }

    /// The whole scene's XY box — the plan's fallback when a level has no route on
    /// it, and the 3D's frame in browse (station-map) mode or when there's no path.
    var worldBounds: CGRect {
        CGRect(x: minX, y: minY, width: max(maxX - minX, 0.001), height: max(maxY - minY, 0.001))
    }

    /// The XY box of the whole *path* (all levels) — `nil` when there's no path.
    ///
    /// Unlike `worldBounds` this ignores the context ways, so a short walk inside a
    /// vast concourse isn't measured against the whole station. On Berlin Hbf 1→16
    /// the walk is ~57 × 55 m where the scene box is ~510 × 814 m.
    var walkBounds: CGRect? {
        guard let first = pathPoints.first else { return nil }
        var lo = CGPoint(x: CGFloat(first.x), y: CGFloat(first.y))
        var hi = lo
        for p in pathPoints {
            lo.x = min(lo.x, CGFloat(p.x)); hi.x = max(hi.x, CGFloat(p.x))
            lo.y = min(lo.y, CGFloat(p.y)); hi.y = max(hi.y, CGFloat(p.y))
        }
        return CGRect(x: lo.x, y: lo.y, width: hi.x - lo.x, height: hi.y - lo.y)
    }

    /// The 3D's XY framing box in walk mode: the path's XY bbox grown by a margin,
    /// so the walk fills the model instead of collapsing against the station's full
    /// footprint (the Berlin 1→16 "vertical stick"). The full level range is kept
    /// separately, so every floor tab still shows. `nil` when there's no path — the
    /// caller then frames the whole station (`worldBounds`), which is also what
    /// browse (station-map) mode wants.
    ///
    /// Margin mirrors the design prototype's "Concept A": ~22 % of the longer side
    /// plus a fixed 18 m, so a 5 m hop and a 200 m corridor both breathe.
    var walkFramingBox: CGRect? {
        guard let w = walkBounds else { return nil }
        let m = max(w.width, w.height) * 0.22 + 18
        return w.insetBy(dx: -m, dy: -m)
    }

    /// The XY box of the part of the path on `level` — the frame that floor's plan
    /// is built on (issue #53, "own frame"). `nil` when the path never visits it.
    func routeBounds(level lvl: Int) -> CGRect? {
        let pts = pathPoints.filter { level(of: $0.z) == lvl }
        guard let first = pts.first else { return nil }
        var lo = CGPoint(x: CGFloat(first.x), y: CGFloat(first.y))
        var hi = lo
        for p in pts {
            lo.x = min(lo.x, CGFloat(p.x)); hi.x = max(hi.x, CGFloat(p.x))
            lo.y = min(lo.y, CGFloat(p.y)); hi.y = max(hi.y, CGFloat(p.y))
        }
        return CGRect(x: lo.x, y: lo.y, width: hi.x - lo.x, height: hi.y - lo.y)
    }

    /// The path split into contiguous single-level runs, in walk order. A floor
    /// plan strokes the runs on its own level and ghosts the ones on the floors it
    /// connects to.
    var levelRuns: [(level: Int, points: [Point3])] {
        var out: [(level: Int, points: [Point3])] = []
        for p in pathPoints {
            let l = level(of: p.z)
            if out.last?.level == l { out[out.count - 1].points.append(p) }
            else { out.append((level: l, points: [p])) }
        }
        return out.filter { $0.points.count >= 2 }
    }

    /// The floors `level` is joined to by a real transition — what the plan ghosts.
    func connectedLevels(to lvl: Int) -> Set<Int> {
        var out: Set<Int> = []
        for t in transitions {
            let f = level(of: t.from.z), b = level(of: t.to.z)
            guard f != b else { continue }
            if f == lvl { out.insert(b) }
            if b == lvl { out.insert(f) }
        }
        return out
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
    /// Does the map actually record *how* you change floors here? `vertical` is
    /// core/'s "a level change happens, mode unknown" — a gap in the data.
    static func isMapped(_ kind: String) -> Bool {
        switch kind {
        case "stairs", "escalator", "elevator", "lift", "ramp": return true
        default:                                                return false
        }
    }
    static func color(_ kind: String) -> Color {
        switch kind {
        case "stairs":              return Theme.stair
        case "escalator":           return Theme.esc
        case "elevator", "lift":    return Theme.elev
        case "ramp":                return Theme.accent
        // "vertical" / unknown. This must NOT be Theme.elev: that is the exact
        // orange the legend spends on "Lift", so an unclassified change drew as a
        // lift that doesn't exist (3 of Dortmund 11→4's 4 changes). Theme.nodata is
        // the app's own "we don't know" grey — DESIGN.md §7.5, honest gaps (#53).
        default:                    return Theme.nodata
        }
    }
    /// The connector colour softened ~18 % toward the panel — the "Recessed" 3D
    /// treatment, so the shaped risers inform without out-shouting the route.
    static func softColor(_ kind: String) -> Color {
        switch kind {
        case "stairs":              return Theme.stairSoft
        case "escalator":           return Theme.escSoft
        case "elevator", "lift":    return Theme.elevSoft
        case "ramp":                return Theme.accent
        default:                    return Theme.nodata
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

// MARK: - The card's one-line level story

/// The level line on the transfer card, derived from the walk's real `transitions`.
///
/// It carries its own icon and tint so the copy and the glyph can't drift: the row
/// used to sniff the sentence (`note.contains("Step-free")`) to pick between a
/// checkmark and a stairs symbol, which quietly claimed *stairs* for a walk whose
/// changes the map never classified.
///
/// The old line read out the enum — Dortmund 11→4 rendered literally as
/// "4 level changes — level change + stairs" (issue #53). This names the modes the
/// map records and reports the rest as a gap, in words a person would use.
struct LevelNote {
    let text: String
    let icon: String
    let tint: Color

    static func make(_ transitions: [VizExport.Transition]) -> LevelNote {
        guard !transitions.isEmpty else {
            return LevelNote(text: "Step-free — no stairs or lifts on the way.",
                             icon: "checkmark", tint: Theme.go)
        }
        let n = transitions.count
        let changes = "\(n) level change\(n == 1 ? "" : "s")"
        let mapped = transitions.filter { WalkConnector.isMapped($0.kind) }
        let unmapped = n - mapped.count

        // Only the modes the map actually records get named.
        let kinds = mapped.map { WalkConnector.label($0.kind).lowercased() }
        let names = Array(Set(kinds)).sorted()
        let tint = mapped.isEmpty ? Theme.nodata : WalkConnector.color(mapped[0].kind)
        let icon = mapped.isEmpty ? "questionmark.circle"
                                  : WalkConnector.icon(mapped[0].kind, up: true)

        if unmapped == 0 {
            let how = names.count == 1
                ? (n == 1 ? "by \(names[0])" : "all by \(names[0])")
                : names.joined(separator: " and ")
            return LevelNote(text: "\(changes), \(how).", icon: icon, tint: tint)
        }
        if mapped.isEmpty {
            // Every change is core/'s unclassified `vertical`. Say so plainly rather
            // than picking a mode we can't back.
            return LevelNote(text: "\(changes) — the map doesn't record stairs or a lift.",
                             icon: "questionmark.circle", tint: Theme.nodata)
        }
        let named = names.count == 1 ? "\(mapped.count) by \(names[0])"
                                     : "\(mapped.count) by \(names.joined(separator: " and "))"
        return LevelNote(text: "\(changes) — \(named), \(unmapped) the map doesn't name.",
                         icon: icon, tint: tint)
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

/// A top-down floor plan for one level, framed on **its own** stretch of the route
/// (issue #53, "own frame + context").
///
/// The frame is this floor's route box, grown to the canvas aspect — so the surplus
/// fills with real station at the same scale rather than letterboxing void — and
/// then layered: the context ways the search touched, a ghost of the floors this one
/// connects to, the route, paired in/out markers, and a scale bar. The scale bar is
/// the price of own-framing: the zoom now changes between tabs, and a map makes that
/// legible rather than disorienting.
struct PlanGeometryCanvas: View {
    let scene: WalkScene
    let level: Int

    var body: some View {
        Canvas { ctx, size in
            let fit = PlanFit.forLevel(scene, level: level, size: size, pad: 18)

            // Own-framing crops: context ways run well past this floor's route, so
            // everything geometric is clipped to the canvas.
            var g = ctx
            g.clip(to: Path(CGRect(origin: .zero, size: size)))

            // 1 · Context — only what the search touched, cropped to this frame.
            // Drawn back-to-front so the route's own connectors land on top.
            let ways = scene.export.ways
                .filter { $0.points.count >= 2 && $0.walkRelevant != false && wayTouches($0, level: level, scene: scene) }
                .sorted { contextOrder($0.kind) < contextOrder($1.kind) }
            for way in ways {
                var p = Path()
                p.move(to: fit.map(way.points[0]))
                for pt in way.points.dropFirst() { p.addLine(to: fit.map(pt)) }
                switch way.kind {
                case "platform":
                    // A slab is ~8 m of real width; keep it a slab, not a hairline.
                    let pw = min(max(8 * fit.scale, 3), 12)
                    g.stroke(p, with: .color(Theme.panel3), style: StrokeStyle(lineWidth: pw, lineCap: .round, lineJoin: .round))
                    g.stroke(p, with: .color(Theme.line), style: StrokeStyle(lineWidth: 0.6))
                case "walkway":
                    g.stroke(p, with: .color(Theme.line), style: StrokeStyle(lineWidth: 1.2, lineCap: .round, lineJoin: .round))
                case "stairs", "escalator", "ramp", "elevator":
                    g.stroke(p, with: .color(WalkConnector.color(way.kind).opacity(0.38)),
                             style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                default:
                    g.stroke(p, with: .color(Theme.line2), style: StrokeStyle(lineWidth: 1))
                }
            }

            guard scene.found, scene.pathPoints.count >= 2 else {
                if !scene.found { drawUnavailable(ctx, size, "Platforms not connected") }
                drawLevelChip(ctx, level)
                return
            }

            let runs = scene.levelRuns

            // 2 · Ghost — the real route on the floors this one connects to, so you
            // can see where you'll be standing one tab later (DESIGN.md §7.6).
            let near = scene.connectedLevels(to: level)
            for run in runs where near.contains(run.level) {
                var p = Path()
                p.move(to: fit.map(run.points[0]))
                for pt in run.points.dropFirst() { p.addLine(to: fit.map(pt)) }
                g.stroke(p, with: .color(Theme.ink3.opacity(0.4)),
                         style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round, dash: [3, 4]))
            }

            // 3 · The route on this floor.
            for run in runs where run.level == level {
                var p = Path()
                p.move(to: fit.map(run.points[0]))
                for pt in run.points.dropFirst() { p.addLine(to: fit.map(pt)) }
                g.stroke(p, with: .color(Theme.accent),
                         style: StrokeStyle(lineWidth: 4.5, lineCap: .round, lineJoin: .round))
            }

            // 4 · Markers. `transitions` is emitted in path order, so per floor the
            // marks alternate arrive / leave and each one knows which it is: a filled
            // disc is your next move, a hollow ring is where you landed. Drawn from
            // the *journey's* point of view, not the floor's — which is what made an
            // arrival on the last floor read as "go back down" (#53).
            var marks: [(p: CGPoint, kind: String, up: Bool, other: Int, arriving: Bool)] = []
            for t in scene.transitions {
                let f = scene.level(of: t.from.z), b = scene.level(of: t.to.z)
                guard f != b else { continue }
                if f == level { marks.append((fit.map(t.from), t.kind, b > level, b, false)) }
                if b == level { marks.append((fit.map(t.to), t.kind, f > level, f, true)) }
            }
            var ends: [(p: CGPoint, color: Color, text: String)] = []
            if scene.startLevel == level, let s = scene.pathPoints.first {
                ends.append((fit.map(s), Theme.go, "Step off · Pl \(scene.startRef)"))
            }
            if scene.endLevel == level, let e = scene.pathPoints.last {
                ends.append((fit.map(e), Theme.accent, "Board · Pl \(scene.endRef)"))
            }

            for e in ends { endpoint(g, e.p, e.color, r: 6.5) }
            for m in marks {
                let c = WalkConnector.color(m.kind)
                if m.arriving { arriveMark(g, m.p, c) } else { leaveMark(g, m.p, c, up: m.up) }
            }

            // Chips last, endpoints first so they win the good spots.
            var placed: [CGRect] = []
            for e in ends {
                drawChip(ctx, e.text, rightOf: e.p, gap: 9, fill: e.color, ink: .white,
                         size: size, placed: &placed)
            }
            for m in marks {
                if m.arriving {
                    drawChip(ctx, "in from \(WalkScene.label(forLevel: m.other))",
                             rightOf: m.p, gap: 12, fill: Theme.panel3, ink: Theme.ink2,
                             size: size, placed: &placed)
                } else {
                    let verb = WalkConnector.verb(m.kind)
                    drawChip(ctx, "\(verb) \(m.up ? "↑" : "↓") to \(WalkScene.label(forLevel: m.other))",
                             rightOf: m.p, gap: 15, fill: WalkConnector.color(m.kind), ink: .white,
                             size: size, placed: &placed)
                }
            }

            drawScaleBar(ctx, fit, size)
            drawLevelChip(ctx, level)
        }
    }
}

/// Back-to-front order for the context layer: slabs and walkways under, connectors
/// over, so the route's own stairs/escalators aren't buried by the station's web.
private func contextOrder(_ kind: String) -> Int {
    switch kind {
    case "walkway":                                   return 0
    case "platform":                                  return 1
    case "ramp":                                      return 2
    case "stairs", "escalator", "elevator", "lift":   return 3
    default:                                          return 0
    }
}

// MARK: - Iconographic connector glyphs (3D)
//
// Ported from the connector prototype: each connector segment on the exploded model
// is drawn as the *shape* of the real thing — and, for escalators and lifts, its
// motion — on the "Recessed" base (soft colour + a paper casing so crossings stay
// legible). A stair steps, an escalator's chevrons ride toward the top, a lift car
// travels its shaft. When motion is off (reduce-motion) they freeze at a neutral pose.

private let connChevronGap: CGFloat = 18      // spacing of the escalator's chevrons
private let connChevronSpeed: Double = 15     // px/s they ride upward
private let liftCyclePeriod: Double = 5.6     // s for one car up-and-down cycle

private func strokePts(_ ctx: GraphicsContext, _ pts: [CGPoint], _ color: Color, _ w: CGFloat) {
    guard let first = pts.first else { return }
    var p = Path(); p.move(to: first)
    for q in pts.dropFirst() { p.addLine(to: q) }
    ctx.stroke(p, with: .color(color), style: StrokeStyle(lineWidth: w, lineCap: .round, lineJoin: .round))
}

/// A single chevron centred at `c`, pointing along the unit vector `dir`.
private func chevron(_ ctx: GraphicsContext, at c: CGPoint, dir: CGPoint, size: CGFloat, _ color: Color, _ w: CGFloat) {
    let px = -dir.y, py = dir.x
    let tip = CGPoint(x: c.x + dir.x * size * 0.5, y: c.y + dir.y * size * 0.5)
    let back = CGPoint(x: c.x - dir.x * size * 0.5, y: c.y - dir.y * size * 0.5)
    var p = Path()
    p.move(to: CGPoint(x: back.x + px * size * 0.6, y: back.y + py * size * 0.6))
    p.addLine(to: tip)
    p.addLine(to: CGPoint(x: back.x - px * size * 0.6, y: back.y - py * size * 0.6))
    ctx.stroke(p, with: .color(color), style: StrokeStyle(lineWidth: w, lineCap: .round, lineJoin: .round))
}

/// Stairs — a stepped silhouette (tread, riser, tread…) between two screen points.
private func glyphStairs(_ ctx: GraphicsContext, _ a: CGPoint, _ b: CGPoint, _ col: Color, w: CGFloat, alpha: Double) {
    let len = hypot(b.x - a.x, b.y - a.y)
    let n = max(3, min(7, Int((len / 8).rounded())))
    let dx = (b.x - a.x) / CGFloat(n), dy = (b.y - a.y) / CGFloat(n)
    var pts: [CGPoint] = [a]; var x = a.x, y = a.y
    for _ in 0..<n { x += dx; pts.append(CGPoint(x: x, y: y)); y += dy; pts.append(CGPoint(x: x, y: y)) }
    strokePts(ctx, pts, Theme.paper.opacity(0.9 * alpha), w + 3)   // casing
    strokePts(ctx, pts, col.opacity(alpha), w)
}

/// Escalator — a faint band with chevrons riding from the low end to the high end.
private func glyphEscalator(_ ctx: GraphicsContext, lo: CGPoint, hi: CGPoint, _ col: Color,
                            w: CGFloat, alpha: Double, now: Double, motion: Bool) {
    let len = hypot(hi.x - lo.x, hi.y - lo.y); guard len > 2 else { return }
    let ux = (hi.x - lo.x) / len, uy = (hi.y - lo.y) / len
    strokePts(ctx, [lo, hi], Theme.paper.opacity(0.8 * alpha), w + 3)
    strokePts(ctx, [lo, hi], col.opacity(0.5 * alpha), w)
    let gap = connChevronGap
    let phase = motion ? CGFloat((now * connChevronSpeed).truncatingRemainder(dividingBy: Double(gap))) : gap * 0.5
    var s = phase
    while s <= len - 1.5 {
        chevron(ctx, at: CGPoint(x: lo.x + ux * s, y: lo.y + uy * s),
                dir: CGPoint(x: ux, y: uy), size: 4.4, col.opacity(alpha), 1.7)
        s += gap
    }
}

private func easeTri(_ u: Double) -> Double { u < 0.5 ? 2 * u * u : 1 - pow(-2 * u + 2, 2) / 2 }
/// The car's position along its shaft, 0 (low) … 1 (high), on a slow eased cycle.
private func liftCarPosition(_ now: Double) -> Double {
    let x = now.truncatingRemainder(dividingBy: liftCyclePeriod) / liftCyclePeriod
    return easeTri(x < 0.5 ? x / 0.5 : (1 - x) / 0.5)
}

/// Lift — a masked shaft with side rails, landing arrows, and a car that travels it.
private func glyphLift(_ ctx: GraphicsContext, lo: CGPoint, hi: CGPoint, _ col: Color,
                       alpha: Double, big: Bool, now: Double, motion: Bool) {
    let len = hypot(hi.x - lo.x, hi.y - lo.y); guard len > 2 else { return }
    let ux = (hi.x - lo.x) / len, uy = (hi.y - lo.y) / len, px = -uy, py = ux
    let r: CGFloat = big ? 4.4 : 3.2
    strokePts(ctx, [lo, hi], Theme.paper.opacity(0.92 * alpha), r * 2 + 4)   // mask the route behind
    strokePts(ctx, [CGPoint(x: lo.x + px * r, y: lo.y + py * r), CGPoint(x: hi.x + px * r, y: hi.y + py * r)], col.opacity(0.45 * alpha), 1.4)
    strokePts(ctx, [CGPoint(x: lo.x - px * r, y: lo.y - py * r), CGPoint(x: hi.x - px * r, y: hi.y - py * r)], col.opacity(0.45 * alpha), 1.4)
    chevron(ctx, at: CGPoint(x: hi.x + ux * 1.5, y: hi.y + uy * 1.5), dir: CGPoint(x: ux, y: uy), size: 4.6, col.opacity(0.4 * alpha), 1.3)
    chevron(ctx, at: CGPoint(x: lo.x - ux * 1.5, y: lo.y - uy * 1.5), dir: CGPoint(x: -ux, y: -uy), size: 4.6, col.opacity(0.4 * alpha), 1.3)
    let t = motion ? liftCarPosition(now) : 0.62
    let cx = lo.x + (hi.x - lo.x) * CGFloat(t), cy = lo.y + (hi.y - lo.y) * CGFloat(t)
    let cw = r * 2 + 2, ch: CGFloat = big ? 9 : 6.5
    var g = ctx
    g.translateBy(x: cx, y: cy)
    g.rotate(by: .radians(atan2(uy, ux) + .pi / 2))
    let rect = CGRect(x: -cw / 2, y: -ch / 2, width: cw, height: ch)
    g.fill(Path(roundedRect: rect, cornerRadius: 2.2), with: .color(col.opacity(alpha)))
    g.stroke(Path(roundedRect: rect, cornerRadius: 2.2), with: .color(Theme.paper.opacity(0.9 * alpha)), lineWidth: 1.2)
}

/// Draw one connector segment (raw 3D endpoints) as its glyph. `lo`/`hi` are ordered
/// by height, so escalator chevrons and the lift car always read low → high.
private func connectorSegment(_ ctx: GraphicsContext, _ iso: IsoFit, _ a3: Point3, _ b3: Point3,
                              kind: String, isRiser: Bool, now: Double, motion: Bool, alphaMul: Double) {
    let A = iso.map(a3), B = iso.map(b3)
    let alpha = (isRiser ? 1.0 : 0.62) * alphaMul
    let col = WalkConnector.softColor(kind)
    let lo = a3.z <= b3.z ? A : B
    let hi = a3.z <= b3.z ? B : A
    switch kind {
    case "stairs":
        glyphStairs(ctx, A, B, col, w: isRiser ? 3 : 1.9, alpha: alpha)
    case "escalator":
        glyphEscalator(ctx, lo: lo, hi: hi, col, w: isRiser ? 2.4 : 1.7, alpha: alpha, now: now, motion: motion)
    case "elevator", "lift":
        glyphLift(ctx, lo: lo, hi: hi, col, alpha: alpha, big: isRiser, now: now, motion: motion)
    default:
        strokePts(ctx, [A, B], Theme.paper.opacity(0.8 * alpha), (isRiser ? 2.6 : 1.6) + 3)
        strokePts(ctx, [A, B], col.opacity(alpha), isRiser ? 2.6 : 1.6)
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
    /// Drives the escalator/lift motion. Production leaves it on; headless snapshots
    /// pass `false` for one deterministic, animation-free frame (`reduceMotion` is a
    /// read-only environment value and can't be forced through `.environment`).
    var animated: Bool = true
    @State private var yaw: Double = 0.5
    @GestureState private var twist: Double = 0
    @State private var zoom: CGFloat = 1
    @GestureState private var pinch: CGFloat = 1
    @State private var pan: CGSize = .zero
    @GestureState private var dragPan: CGSize = .zero
    @State private var focusLevel: Int?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let liveYaw = yaw + twist
        let liveZoom = min(max(zoom * pinch, 0.5), 8)
        let livePan = CGSize(width: pan.width + dragPan.width, height: pan.height + dragPan.height)
        let focus = focusLevel
        Group {
            if reduceMotion || !animated {
                Canvas { ctx, size in
                    draw(ctx, size, liveYaw: liveYaw, liveZoom: liveZoom, livePan: livePan,
                         focus: focus, now: 0, motion: false)
                }
            } else {
                // One TimelineView clock drives the escalator chevrons and the lift
                // car; the projection and the rest of the scene are recomputed each
                // tick, the way the prototype's rAF loop redraws every frame.
                TimelineView(.animation) { tl in
                    Canvas { ctx, size in
                        draw(ctx, size, liveYaw: liveYaw, liveZoom: liveZoom, livePan: livePan,
                             focus: focus, now: tl.date.timeIntervalSinceReferenceDate, motion: true)
                    }
                }
            }
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
        .overlay(alignment: .top) {
            // One gray icon-label per gesture, no chip — kept at the top so they
            // stay clear of the level chips along the bottom.
            HStack(spacing: 14) {
                Label("Pan", systemImage: "hand.draw")          // drag gesture
                Label("Zoom", systemImage: "hand.pinch")        // pinch gesture
                Label("Rotate", systemImage: "rotate.3d")       // spin the model
            }
            .font(.system(size: 9.5)).foregroundStyle(Theme.ink3).padding(.top, 6)
        }
    }

    /// Renders the whole 3D scene into `ctx`. Split out of `body` so the static
    /// (reduce-motion) path and the animated `TimelineView` path share one renderer.
    /// `now` (seconds) drives the escalator chevrons and lift car; `motion == false`
    /// freezes them at a neutral pose.
    private func draw(_ ctx: GraphicsContext, _ size: CGSize, liveYaw: Double, liveZoom: CGFloat,
                      livePan: CGSize, focus: Int?, now: Double, motion: Bool) {
        // Walk mode frames the *walk* — the path's XY box grown by a margin — so a
        // short walk inside a vast station spreads across the model instead of
        // collapsing to a vertical stick (Berlin Hbf 1→16). Browse (station map) mode
        // and pathless walks fall back to the whole-station box so the entire layout
        // stays visible. Either way the full level range drives the vertical lift and
        // the left-edge tabs.
        let box = (browse ? nil : scene.walkFramingBox) ?? scene.worldBounds
        let iso = IsoFit(scene: scene, box: box, size: size, angle: liveYaw, zoom: liveZoom, pan: livePan, pad: 24)
        drawLevelTabs(ctx, iso)
        func alpha(_ lvl: Int) -> Double { focus == nil || focus == lvl ? 1 : 0.10 }

        // Ways, drawn low floors first so upper floors overlay. A selected level dims the rest.
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
            case "stairs", "escalator", "ramp", "elevator", "lift":
                // Each connector is drawn as its own shape (and motion): stepped
                // stairs, an escalator's riding chevrons, a lift car in its shaft.
                for i in 0..<(way.points.count - 1) {
                    connectorSegment(ctx, iso, way.points[i], way.points[i + 1],
                                     kind: way.kind, isRiser: false, now: now, motion: motion, alphaMul: a)
                }
            default:
                ctx.stroke(p, with: .color(Theme.line2.opacity(a)), style: StrokeStyle(lineWidth: 1))
            }
        }

        // Every platform the export carries, marked with its ref and lifted to its
        // floor — not just the two the walk connects. Hidden on other floors when a
        // level is isolated.
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
        // Recessed treatment: the route is the hero — a touch heavier, with a soft glow.
        var glow = ctx
        glow.addFilter(.shadow(color: Theme.accent.opacity(0.55), radius: 4))
        glow.stroke(route, with: .color(Theme.accent), style: StrokeStyle(lineWidth: 4.5, lineCap: .round, lineJoin: .round))
        for t in scene.transitions {   // the stair / escalator / lift you actually ride
            connectorSegment(ctx, iso, t.from, t.to, kind: t.kind, isRiser: true, now: now, motion: motion, alphaMul: 1)
        }
        endpoint(ctx, iso.map(pts.first!), Theme.go, r: 5)
        endpoint(ctx, iso.map(pts.last!), Theme.accent, r: 5)
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
            // Anchor the tabs to the *framing* box's left-mid edge, not the whole
            // scene's — otherwise walk-mode framing (a small box) would project the
            // far scene corner clean off-canvas and the tabs would drift away.
            let ref = iso.map(Point3(x: Float(iso.box.minX),
                                     y: Float(iso.box.midY),
                                     z: Float(CGFloat(lvl) * scene.floorHeight)))
            var t = ctx.resolve(Text(WalkScene.label(forLevel: lvl))
                .font(.system(size: 9, weight: .bold, design: .monospaced)))
            t.shading = .color(Theme.ink3)
            ctx.draw(t, at: CGPoint(x: 12, y: ref.y), anchor: .leading)
        }
    }
}

// MARK: - Projections

/// Uniform aspect-fit of a world XY box into a rect, Y flipped (world up → screen
/// down). Internal (not private) so the tests can pin the framing directly.
struct PlanFit {
    let scale: CGFloat, offX: CGFloat, offY: CGFloat, minX: CGFloat, maxY: CGFloat

    init(box: CGRect, size: CGSize, pad: CGFloat) {
        let spanX = max(box.width, 0.001), spanY = max(box.height, 0.001)
        scale = min((size.width - 2*pad) / spanX, (size.height - 2*pad) / spanY)
        offX = pad + ((size.width - 2*pad) - spanX*scale) / 2
        offY = pad + ((size.height - 2*pad) - spanY*scale) / 2
        minX = box.minX; maxY = box.maxY
    }

    func map(_ p: Point3) -> CGPoint {
        CGPoint(x: offX + (CGFloat(p.x) - minX) * scale,
                y: offY + (maxY - CGFloat(p.y)) * scale)
    }

    /// The framing decision, in one place (issue #53).
    ///
    /// This used to frame every level on the **whole scene's** box — every context
    /// way the search touched, plus the path — and defended it with "so the plan
    /// never jumps when you switch". It bought that steadiness by starving each
    /// floor: on Berlin Hbf 1→16 the scene box is 510 × 814 m, so *every* one of the
    /// five floors drew its route at under 0.05% of the canvas — a ~3 px mark adrift
    /// in void.
    ///
    /// Instead: frame the floor on its own route, then grow the frame to the canvas
    /// aspect. Growing (not letterboxing) is the point — the surplus fills with real
    /// station at the *same* scale, so the route keeps its size and nothing is zoomed
    /// out to buy context. The zoom does now change between tabs; the scale bar pays
    /// for that.
    static func forLevel(_ scene: WalkScene, level: Int, size: CGSize, pad: CGFloat) -> PlanFit {
        guard let route = scene.routeBounds(level: level) else {
            // The path never visits this floor. The picker shouldn't offer it at all
            // (it reads `pathLevels`), but a caller can still ask: fall back to the
            // whole scene rather than divide by a zero-sized box.
            return PlanFit(box: scene.worldBounds, size: size, pad: pad)
        }
        // Breathing room proportional to the route, floored/capped so a 5 m hop and
        // a 200 m corridor both land somewhere sane.
        let m = min(max(0.35 * max(route.width, route.height), 10), 30)
        let padded = route.insetBy(dx: -m, dy: -m)
        let aspect = (size.width - 2*pad) / max(size.height - 2*pad, 0.001)
        var w = max(padded.width, 0.001), h = max(padded.height, 0.001)
        if w / h < aspect { w = h * aspect } else { h = w / aspect }
        return PlanFit(box: CGRect(x: padded.midX - w/2, y: padded.midY - h/2, width: w, height: h),
                       size: size, pad: pad)
    }
}

/// Exploded-floor axonometric projection. XY is normalised to a nominal size (so
/// stations of any footprint look alike), rotated by `angle`, iso-projected, then
/// each level is lifted clear of the floor below it. The fit is computed from the
/// **framing box** corners at the extreme levels, so nothing clips at any rotation.
///
/// The framing box is passed in, not read off the scene: walk mode frames the
/// *walk* (`WalkScene.walkFramingBox` — the path's XY box plus a margin) so a short
/// walk inside a vast station spreads across the model instead of collapsing to a
/// vertical stick; browse (station map) mode passes the whole-station box
/// (`worldBounds`). The level range still comes from the scene, so every floor tab
/// shows and the per-floor lift stays proportional to the (now smaller) footprint.
/// Internal (not private) so the tests can pin that the floors really separate.
struct IsoFit {
    let angle: Double
    /// The XY box this fit frames — the walk's box in walk mode, the whole station
    /// in browse. `drawLevelTabs` reads it so the tabs track the framed box.
    let box: CGRect
    let cx: CGFloat, cy: CGFloat, norm: CGFloat, floorHeight: CGFloat
    let scale: CGFloat, scx: CGFloat, scy: CGFloat, qcx: CGFloat, qcy: CGFloat
    let pan: CGSize
    /// The screen-y lift per floor — *derived* from the footprint, not a constant.
    ///
    /// It was a flat 20 while the thing it tracks — the iso-projected footprint —
    /// is data- and angle-dependent: Berlin Hbf's normalised floor projects to ~62
    /// units of screen-y at the default orbit, so with a constant lift floors
    /// −2/−1/0 fused into one plate (#53). So it scales with the footprint. But
    /// lifting by a hair *more* than the full footprint (the old `× 1.06`) made a
    /// one-floor riser as tall as a whole floor is deep — the lifts/elevators read
    /// as absurdly stretched sticks. Z is `level × floorHeight` nominal, not
    /// surveyed elevation, so true-to-scale isn't an option (a ~4 m floor against a
    /// ~300 m concourse would collapse the stack). Instead lift by ~a third of the
    /// footprint: floors now overlap but stay clearly stacked, and risers keep sane
    /// proportions.
    let levelUnit: CGFloat

    init(scene: WalkScene, box: CGRect, size: CGSize, angle: Double, zoom: CGFloat, pan: CGSize = .zero, pad: CGFloat) {
        self.angle = angle
        self.pan = pan
        self.box = box
        cx = box.midX
        cy = box.midY
        let diag = max(box.width.magnitude, box.height.magnitude)
        norm = 100 / max(diag, 0.001)
        floorHeight = scene.floorHeight

        // A floor's projected screen-y span. `project` puts y at (rx+ry)·sin30, and
        // rx+ry = nx·(cos+sin) + ny·(cos−sin), so over the normalised box the span
        // is |cos+sin|·spanNX + |cos−sin|·spanNY, halved by sin30. Derived from the
        // framing box, so in walk mode the lift tracks the walk's footprint, not the
        // whole station's.
        let spanNX = box.width.magnitude * norm
        let spanNY = box.height.magnitude * norm
        let c = CGFloat(cos(angle)), s = CGFloat(sin(angle))
        let footprintY = (abs(c + s) * spanNX + abs(c - s) * spanNY) * 0.5
        // ~1/3 of the footprint (was `× 1.06`, full clearance): floors overlap but
        // stay stacked, and a one-floor riser no longer towers over its own plate.
        levelUnit = max(footprintY * 0.35, 8)
        let lu = levelUnit

        // Pre-project the 8 bounding corners (framing box × full level range) to find
        // extents. The level range is the station's, so every floor tab still shows.
        let loL = CGFloat(scene.levelsAsc.first ?? 0), hiL = CGFloat(scene.levelsAsc.last ?? 0)
        var lo = CGPoint(x: CGFloat.greatestFiniteMagnitude, y: CGFloat.greatestFiniteMagnitude)
        var hi = CGPoint(x: -CGFloat.greatestFiniteMagnitude, y: -CGFloat.greatestFiniteMagnitude)
        for x in [box.minX, box.maxX] {
            for y in [box.minY, box.maxY] {
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

// MARK: - Paired transition marks (#53)
//
// The two halves of a level change. A mark used to know only "the other floor is
// above/below", never whether it was where you came *in* or where you go *out* —
// so a pair on one floor said the same thing twice, and an arrival on the last
// floor read as an instruction to go back down. These are DESIGN.md §7.6's
// "connective tissue that stitch the floors back into one journey", so they say
// which half they are.

/// Where you leave this floor — filled, with the direction you're about to go.
private func leaveMark(_ ctx: GraphicsContext, _ p: CGPoint, _ c: Color, up: Bool) {
    let r: CGFloat = 9
    ctx.fill(Path(ellipseIn: CGRect(x: p.x - r - 2, y: p.y - r - 2, width: 2*r + 4, height: 2*r + 4)),
             with: .color(Theme.paper))
    ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)), with: .color(c))
    ctx.stroke(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)),
               with: .color(Theme.paper), lineWidth: 1.5)
    ctx.drawGeoText(up ? "▲" : "▼", .system(size: 9, weight: .black), .white, at: p, anchor: .center)
}

/// Where you arrived on this floor — hollow, because it's orientation, not a
/// decision. Never carries a direction: you've already made this move.
private func arriveMark(_ ctx: GraphicsContext, _ p: CGPoint, _ c: Color) {
    let r: CGFloat = 6.2
    ctx.fill(Path(ellipseIn: CGRect(x: p.x - 9, y: p.y - 9, width: 18, height: 18)),
             with: .color(Theme.paper))
    ctx.stroke(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)),
               with: .color(c), lineWidth: 2.6)
}

/// A rounded label chip anchored beside `p`, dodged clear of the chips already
/// placed and kept inside the canvas.
///
/// The dodge searches *outward* — down, up, further down, further up — rather than
/// only downward: a floor like Dortmund L0 puts six chips in one corner, and a
/// one-way search walks the last of them a long way from the mark it belongs to.
private func drawChip(_ ctx: GraphicsContext, _ text: String, rightOf p: CGPoint, gap: CGFloat,
                      fill: Color, ink: Color, size: CGSize, placed: inout [CGRect]) {
    var resolved = ctx.resolve(Text(text).font(.system(size: 9.5, weight: .semibold)))
    let ts = resolved.measure(in: CGSize(width: 400, height: 60))
    let w = ts.width + 11, h = ts.height + 5
    var cx = p.x + gap + w/2
    if cx + w/2 > size.width - 3 { cx = p.x - gap - w/2 }        // no room right: flip
    cx = min(max(cx, w/2 + 3), size.width - w/2 - 3)             // and stay on canvas

    func rect(_ cy: CGFloat) -> CGRect { CGRect(x: cx - w/2, y: cy - h/2, width: w, height: h) }
    func free(_ r: CGRect) -> Bool {
        r.minY >= 2 && r.maxY <= size.height - 2
            && !placed.contains { $0.insetBy(dx: -2, dy: -2).intersects(r) }
    }
    let step = h + 3
    var best = rect(p.y)
    if !free(best) {
        var found = false
        for i in 1...7 {
            for candidate in [rect(p.y + CGFloat(i) * step), rect(p.y - CGFloat(i) * step)]
            where free(candidate) { best = candidate; found = true; break }
            if found { break }
        }
        // Nowhere clear: keep it on the mark rather than exiled off the canvas.
        if !found { best = rect(min(max(p.y, h/2 + 2), size.height - h/2 - 2)) }
    }
    placed.append(best)
    // A displaced chip needs a leader, or it reads as labelling wherever it landed.
    // Neutral ink, not the chip's own fill — the muted chips are `panel3`, which is
    // invisible against `paper`.
    if abs(best.midY - p.y) > 2 {
        var leader = Path()
        leader.move(to: p)
        leader.addLine(to: CGPoint(x: best.minX > p.x ? best.minX : best.maxX, y: best.midY))
        ctx.stroke(leader, with: .color(Theme.ink3.opacity(0.5)), lineWidth: 0.9)
    }
    ctx.fill(Path(roundedRect: best, cornerRadius: h/2), with: .color(fill))
    resolved.shading = .color(ink)
    ctx.draw(resolved, at: CGPoint(x: best.midX, y: best.midY), anchor: .center)
}

/// Per-level framing means the zoom changes between tabs. A scale bar is how maps
/// have always made that legible rather than disorienting (#53).
private func drawScaleBar(_ ctx: GraphicsContext, _ fit: PlanFit, _ size: CGSize) {
    let spanM = size.width / max(fit.scale, 0.0001)
    var m: CGFloat = 5
    for n in [5, 10, 20, 25, 50, 100, 200, 400] as [CGFloat] where n <= spanM * 0.32 { m = n }
    let px = m * fit.scale
    guard px.isFinite, px > 1 else { return }
    let x0: CGFloat = 10, y0 = size.height - 10
    var bar = Path()
    bar.move(to: CGPoint(x: x0, y: y0));             bar.addLine(to: CGPoint(x: x0 + px, y: y0))
    bar.move(to: CGPoint(x: x0, y: y0 - 3));         bar.addLine(to: CGPoint(x: x0, y: y0 + 2))
    bar.move(to: CGPoint(x: x0 + px, y: y0 - 3));    bar.addLine(to: CGPoint(x: x0 + px, y: y0 + 2))
    ctx.stroke(bar, with: .color(Theme.ink3.opacity(0.85)), lineWidth: 1.2)
    ctx.drawGeoText("\(Int(m)) m", .system(size: 8, weight: .semibold, design: .monospaced),
                    Theme.ink3, at: CGPoint(x: x0 + px + 5, y: y0), anchor: .leading)
}

/// Which floor you're looking at, top-left, always on top.
private func drawLevelChip(_ ctx: GraphicsContext, _ level: Int) {
    let rect = CGRect(x: 14, y: 10, width: 40, height: 16)
    ctx.fill(Path(roundedRect: rect, cornerRadius: 6), with: .color(Theme.panel3))
    ctx.drawGeoText(WalkScene.label(forLevel: level),
                    .system(size: 9.5, weight: .heavy, design: .monospaced),
                    Theme.ink2, at: CGPoint(x: rect.midX, y: rect.midY), anchor: .center)
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
