import SwiftUI
import TransfrCore

/// Direct platform-to-platform walk — the prototype's `#s-walklookup` (§6.9/§7.10).
/// The verdict-free door: no journey, no layover, no verdict — so the *facts* lead
/// (distance / walk time / level Δ). Driven by `model.walkLookup` (a station resolved
/// to a relation + two of its real platforms in `InputView`), it fetches that walk's
/// `viz_export` from `/walk` and projects it through the **same** `WalkScene` +
/// canvases the transfer walk uses (Section / Levels / 3D + turn-by-turn). When no
/// geometry is available (the offline sample tier, relationId 0) it degrades to a
/// small schematic rather than nothing.
struct WalkLookupView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings

    @State private var mode: Mode = .section
    @State private var level = 0
    @State private var scene: WalkScene?
    @State private var loading = true

    enum Mode: String, CaseIterable, Identifiable { case section, levels, threeD
        var id: String { rawValue }
        var label: String { self == .threeD ? "3D" : rawValue.capitalized }
        var icon: String {
            switch self { case .section: "chart.bar.xaxis"; case .levels: "square.stack.3d.up"; case .threeD: "cube" }
        }
    }

    private var lookup: TripModel.WalkLookup? { model.walkLookup }
    private var imperial: Bool { settings.units == .imperial }
    private var fromRef: String { lookup?.fromPlatform ?? "?" }
    private var toRef: String { lookup?.toPlatform ?? "?" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                factsRow
                guidanceBox
                modePicker
                stage
                turnByTurn
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle(lookup?.station ?? "Walk").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .principal) { principal } }
        // Re-keyed on `avoidElevators` so flipping the preference refetches the
        // elevator-free variant (a different route, hence different geometry).
        .task(id: settings.avoidElevators) { await load() }
    }

    private var principal: some View {
        VStack(spacing: 1) {
            Text(lookup?.station ?? "Walk").font(.system(size: 16, weight: .semibold))
            Text(subtitle).font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
        }
    }

    private var subtitle: String {
        var parts = ["Platform \(fromRef) → \(toRef)"]
        if let s = scene, s.found {
            parts.append(Fmt.distance(s.export.path.walkingDistanceMeters, imperial: imperial))
            parts.append(Fmt.walkTime(s.export.path.walkingTimeSeconds))
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Facts (lead, no verdict)

    private var factsRow: some View {
        HStack {
            StatCell(key: "Walk time", value: scene.map { $0.found ? Fmt.walkTime($0.export.path.walkingTimeSeconds) : "—" } ?? "—")
            StatCell(key: "Distance", value: scene.map { $0.found ? Fmt.distance($0.export.path.walkingDistanceMeters, imperial: imperial) : "—" } ?? "—")
            StatCell(key: "Level Δ", value: scene.map { levelDelta($0) } ?? "—")
        }
    }

    private func levelDelta(_ s: WalkScene) -> String {
        let d = s.endLevel - s.startLevel
        return d == 0 ? "0" : (d > 0 ? "+\(d)" : "−\(abs(d))")
    }

    // MARK: - Guidance box (verdict-free narration derived from the real geometry)

    @ViewBuilder
    private var guidanceBox: some View {
        if let s = scene, !s.found {
            infoBox(icon: "exclamationmark.triangle.fill", tint: Theme.miss, bg: Theme.missSoft,
                    lead: "These platforms aren't connected on the map.",
                    body: " Pick a different pair, or try a nearby station.")
        } else if let s = scene {
            let verticals = s.transitions
            if verticals.isEmpty {
                infoBox(icon: "figure.walk", tint: Theme.go, bg: Theme.goSoft,
                        lead: "One level, step-free.",
                        body: " Walk straight across — no stairs between Platform \(fromRef) and \(toRef).")
            } else {
                infoBox(icon: settings.avoidElevators ? "figure.stairs" : "figure.walk", tint: Theme.go, bg: Theme.goSoft,
                        lead: connectorSummary(verticals) + ".",
                        body: settings.avoidElevators
                            ? " Routed without lifts (Avoid lifts is on in Settings)."
                            : " \(WalkScene.label(forLevel: s.startLevel)) → \(WalkScene.label(forLevel: s.endLevel)).")
            }
        } else if lookup?.relationId == 0 {
            infoBox(icon: "figure.walk", tint: Theme.go, bg: Theme.goSoft,
                    lead: "Schematic walk.",
                    body: " Live geometry for this station isn't in the offline sample — connect to the service to draw the real path.")
        } else {
            infoBox(icon: "map", tint: Theme.ink2, bg: Theme.panel2,
                    lead: "No drawable geometry for these platforms.",
                    body: " The station may not be mapped in enough detail yet.")
        }
    }

    /// "Escalator + lift", "Stairs", etc. — the distinct connector kinds along the path.
    private func connectorSummary(_ transitions: [VizExport.Transition]) -> String {
        var seen: [String] = []
        for t in transitions where !seen.contains(WalkConnector.verb(t.kind)) {
            seen.append(WalkConnector.verb(t.kind))
        }
        return seen.joined(separator: " + ")
    }

    private func infoBox(icon: String, tint: Color, bg: Color, lead: String, body: String) -> some View {
        HStack(spacing: 10) {
            SetIcon(icon, tint: tint, bg: bg)
            (Text(lead).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.ink)
             + Text(body).font(.system(size: 13)).foregroundColor(Theme.ink2))
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(bg))
    }

    // MARK: - Mode picker + stage (shared geometry canvases)

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
                    stageBox { canvas(for: .section) }
                    legend([("Your path", Theme.accent), ("Stairs", Theme.stair),
                            ("Escalator", Theme.esc), ("Elevator", Theme.elev)])
                }
            }
        case .levels:
            VStack(spacing: 10) {
                levelPicker
                Panel(padding: 12) {
                    VStack(spacing: 10) {
                        stageBox { canvas(for: .levels) }
                        legend([("Path", Theme.accent), ("Platform", Theme.panel3), ("Connector", Theme.stair)])
                    }
                }
            }
        case .threeD:
            Panel(padding: 12) {
                stageBox(height: 260) { canvas(for: .threeD) }
            }
        }
    }

    /// Real geometry once loaded; the schematic fallback otherwise.
    @ViewBuilder
    private func canvas(for m: Mode) -> some View {
        if let scene {
            switch m {
            case .section: SectionGeometryCanvas(scene: scene)
            case .levels:  PlanGeometryCanvas(scene: scene, level: level)
            case .threeD:  IsoGeometryCanvas(scene: scene)
            }
        } else {
            SchematicLookupCanvas(from: fromRef, to: toRef)
        }
    }

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
        }
    }

    private func levelPickerLabel(_ lvl: Int, _ scene: WalkScene) -> String {
        var s = WalkScene.label(forLevel: lvl)
        if lvl == scene.startLevel { s += " · off" }
        else if lvl == scene.endLevel { s += " · board" }
        return s
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

    // MARK: - Turn by turn (from the real transitions)

    @ViewBuilder
    private var turnByTurn: some View {
        if let scene {
            VStack(alignment: .leading, spacing: 10) {
                Eyebrow(text: "Turn by turn")
                ForEach(scene.turnByTurn(imperial: imperial)) { step in
                    stepRow(step)
                }
            }
        }
    }

    private func stepRow(_ step: WalkStep) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: step.icon).font(.system(size: 12, weight: .bold)).foregroundStyle(.white)
                .frame(width: 26, height: 26).background(Circle().fill(step.color))
            VStack(alignment: .leading, spacing: 1) {
                Text(step.title).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Text(step.sub).font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: - Load

    /// Fetch the walk's geometry for the resolved lookup and build the scene the
    /// canvases project. relationId 0 (sample tier) or a non-`ok` result leaves
    /// `scene` nil, so the schematic stands.
    private func load() async {
        loading = true
        defer { loading = false }
        guard let lk = lookup, lk.relationId != 0 else { scene = nil; return }
        let key = WalkKey(relationId: lk.relationId, fromPlatform: lk.fromPlatform,
                          toPlatform: lk.toPlatform, stepFree: settings.avoidElevators)
        if let result = await model.walk(for: key), result.ok, let export = result.export {
            let s = WalkScene(export)
            scene = s
            level = s.levelsAsc.contains(s.startLevel) ? s.startLevel : (s.levelsAsc.first ?? 0)
        } else {
            scene = nil
        }
    }
}

