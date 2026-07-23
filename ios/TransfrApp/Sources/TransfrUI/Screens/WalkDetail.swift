import SwiftUI
import TransfrCore

/// The walk-detail surface — **one** implementation, hosted by both doors that lead
/// to it: `WalkView` (a change of train inside a journey) and `WalkLookupView` (the
/// verdict-free "Walk only" door from the home screen).
///
/// The two screens grew this chrome independently and drifted: two `Mode` enums, two
/// stages, two turn-by-turn lists, and a "Levels"/"Level Δ" stat that reported a *net*
/// delta on both — so a route with six vertical transitions could read "+4" and one
/// with two could read "Δ 0". Everything geometry-driven now lives here; the screens
/// keep only their own fetch, their navigation title, and their own extras.
///
/// The reading order is facts → the honest caveat → the drawing → the steps: stats,
/// `guidanceBox`, mode picker, stage, turn-by-turn. Copy that points at the drawing
/// therefore says "below" on both doors.
struct WalkDetail<Fallback: View>: View {
    @Environment(SettingsStore.self) private var settings

    /// Real `viz_export` geometry, once `/walk` returns it. Nil means the caller's
    /// schematic `fallback` stands in.
    let scene: WalkScene?
    /// The first geometry fetch is still in flight — the stage shows a spinner rather
    /// than flashing a schematic that a real drawing is about to replace.
    let loading: Bool
    /// The two endpoint platforms, for copy and for the caller's schematic. These are
    /// *display* refs (the recovered public sign where the feed uses an internal
    /// code); `WalkKey` keeps the raw ones, because that is the routing key.
    let fromRef: String
    let toRef: String
    /// Walk time / distance as the *journey* measured them. The transfer door has
    /// these from the verdict before any geometry loads — and still has them when a
    /// station is mapped too thinly to draw — so they win over the geometry's own
    /// figures; the lookup door passes nil and the stats fall back to the export.
    var walkTimeS: Double? = nil
    var walkDistanceM: Double? = nil
    /// Step-off guidance from the same `/walk`, which sharpens the first turn-by-turn
    /// row from "step off on Platform X" to *where* on it. Transfer door only — a
    /// walk-only lookup has no arriving train to step off.
    var boarding: BoardingGuidance? = nil
    /// The facility this walk leads to ("walk to nearest"), named in the 3D legend and
    /// the browse caption. Non-nil also opens the surface in 3D, so the platform and
    /// the POI are both in view — that door's whole point.
    var facilityName: String? = nil
    /// Browse the whole station with the facility pinned, rather than draw a walk
    /// between two platforms. No walk facts and no turn-by-turn: a facility is a place
    /// to find, not a timed route.
    var browse: Bool = false
    /// The station the browse caption pins the facility on.
    var stationName: String? = nil
    /// The caller knows it is on the offline sample tier, where the absence of
    /// geometry is our own bundled data and not a thinly mapped station. Says so
    /// rather than blaming the map.
    var sampleTier: Bool = false
    /// Floors the caller's schematic can draw, for the level picker while there is no
    /// real geometry. Empty (the default) = the fallback is a single drawing.
    var schematicLevels: [WalkSchematicLevel] = []
    /// The drawing to show until real geometry arrives, per mode and floor. The two
    /// doors have genuinely different schematics — a transfer's platform/underpass
    /// section, a lookup's neutral two-platform bar — so it is passed in rather than
    /// branched on in here.
    @ViewBuilder let fallback: (WalkMode, Int) -> Fallback

    @State private var mode: WalkMode = .section
    /// Guards the one-time initial mode choice (a facility walk opens in 3D).
    @State private var didPickInitialMode = false
    /// The floor the plan tab draws, once the user has tapped the picker. Nil until
    /// then, so the plan opens on the floor you step off at — and a refetched route
    /// (flipping "avoid lifts" can change which floors the walk visits) can never
    /// leave it parked on a floor the new path never reaches.
    @State private var pickedLevel: Int?

