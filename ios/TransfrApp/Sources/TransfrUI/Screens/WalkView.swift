import SwiftUI
import TransfrCore

/// The walk between two platforms — the prototype's `#s-walk`. Section / Levels
/// tabs render with SwiftUI `Canvas` (DESIGN.md §13.3, "one contract, four
/// renderers"). This first cut draws a **schematic** from the transfer's own
/// fields; when `/walk` returns real `viz_export` geometry the same views project
/// `export.path.points` instead (the hook is `loadGeometry()` below). 3D and AR
/// are documented follow-ups.
struct WalkView: View {
    @Environment(TripModel.self) private var model
    let transferIndex: Int

    @State private var mode: Mode = .section
    @State private var level: Int = 0
    @State private var scene: WalkScene?      // real geometry once /walk returns it
    @State private var loading = true         // fetching that geometry (first load)

    enum Mode: String, CaseIterable, Identifiable { case section, levels, threeD
        var id: String { rawValue }
        var label: String { self == .threeD ? "3D" : rawValue.capitalized }
        var icon: String {
            switch self { case .section: "chart.bar.xaxis"; case .levels: "square.stack.3d.up"; case .threeD: "cube" }
        }
    }

    private var transfer: Transfer? { model.transfers[safe: transferIndex] }
    private var hasLevelChange: Bool { (transfer?.verdictKind ?? .feasible) != .feasible }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                modePicker
                stage
                statsRow
                steps
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle(transfer?.atStation ?? "Walk")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .principal) { principal } }
        .task { await loadGeometry() }
    }

    private var principal: some View {
        VStack(spacing: 1) {
            Text(transfer?.atStation ?? "Walk").font(.system(size: 16, weight: .semibold))
            if let t = transfer {
                Text("Platform \(t.arrivalPlatform ?? "?") → \(t.departurePlatform ?? "?") · \(Fmt.meters(t.walkDistanceM)) · \(Fmt.walkTime(t.walkTimeS))")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
            }
        }
    }

    private var modePicker: some View {
        HStack(spacing: 6) {
            ForEach(Mode.allCases) { m in
                Button { withAnimation(.snappy) { mode = m } } label: {
                    Label(m.label, systemImage: m.icon)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(mode == m ? .white : Theme.ink2)
                        .frame(maxWidth: .infinity).padding(.vertical, 9)
                        .background(RoundedRectangle(cornerRadius: 11)
                            .fill(mode == m ? Theme.accent : Theme.panel2))
                }
                .buttonStyle(.plain)
            }
        }
    }

    @ViewBuilder
    private var stage: some View {
        switch mode {
        case .section:
            Panel(padding: 12, tint: Theme.panel) {
                VStack(spacing: 10) {
                    stageBox {
                        if let scene { SectionGeometryCanvas(scene: scene) }
                        else { SectionCanvas(transfer: transfer, hasLevelChange: hasLevelChange) }
                    }
                    legend([("Your path", Theme.accent), ("Stairs", Theme.stair),
                            ("Escalator", Theme.esc), ("Elevator", Theme.elev)])
                }
            }
        case .levels:
            VStack(spacing: 10) {
                levelPicker
                Panel(padding: 12) {
                    VStack(spacing: 10) {
                        stageBox {
                            if let scene { PlanGeometryCanvas(scene: scene, level: level) }
                            else { LevelCanvas(level: level, transfer: transfer, hasLevelChange: hasLevelChange) }
                        }
                        legend([("Path", Theme.accent), ("Platform", Theme.panel3), ("Connector", Theme.stair)])
                    }
                }
            }
        case .threeD:
            Panel(padding: 12) {
                VStack(spacing: 10) {
                    stageBox(height: 260) {
                        if let scene { IsoGeometryCanvas(scene: scene) }
                        else { threeDPlaceholder }
                    }
                    if scene != nil {
                        Text("Exploded floors, drawn from the walk's real geometry — the same viz_export the Section and Levels tabs project.")
                            .font(.system(size: 12)).foregroundStyle(Theme.ink3)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
    }

    /// Fixed-height stage that shows a spinner over the first geometry fetch, so a
    /// live walk never flashes the schematic before its real drawing arrives.
    @ViewBuilder
    private func stageBox<Content: View>(height: CGFloat = 210, @ViewBuilder _ content: () -> Content) -> some View {
        ZStack {
            content().frame(height: height).frame(maxWidth: .infinity)
            if scene == nil && loading {
                RoundedRectangle(cornerRadius: 12).fill(Theme.panel2).frame(height: height)
                    .overlay(ProgressView())
            }
        }
    }

    @ViewBuilder
    private var levelPicker: some View {
        if let scene, scene.levelsAsc.count > 1 {
            Picker("Level", selection: $level) {
                ForEach(scene.levelsAsc.reversed(), id: \.self) { lvl in
                    Text(levelPickerLabel(lvl, scene)).tag(lvl)
                }
            }.pickerStyle(.segmented)
        } else if scene == nil && hasLevelChange {
            Picker("Level", selection: $level) {
                Text("L0 · Platforms").tag(0)
                Text("L−1 · Underpass").tag(-1)
            }.pickerStyle(.segmented)
        }
    }

    private func levelPickerLabel(_ lvl: Int, _ scene: WalkScene) -> String {
        var s = WalkScene.label(forLevel: lvl)
        if lvl == scene.startLevel { s += " · off" }
        else if lvl == scene.endLevel { s += " · board" }
        return s
    }

    private var threeDPlaceholder: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14).fill(Theme.panel2)
            VStack(spacing: 8) {
                Image(systemName: "cube.transparent").font(.system(size: 34)).foregroundStyle(Theme.accent)
                Text("3D view").font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
                Text("Rotatable floors render once this walk's geometry loads from /walk.")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
                    .multilineTextAlignment(.center).padding(.horizontal, 24)
            }
        }
    }

    private func legend(_ items: [(String, Color)]) -> some View {
        HStack(spacing: 14) {
            ForEach(items, id: \.0) { name, color in
                HStack(spacing: 5) {
                    Circle().fill(color).frame(width: 8, height: 8)
                    Text(name).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                }
            }
            Spacer()
        }
    }

    private var statsRow: some View {
        HStack {
            StatCell(key: "Walk time", value: Fmt.walkTime(transfer?.walkTimeS))
            StatCell(key: "Distance", value: Fmt.meters(transfer?.walkDistanceM))
            StatCell(key: "Levels", value: levelsStat)
        }
    }

    /// The deepest level the path drops to relative to where you step off — real
    /// when geometry is loaded, the `hasLevelChange` proxy otherwise.
    private var levelsStat: String {
        guard let scene else { return hasLevelChange ? "−1" : "0" }
        let levels = scene.pathLevels
        guard let lo = levels.min(), let hi = levels.max(), lo != hi else { return "0" }
        let deepest = abs(lo - scene.startLevel) >= abs(hi - scene.startLevel) ? lo : hi
        let d = deepest - scene.startLevel
        return d == 0 ? "0" : (d > 0 ? "+\(d)" : "−\(abs(d))")
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: 10) {
            Eyebrow(text: "Turn by turn")
            ForEach(currentSteps) { step in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: step.icon).font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 26, height: 26)
                        .background(Circle().fill(step.color))
                    VStack(alignment: .leading, spacing: 1) {
                        Text(step.title).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                        Text(step.sub).font(.system(size: 12)).foregroundStyle(Theme.ink3)
                    }
                }
            }
        }
    }

    /// Derived from the real `transitions` when geometry is present; the synthesized
    /// walkthrough only stands in for the sample tier / off-path lookups.
    private var currentSteps: [WalkStep] {
        if let scene { return scene.turnByTurn() }
        return schematicSteps
    }

    private var schematicSteps: [WalkStep] {
        guard let t = transfer else { return [] }
        let from = t.arrivalPlatform ?? "?", to = t.departurePlatform ?? "?"
        if !hasLevelChange {
            return [
                WalkStep(icon: "figure.walk", color: Theme.go,
                         title: "Step off onto Platform \(from)", sub: "Platform \(to) is directly across the island"),
                WalkStep(icon: "checkmark", color: Theme.accent,
                         title: "Board on Platform \(to)", sub: "No stairs — very comfortable"),
            ]
        }
        return [
            WalkStep(icon: "clock", color: Theme.go,
                     title: "Off the train — walk toward sector C", sub: "Platform \(from) · the stairwell is at C"),
            WalkStep(icon: "stairs", color: Theme.stair,
                     title: "Stairs down to the underpass", sub: "escalator alongside · level 0 → −1"),
            WalkStep(icon: "arrow.right", color: Theme.accent,
                     title: "Along the underpass to the Platform \(to) stairwell", sub: "level −1"),
            WalkStep(icon: "checkmark", color: Theme.accent,
                     title: "Up the stairs — your train boards here", sub: "Platform \(to)"),
        ]
    }

    /// The keystone hook. Asks the repository for real `viz_export` geometry and
    /// builds a `WalkScene` the Canvas views project. The sample tier returns
    /// `ok == false`, so `scene` stays nil and the schematic stands.
    private func loadGeometry() async {
        defer { loading = false }
        guard let t = transfer, let key = WalkKey(transfer: t) else { return }
        if let result = await model.walk(for: key), result.ok, let export = result.export {
            let s = WalkScene(export)
            scene = s
            level = s.levelsAsc.contains(s.startLevel) ? s.startLevel : (s.levelsAsc.first ?? 0)
        }
    }
}