/// A neutral flat two-platform section, drawn when no real geometry is available
/// (offline sample tier). Just enough to keep the verdict-free door coherent.
private struct SchematicLookupCanvas: View {
    let from: String
    let to: String

    var body: some View {
        Canvas { ctx, size in
            let w = size.width, h = size.height
            let y = h * 0.5
            var line = Path(); line.move(to: CGPoint(x: 12, y: y)); line.addLine(to: CGPoint(x: w - 12, y: y))
            ctx.stroke(line, with: .color(Theme.line), style: StrokeStyle(lineWidth: 1, dash: [3, 4]))

            ctx.fill(Path(roundedRect: CGRect(x: w * 0.10, y: y - 6, width: w * 0.80, height: 12), cornerRadius: 3),
                     with: .color(Theme.panel3))

            let a = CGPoint(x: w * 0.22, y: y - 12), b = CGPoint(x: w * 0.78, y: y - 12)
            var p = Path(); p.move(to: a); p.addLine(to: b)
            ctx.stroke(p, with: .color(Theme.accent), style: StrokeStyle(lineWidth: 4.5, lineCap: .round))
            for (pt, c) in [(a, Theme.go), (b, Theme.accent)] {
                ctx.fill(Path(ellipseIn: CGRect(x: pt.x - 6, y: pt.y - 6, width: 12, height: 12)), with: .color(c))
            }
            for (pt, ref) in [(a, from), (b, to)] {
                var t = ctx.resolve(Text("Pl \(ref)").font(.system(size: 10, weight: .bold, design: .monospaced)))
                t.shading = .color(Theme.ink)
                ctx.draw(t, at: CGPoint(x: pt.x, y: y - 30), anchor: .center)
            }
        }
    }
}