    private var imperial: Bool { settings.units == .imperial }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if browse {
                    facilityBrowseStage
                } else {
                    statsRow
                    guidanceBox
                    modePicker
                    stage
                    turnByTurn
                }
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .onAppear {
            if !didPickInitialMode {
                didPickInitialMode = true
                if facilityName != nil { mode = .threeD }
            }
        }
    }

    // MARK: - Facts (they lead: what the walk costs, before any caveat)

    private var statsRow: some View {
        HStack {
            StatCell(key: "Walk time", value: Fmt.walkTime(shownWalkTimeS))
            StatCell(key: "Distance", value: Fmt.distance(shownWalkDistanceM, imperial: imperial))
            StatCell(key: "Level changes", value: levelChangesStat)
        }
    }

    private var shownWalkTimeS: Double? {
        walkTimeS ?? scene.flatMap { $0.found ? $0.export.path.walkingTimeSeconds : nil }
    }
    private var shownWalkDistanceM: Double? {
        walkDistanceM ?? scene.flatMap { $0.found ? $0.export.path.walkingDistanceMeters : nil }
    }

    /// How many times the walk changes floor — a **count**, not the net delta both
    /// doors used to show. The net flattens a route that drops to an underpass and
    /// climbs back into "no level change" (Dortmund 11→4: four changes, net 0) and
    /// reports a five-riser scramble as "+4". The count is what makes a change hard.
    ///
    /// "—" without geometry: the transfer door used to derive its stat from the
    /// *verdict* (anything not `feasible` ⇒ "−1"), which is a guess about level
    /// changes dressed as a measurement.
    private var levelChangesStat: String {
        guard let scene else { return "—" }
        return "\(scene.levelChangeCount)"
    }

    // MARK: - Guidance (the honest caveat, above the drawing it describes)

    @ViewBuilder
    private var guidanceBox: some View {
        if let scene {
            if !scene.found {
                infoBox(icon: "exclamationmark.triangle.fill", tint: Theme.miss, bg: Theme.missSoft,
                        lead: "These platforms aren't connected on the map.",
                        body: " The change may still be walkable — the detailed indoor route just isn't mapped.")
            } else if scene.transitions.isEmpty && !scene.pathChangesLevel {
                // Both halves are required. `transitions` describes the level changes
                // the map classified; the section renderer derives its risers from the
                // path polyline itself. A change present in the geometry but missing
                // from `transitions` is drawn as a grey riser while `isEmpty` still
                // reads true — which is how a green "step-free" claim came to sit above
                // a picture of the path dropping a floor.
                infoBox(icon: "figure.walk", tint: Theme.go, bg: Theme.goSoft,
                        lead: "One level, step-free.",
                        body: " Walk straight across between the platforms — no stairs.")
            } else if scene.transitions.isEmpty {
                infoBox(icon: "questionmark.circle", tint: Theme.nodata, bg: Theme.nodataSoft,
                        lead: "Level change not mapped.",
                        body: " The route drawn below changes level, but the map doesn't record stairs or a lift — follow station signs.")
            } else {
                infoBox(icon: settings.avoidElevators ? "figure.stairs" : "figure.walk", tint: Theme.go, bg: Theme.goSoft,
                        lead: connectorSummary(scene.transitions) + ".",
                        body: settings.avoidElevators
                            ? " Routed without lifts (Avoid lifts is on in Settings)."
                            : " \(WalkScene.label(forLevel: scene.startLevel)) → \(WalkScene.label(forLevel: scene.endLevel)).")
            }
        } else if sampleTier {
            infoBox(icon: "figure.walk", tint: Theme.go, bg: Theme.goSoft,
                    lead: "Schematic walk.",
                    body: " Live geometry for this station isn't in the offline sample — connect to the service to draw the real path.")
        } else if !loading {
            // Only once the fetch is done. Unguarded, this verdict on the station's
            // mapping showed on every load — including the ones that a moment later
            // drew a perfect walk.
            infoBox(icon: "map", tint: Theme.ink2, bg: Theme.panel2,
                    lead: "No drawable walk for these platforms yet.",
                    body: " The platforms are correct; this station just isn't mapped in enough detail to draw the indoor route. The diagram below is a schematic, not the real path.")
        }
    }

    /// Distinct connector kinds along the path, e.g. "Escalator + lift".
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

    // MARK: - Mode picker + stage (the shared geometry canvases)

    private var modePicker: some View {
        HStack(spacing: 6) {
            ForEach(WalkMode.allCases) { m in
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
                    stageBox { canvas(.section) }
                    legend([("Your path", Theme.accent), ("Stairs", Theme.stair),
                            ("Escalator", Theme.esc), ("Elevator", Theme.elev)])
                }
            }
        case .levels:
            VStack(spacing: 10) {
                levelPicker
                Panel(padding: 12) {
                    VStack(spacing: 10) {
                        stageBox { canvas(.levels) }
                        legend([("Path", Theme.accent), ("Platform", Theme.panel3), ("Connector", Theme.stair)])
                    }
                }
            }
        case .threeD:
            Panel(padding: 12) {
                VStack(spacing: 10) {
                    stageBox(height: 260) { canvas(.threeD) }
                    threeDLegend
                }
            }
        }
    }

    /// Real geometry once loaded; the caller's schematic otherwise.
    @ViewBuilder
    private func canvas(_ m: WalkMode) -> some View {
        if let scene {
            switch m {
            case .section: SectionGeometryCanvas(scene: scene)
            case .levels:  PlanGeometryCanvas(scene: scene, level: level)
            case .threeD:  IsoGeometryCanvas(scene: scene)
            }
        } else {
            fallback(m, level)
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

    /// The tapped facility on the whole-station 3D map: a caption naming it, the
    /// browse model with the POI pinned, and the station-map legend. No walk facts —
    /// a facility is a place to find, not a timed route.
    @ViewBuilder private var facilityBrowseStage: some View {
        infoBox(icon: "mappin.circle.fill", tint: Theme.poi, bg: Theme.poiSoft,
                lead: (facilityName ?? "This facility") + " ",
                body: "is pinned on \(stationName ?? "the station"). Drag to rotate, pinch to zoom, tap a floor to isolate it.")
        Panel(padding: 12) {
            VStack(spacing: 10) {
                stageBox(height: 340) {
                    if let scene { IsoGeometryCanvas(scene: scene, browse: true) }
                    else { fallback(.threeD, level) }
                }
                threeDLegend
            }
        }
    }

    // MARK: - Level picker

    /// The floor the plan draws: the user's pick while it is still on the route, else
    /// the floor you step off at.
    private var level: Int {
        guard let scene else { return pickedLevel ?? schematicLevels.first?.level ?? 0 }
        if let pickedLevel, scene.pathLevels.contains(pickedLevel) { return pickedLevel }
        return scene.pathLevels.contains(scene.startLevel) ? scene.startLevel : (scene.pathLevels.first ?? 0)
    }

    private var levelBinding: Binding<Int> {
        Binding(get: { level }, set: { pickedLevel = $0 })
    }

    @ViewBuilder
    private var levelPicker: some View {
        // `pathLevels`, not `levelsAsc`: the canvas draws the path, so the picker
        // must offer the floors the path visits. `levelsAsc` is the union over every
        // context way the search touched and tabs floors the walk never reaches —
        // Dortmund 11→4 offered L−2, which drew a floor with no route on it (#53).
        if let scene, scene.pathLevels.count > 1 {
            Picker("Level", selection: levelBinding) {
                ForEach(scene.pathLevels.reversed(), id: \.self) { lvl in
                    Text(levelPickerLabel(lvl, scene)).tag(lvl)
                }
            }.pickerStyle(.segmented)
        } else if scene == nil && schematicLevels.count > 1 {
            // No real geometry: only the caller knows what floors its own schematic
            // draws, so it supplies the tabs and their labels.
            Picker("Level", selection: levelBinding) {
                ForEach(schematicLevels) { s in Text(s.label).tag(s.level) }
            }.pickerStyle(.segmented)
        }
    }

    private func levelPickerLabel(_ lvl: Int, _ scene: WalkScene) -> String {
        var s = WalkScene.label(forLevel: lvl)
        if lvl == scene.startLevel { s += " · off" }
        else if lvl == scene.endLevel { s += " · board" }
        return s
    }

    // MARK: - Legends

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

    /// The 3D view's legend. Browsing a facility shows the station-map key with the
    /// facility named; a facility walk names it beside the path; a plain walk labels
    /// the path and the vertical circulation.
    @ViewBuilder private var threeDLegend: some View {
        if browse {
            legend([("Platform", Theme.panel3), ("Stairs", Theme.stair),
                    ("Lift", Theme.elev), (facilityName ?? "Facility", Theme.poi)])
        } else if let facilityName {
            legend([("Your path", Theme.accent), ("Platform", Theme.panel3), (facilityName, Theme.poi)])
        } else {
            legend([("Your path", Theme.accent), ("Platform", Theme.panel3),
                    ("Stairs", Theme.stair), ("Lift", Theme.elev)])
        }
    }

    // MARK: - Turn by turn (from the real transitions, never synthesised)

    /// Shown ONLY when we have real `viz_export` geometry — never a fabricated
    /// walkthrough. With no geometry `guidanceBox` says so instead of inventing
    /// "sector C / underpass" directions, which would read as real wayfinding for a
    /// station we haven't actually mapped.
    @ViewBuilder
    private var turnByTurn: some View {
        if let scene {
            VStack(alignment: .leading, spacing: 10) {
                Eyebrow(text: "Turn by turn")
                ForEach(scene.turnByTurn(imperial: imperial, boarding: boarding)) { step in
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
}

// MARK: - Supporting types

/// The three renderers of the one decoded walk (DESIGN.md §13.3, "one contract,
/// four renderers"): the longitudinal section, the per-floor plans, and the
/// draggable 3D.
enum WalkMode: String, CaseIterable, Identifiable { case section, levels, threeD
    var id: String { rawValue }
    var label: String { self == .threeD ? "3D" : rawValue.capitalized }
    var icon: String {
        switch self { case .section: "chart.bar.xaxis"; case .levels: "square.stack.3d.up"; case .threeD: "cube" }
    }
}

/// One floor a screen's *schematic* fallback can draw, with the label the level
/// picker gives it. The fallback drawing belongs to the calling screen, so the tabs
/// over it do too.
struct WalkSchematicLevel: Identifiable, Hashable {
    let level: Int
    let label: String
    var id: Int { level }
}

extension WalkScene {
    /// How many times the drawn path crosses between floors.
    ///
    /// Read off the path's own level sequence rather than `transitions`, because the
    /// two disagree exactly where honesty matters: a level change the geometry
    /// contains but `transitions` never describes is still drawn as a riser (see
    /// `pathChangesLevel`), and a stat of 0 beside a picture of the path dropping a
    /// floor is the same contradiction the step-free banner used to make. On every
    /// committed fixture the two agree (Berlin 4, Dortmund 4, Essen 2) — the count
    /// only diverges where the map left a gap.
    ///
    /// Lives here rather than beside the other derivations because the stat cell is
    /// its only consumer; the renderers read `levelRuns` / `pathLevels`.
    var levelChangeCount: Int {
        var count = 0
        var previous: Int?
        for p in pathPoints {
            let l = level(of: p.z)
            if let previous, previous != l { count += 1 }
            previous = l
        }
        return count
    }
}

extension TripModel {
    /// The one fetch shape both walk doors use: ask the repository for this walk's
    /// real `viz_export` and project it into the `WalkScene` the detail draws.
    ///
    /// `nil` means there is nothing to draw — the offline sample tier returns
    /// `ok == false`, and so does a station mapped too thinly to route indoors — and
    /// the caller keeps its schematic rather than showing an empty stage.
    func walkScene(for key: WalkKey) async -> (scene: WalkScene, boarding: BoardingGuidance?)? {
        guard let result = await walk(for: key), result.ok, let export = result.export else { return nil }
        return (WalkScene(export), result.boarding)
    }
}