// MARK: - Schematic canvases

/// The section overview: platforms as slabs on level bands, the path dropping to
/// an underpass and back up with stair risers. A faithful port of the prototype's
/// section SVG, drawn to fit any width.
private struct SectionCanvas: View {
    let transfer: Transfer?
    let hasLevelChange: Bool

    var body: some View {
        Canvas { ctx, size in
            let w = size.width, h = size.height
            let l0 = h * 0.30            // platform level band
            let lm1 = h * 0.72           // underpass band

            // Level reference lines
            for y in [l0, lm1] {
                var p = SwiftPath(); p.move(to: CGPoint(x: 12, y: y)); p.addLine(to: CGPoint(x: w - 12, y: y))
                ctx.stroke(p, with: .color(Theme.line), style: StrokeStyle(lineWidth: 1, dash: [3, 4]))
            }

            let from = transfer?.arrivalPlatform ?? "?"
            let to = transfer?.departurePlatform ?? "?"

            if hasLevelChange {
                // Two upper platform slabs + one lower underpass slab
                slab(&ctx, CGRect(x: w * 0.06, y: l0 - 6, width: w * 0.22, height: 12))
                slab(&ctx, CGRect(x: w * 0.72, y: l0 - 6, width: w * 0.22, height: 12))
                slab(&ctx, CGRect(x: w * 0.28, y: lm1 - 6, width: w * 0.44, height: 12))

                let a = CGPoint(x: w * 0.17, y: l0)
                let b = CGPoint(x: w * 0.28, y: l0)
                let c = CGPoint(x: w * 0.36, y: lm1)
                let d = CGPoint(x: w * 0.64, y: lm1)
                let e = CGPoint(x: w * 0.72, y: l0)
                let f = CGPoint(x: w * 0.83, y: l0)

                // Path
                var path = SwiftPath()
                path.move(to: a); path.addLine(to: b); path.addLine(to: c)
                path.addLine(to: d); path.addLine(to: e); path.addLine(to: f)
                ctx.stroke(path, with: .color(Theme.accent),
                           style: StrokeStyle(lineWidth: 4.5, lineCap: .round, lineJoin: .round))
                // Stair risers highlighted
                for (s, t) in [(b, c), (d, e)] {
                    var r = SwiftPath(); r.move(to: s); r.addLine(to: t)
                    ctx.stroke(r, with: .color(Theme.stair), style: StrokeStyle(lineWidth: 5, lineCap: .round))
                }
                endpoint(&ctx, a, Theme.go)
                endpoint(&ctx, f, Theme.accent)
                label(&ctx, "Pl \(from)", at: CGPoint(x: a.x, y: l0 - 22))
                label(&ctx, "Pl \(to)", at: CGPoint(x: f.x, y: l0 - 22))
            } else {
                // Same island: flat path across one level.
                slab(&ctx, CGRect(x: w * 0.10, y: l0 - 6, width: w * 0.80, height: 12))
                let a = CGPoint(x: w * 0.22, y: l0 - 12)
                let f = CGPoint(x: w * 0.78, y: l0 - 12)
                var path = SwiftPath(); path.move(to: a); path.addLine(to: f)
                ctx.stroke(path, with: .color(Theme.accent),
                           style: StrokeStyle(lineWidth: 4.5, lineCap: .round))
                endpoint(&ctx, a, Theme.go)
                endpoint(&ctx, f, Theme.accent)
                label(&ctx, "Pl \(from)", at: CGPoint(x: a.x, y: l0 - 30))
                label(&ctx, "Pl \(to)", at: CGPoint(x: f.x, y: l0 - 30))
            }
        }
    }

