import SwiftUI
import TransfrCore

/// Direct platform-to-platform walk — the prototype's `#s-walklookup` (§6.9/§7.10).
/// The verdict-free door: no journey, no layover, no verdict — so the *facts* lead
/// (distance / walk time / level changes). Driven by `model.walkLookup` (a station
/// resolved to a relation + two of its real platforms in `InputView`), it fetches that
/// walk's `viz_export` from `/walk` and hands it to `WalkDetail`, the **same** surface
/// a journey's transfer walk uses. When no geometry is available (the offline sample
/// tier, relationId 0) it degrades to a small schematic rather than nothing.
struct WalkLookupView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings

    @State private var scene: WalkScene?
    @State private var loading = true

    private var lookup: TripModel.WalkLookup? { model.walkLookup }
    private var imperial: Bool { settings.units == .imperial }
    private var fromRef: String { lookup?.fromPlatform ?? "?" }
    private var toRef: String { lookup?.toPlatform ?? "?" }

    private var isBrowse: Bool { lookup?.browse == true }

    var body: some View {
        WalkDetail(
            scene: scene,
            loading: loading,
            fromRef: fromRef,
            toRef: toRef,
            facilityName: facilityName,
            browse: isBrowse,
            stationName: lookup?.station,
            sampleTier: lookup?.relationId == 0
        ) { _, _ in
            // One neutral schematic for every mode — this door has no verdict to
            // shape a section around, so there's nothing honest to vary.
            SchematicLookupCanvas(from: fromRef, to: toRef)
        }
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
        if isBrowse {
            let n = scene?.export.ways.filter { $0.kind == "platform" }.count ?? 0
            return n > 0 ? "\(facilityName ?? "Facility") · \(n) platforms" : (facilityName ?? "Facility")
        }
        var parts = ["Platform \(fromRef) → \(toRef)"]
        if let s = scene, s.found {
            parts.append(Fmt.distance(s.export.path.walkingDistanceMeters, imperial: imperial))
            parts.append(Fmt.walkTime(s.export.path.walkingTimeSeconds))
        }
        return parts.joined(separator: " · ")
    }

    /// A short, human label for the walk's facility (name, else tidied subtype,
    /// else category), or nil for a plain platform-to-platform lookup.
    private var facilityName: String? {
        guard let poi = lookup?.poi else { return nil }
        return poi.name
            ?? poi.subtype?.replacingOccurrences(of: "_", with: " ").capitalized
            ?? poi.category.capitalized
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
                          toPlatform: lk.toPlatform, stepFree: settings.avoidElevators,
                          allPlatforms: lk.browse,
                          fromLat: lk.fromLat, fromLon: lk.fromLon,
                          toLat: lk.toLat, toLon: lk.toLon, poi: lk.poi)
        scene = await model.walkScene(for: key)?.scene
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
