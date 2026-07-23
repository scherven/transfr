import SwiftUI
import TransfrCore

/// The walk between two platforms of a journey's change — the prototype's `#s-walk`.
/// The surface itself is `WalkDetail`, shared with the verdict-free "Walk only" door
/// (`WalkLookupView`), so the two can't drift again; this screen supplies the
/// transfer's own fetch, its title, and the schematic it falls back to when `/walk`
/// returns no geometry for the station.
struct WalkView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings
    let transferIndex: Int

    @State private var scene: WalkScene?      // real geometry once /walk returns it
    @State private var boarding: BoardingGuidance?   // step-off guidance from the same /walk
    @State private var loading = true         // fetching that geometry (first load)

    private var transfer: Transfer? { model.transfers[safe: transferIndex] }
    private var hasLevelChange: Bool { (transfer?.verdictKind ?? .feasible) != .feasible }
    private var imperial: Bool { settings.units == .imperial }

    var body: some View {
        WalkDetail(
            scene: scene,
            loading: loading,
            fromRef: transfer?.shownArrivalPlatform ?? "?",
            toRef: transfer?.shownDeparturePlatform ?? "?",
            // The journey already measured this walk, so the facts stand before — and
            // without — any geometry. The lookup door has only the export.
            walkTimeS: transfer?.walkTimeS,
            walkDistanceM: transfer?.walkDistanceM,
            boarding: boarding,
            schematicLevels: hasLevelChange
                ? [WalkSchematicLevel(level: 0, label: "L0 · Platforms"),
                   WalkSchematicLevel(level: -1, label: "L−1 · Underpass")]
                : []
        ) { mode, level in
            switch mode {
            case .section: SectionCanvas(transfer: transfer, hasLevelChange: hasLevelChange)
            case .levels:  LevelCanvas(level: level, transfer: transfer, hasLevelChange: hasLevelChange)
            case .threeD:  threeDPlaceholder
            }
        }
        .navigationTitle(transfer?.atStation ?? "Walk")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .principal) { principal } }
        // Re-keyed on `avoidElevators` so flipping the preference refetches the
        // elevator-free variant (a different route, hence different geometry).
        .task(id: settings.avoidElevators) { await loadGeometry() }
    }

    private var principal: some View {
        VStack(spacing: 1) {
            Text(transfer?.atStation ?? "Walk").font(.system(size: 16, weight: .semibold))
            if let t = transfer {
                Text("Platform \(t.shownArrivalPlatform ?? "?") → \(t.shownDeparturePlatform ?? "?") · \(Fmt.distance(t.walkDistanceM, imperial: imperial)) · \(Fmt.walkTime(t.walkTimeS))")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
            }
        }
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

    /// The keystone hook. Asks the repository for real `viz_export` geometry and
    /// builds the `WalkScene` the Canvas views project. The sample tier returns
    /// `ok == false`, so `scene` stays nil and the schematic stands.
    private func loadGeometry() async {
        defer { loading = false }
        guard let t = transfer,
              let key = WalkKey(transfer: t, stepFree: settings.avoidElevators) else { return }
        if let result = await model.walkScene(for: key) {
            scene = result.scene
            boarding = result.boarding
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

            // The recovered public sign, like every other screen — a feed's internal
            // code drawn here would contradict the header right above it.
            let from = transfer?.shownArrivalPlatform ?? "?"
            let to = transfer?.shownDeparturePlatform ?? "?"

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
            let from = transfer?.shownArrivalPlatform ?? "?"
            let to = transfer?.shownDeparturePlatform ?? "?"

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