    private func slab(_ ctx: inout GraphicsContext, _ rect: CGRect) {
        ctx.fill(SwiftPath(roundedRect: rect, cornerRadius: 3), with: .color(Theme.panel3))
    }
    private func endpoint(_ ctx: inout GraphicsContext, _ p: CGPoint, _ c: Color) {
        let r: CGFloat = 6
        ctx.fill(SwiftPath(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r)), with: .color(c))
    }
    private func label(_ ctx: inout GraphicsContext, _ text: String, at p: CGPoint) {
        ctx.drawText(text, .system(size: 10, weight: .bold, design: .monospaced), Theme.ink, at: p)
    }
}

/// A single floor plan. Schematic: on L0 the two platforms with the stairwell
/// between them; on L−1 the underpass corridor.
private struct LevelCanvas: View {
    let level: Int
    let transfer: Transfer?
    let hasLevelChange: Bool

    var body: some View {
        Canvas { ctx, size in
            let w = size.width, h = size.height
            let from = transfer?.arrivalPlatform ?? "?"
            let to = transfer?.departurePlatform ?? "?"

            if level == 0 || !hasLevelChange {
                platformBar(&ctx, CGRect(x: 16, y: h * 0.18, width: w - 32, height: h * 0.22), "PLATFORM \(from)")
                platformBar(&ctx, CGRect(x: 16, y: h * 0.60, width: w - 32, height: h * 0.22), "PLATFORM \(to)")
                if hasLevelChange {
                    stairwell(&ctx, CGRect(x: w * 0.45, y: h * 0.20, width: w * 0.10, height: h * 0.60))
                }
                // Arrive marker on upper platform, board marker on lower
                marker(&ctx, CGPoint(x: w * 0.70, y: h * 0.29), Theme.go, "off train")
                marker(&ctx, CGPoint(x: w * 0.62, y: h * 0.71), Theme.accent, "board")
            } else {
                // Underpass corridor
                ctx.fill(SwiftPath(roundedRect: CGRect(x: w * 0.32, y: h * 0.22, width: w * 0.36, height: h * 0.56), cornerRadius: 10),
                         with: .color(Theme.panel2))
                ctx.drawText("UNDERPASS · L−1", .system(size: 10, weight: .bold, design: .monospaced),
                             Theme.ink3, at: CGPoint(x: w * 0.5, y: h * 0.5))
                var p = SwiftPath(); p.move(to: CGPoint(x: w * 0.5, y: h * 0.26)); p.addLine(to: CGPoint(x: w * 0.5, y: h * 0.74))
                ctx.stroke(p, with: .color(Theme.accent), style: StrokeStyle(lineWidth: 4, lineCap: .round))
                stairwell(&ctx, CGRect(x: w * 0.44, y: h * 0.16, width: w * 0.12, height: h * 0.10))
                stairwell(&ctx, CGRect(x: w * 0.44, y: h * 0.74, width: w * 0.12, height: h * 0.10))
            }
        }
    }

    private func platformBar(_ ctx: inout GraphicsContext, _ rect: CGRect, _ title: String) {
        ctx.fill(SwiftPath(roundedRect: rect, cornerRadius: 5), with: .color(Theme.panel3))
        ctx.drawText(title, .system(size: 9, weight: .bold, design: .monospaced), Theme.ink2,
                     at: CGPoint(x: rect.minX + 44, y: rect.midY))
    }
    private func stairwell(_ ctx: inout GraphicsContext, _ rect: CGRect) {
        ctx.fill(SwiftPath(roundedRect: rect, cornerRadius: 4), with: .color(Theme.stair))
    }
    private func marker(_ ctx: inout GraphicsContext, _ p: CGPoint, _ c: Color, _ text: String) {
        ctx.fill(SwiftPath(ellipseIn: CGRect(x: p.x - 5, y: p.y - 5, width: 10, height: 10)), with: .color(c))
        ctx.drawText(text, .system(size: 8), Theme.ink3, at: CGPoint(x: p.x, y: p.y - 12))
    }
}

/// Alias so the file reads clearly (`Path` is also a SwiftUI view type name).
private typealias SwiftPath = Path

private extension GraphicsContext {
    /// Draw coloured text without tripping over `Text.foregroundStyle` returning
    /// `some View` (which `draw(_:at:)` won't accept) — resolve, then shade.
    func drawText(_ string: String, _ font: Font, _ color: Color, at point: CGPoint) {
        var resolved = resolve(Text(string).font(font))
        resolved.shading = .color(color)
        draw(resolved, at: point, anchor: .center)
    }
}
