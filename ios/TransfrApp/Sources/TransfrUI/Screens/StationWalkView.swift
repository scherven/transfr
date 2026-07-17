import SwiftUI
import TransfrCore

/// Full station walk — the prototype's `#s-stationwalk` (§6.10). Now LIVE: pick a
/// station (autocomplete → `/station-platforms` resolves its relation + real
/// platforms), pick a source platform, and `/station-walk` runs one pathfind from
/// it to every other platform — distance / walk time to each, in platform-ref order,
/// with an "avoid lifts" marker. Tapping a reachable row opens the full walk view (§6.5)
/// for the real `(relation, from, to)`. Degrades gracefully: an unmapped station or
/// the offline sample tier still populates (sample rows are synthesized; a tapped
/// row falls back to the schematic).
struct StationWalkView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings

    // Station query + autocomplete
    @State private var station = "Berlin Hbf"
    @FocusState private var stationFocused: Bool
    @State private var suggestions: [StationSuggestion] = []
    @State private var searchTask: Task<Void, Never>?

    // Resolution (coordinate → platforms + relation) and the fetched walk
    @State private var resolved: StationPlatformsResponse?
    @State private var resolvedForStation = ""
    @State private var coord: (lat: Double, lon: Double)?
    @State private var source = ""
    @State private var walk: StationWalkResponse?
    @State private var resolving = false
    @State private var loadingWalk = false

    private var imperial: Bool { settings.units == .imperial }

    /// The resolved station's real platforms, but only while they still match the
    /// station text (editing the name reverts to needing a fresh pick).
    private var platforms: [String] {
        guard resolvedForStation == station.trimmingCharacters(in: .whitespaces),
              let refs = resolved?.platforms, !refs.isEmpty else { return [] }
        return refs
    }

    /// A change to any of these re-runs the pathfind: the resolved station, the
    /// source platform, or the "avoid lifts" preference (a different, elevator-free route).
    private struct FetchKey: Hashable { var station: String; var source: String; var stepFree: Bool }
    private var fetchKey: FetchKey { .init(station: resolvedForStation, source: source, stepFree: settings.avoidElevators) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                stationField
                if stationFocused { suggestionList }
                sourcePicker
                content
                footerNote
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Full station walk").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .principal) { principal } }
        .task { await resolveDefaultIfNeeded() }
        .task(id: fetchKey) { await runWalk() }
    }

    private var principal: some View {
        VStack(spacing: 1) {
            Text("Full station walk").font(.system(size: 16, weight: .semibold))
            Text(subtitle).font(.system(size: 11)).foregroundStyle(Theme.ink3)
        }
    }

    private var subtitle: String {
        let name = walk?.station ?? resolved?.station ?? station
        if let count = resolved?.platforms.count, count > 0 { return "\(name) · \(count) platforms" }
        return name
    }

    // MARK: - Station field + autocomplete

    private var stationField: some View {
        Panel(padding: 6) {
            HStack(spacing: 12) {
                Circle().fill(Theme.accent).frame(width: 10, height: 10)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Station").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                    TextField("Station", text: $station)
                        .font(.system(size: 17, weight: .semibold)).foregroundStyle(Theme.ink)
                        .textInputAutocapitalization(.words).autocorrectionDisabled()
                        .focused($stationFocused)
                        .submitLabel(.search)
                        .onChange(of: station) { _, new in if stationFocused { scheduleSearch(new) } }
                        .onChange(of: stationFocused) { _, now in if now { scheduleSearch(station) } }
                }
                Spacer(minLength: 0)
                if resolving { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 12).padding(.vertical, 12)
        }
    }

    @ViewBuilder private var suggestionList: some View {
        if !suggestions.isEmpty {
            Panel(padding: 0) {
                VStack(spacing: 0) {
                    ForEach(Array(suggestions.prefix(6).enumerated()), id: \.offset) { i, s in
                        if i > 0 { Divider().overlay(Theme.line).padding(.leading, 44) }
                        Button { pick(s) } label: { suggestionRow(s) }.buttonStyle(.plain)
                    }
                }
            }
            .padding(.top, 8)
        }
    }

    private func suggestionRow(_ s: StationSuggestion) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "mappin.circle.fill").font(.system(size: 18))
                .foregroundStyle(Theme.ink3).frame(width: 20)
            Text(s.name).font(.system(size: 15, weight: .medium)).foregroundStyle(Theme.ink)
            Spacer(minLength: 8)
            if let c = s.country, !c.isEmpty {
                Text(c).font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Theme.ink3)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 12).contentShape(Rectangle())
    }

    // MARK: - Source platform picker

    @ViewBuilder private var sourcePicker: some View {
        if !platforms.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Walk from").font(.system(size: 11.5)).foregroundStyle(Theme.ink3)
                Menu {
                    Picker("Walk from", selection: $source) {
                        ForEach(platforms, id: \.self) { ref in Text("Platform \(ref)").tag(ref) }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(source.isEmpty ? "—" : "Platform \(source)")
                            .font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.ink3)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line, lineWidth: 1))
                }
            }
            .padding(.top, 14)
        }
    }

    // MARK: - Results / states

    @ViewBuilder private var content: some View {
        if let walk, walk.found, !walk.results.isEmpty {
            resultsSection(walk)
        } else if loadingWalk || (resolving && walk == nil) {
            loadingPlaceholder
        } else if let resolved, !resolved.found {
            degraded("This station isn't mapped in enough detail to walk yet.")
        } else if walk != nil {
            degraded("This station has just the one platform — nothing to walk to.")
        } else {
            prompt
        }
    }

    private func resultsSection(_ walk: StationWalkResponse) -> some View {
        let rows = walk.results
        let dists = rows.filter(\.found).compactMap(\.walkDistanceM)
        return VStack(alignment: .leading, spacing: 0) {
            HStack {
                StatCell(key: "Reachable", value: "\(dists.count) / \(rows.count)")
                StatCell(key: "Nearest", value: dists.min().map { Fmt.distance($0, imperial: imperial) } ?? "—")
                StatCell(key: "Farthest", value: dists.max().map { Fmt.distance($0, imperial: imperial) } ?? "—")
            }
            .padding(.top, 14).padding(.bottom, 14)

            columnHeader
            sourceRow
            ForEach(rows) { row in
                if row.found {
                    Button { openWalk(row) } label: { destRow(row) }.buttonStyle(.plain)
                } else {
                    destRow(row)
                }
            }
        }
    }

    private var columnHeader: some View {
        HStack {
            Text("From Platform \(source)").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.ink3)
            Spacer()
            Text("walk").font(.system(size: 11)).foregroundStyle(Theme.ink3).frame(width: 62, alignment: .trailing)
            Text("dist").font(.system(size: 11)).foregroundStyle(Theme.ink3).frame(width: 52, alignment: .trailing)
        }
        .padding(.horizontal, 4).padding(.bottom, 8)
    }

    private var sourceRow: some View {
        platformRow(name: "Platform \(source)", note: "you are here",
                    walk: "—", dist: "—", found: true, isSource: true)
    }

    private func destRow(_ row: StationWalkRow) -> some View {
        platformRow(
            name: "Platform \(row.toPlatform)",
            note: row.found ? nil : humanReason(row.reason),
            walk: row.found ? Fmt.walkTime(row.walkTimeS) : "—",
            dist: row.found ? Fmt.distance(row.walkDistanceM, imperial: imperial) : "—",
            found: row.found, isSource: false)
    }

    private func platformRow(name: String, note: String?, walk: String, dist: String,
                             found: Bool, isSource: Bool) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(.system(size: 14, weight: .medium))
                    .foregroundStyle(found ? Theme.ink : Theme.ink3)
                if let note {
                    Text(note).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                }
            }
            Spacer()
            Text(walk).font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(found ? Theme.ink : Theme.ink3).frame(width: 62, alignment: .trailing)
            Text(dist).font(.system(size: 13, design: .monospaced))
                .foregroundStyle(Theme.ink2).frame(width: 52, alignment: .trailing)
            marker(found: found, isSource: isSource)
        }
        .padding(.horizontal, 12).padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: 12).fill(isSource ? Theme.accentSoft : Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(isSource ? Theme.accent.opacity(0.3) : Theme.line, lineWidth: 1))
        .padding(.bottom, 6)
    }

    /// Source pin, an unreachable ✕, the green stairs figure (when Avoid lifts is
    /// on — every found row is then routed without lifts), or a tap chevron otherwise.
    @ViewBuilder private func marker(found: Bool, isSource: Bool) -> some View {
        if isSource {
            Image(systemName: "mappin.circle.fill").font(.system(size: 12))
                .foregroundStyle(Theme.accent).frame(width: 16)
        } else if !found {
            Image(systemName: "xmark.circle").font(.system(size: 12))
                .foregroundStyle(Theme.miss.opacity(0.7)).frame(width: 16)
        } else if settings.avoidElevators {
            Image(systemName: "figure.stairs").font(.system(size: 12))
                .foregroundStyle(Theme.go).frame(width: 16)
        } else {
            Image(systemName: "chevron.right").font(.system(size: 11, weight: .bold))
                .foregroundStyle(Theme.ink3).frame(width: 16)
        }
    }

    private var loadingPlaceholder: some View {
        VStack(spacing: 10) {
            ProgressView()
            Text("Finding walks from Platform \(source.isEmpty ? "…" : source)…")
                .font(.system(size: 12)).foregroundStyle(Theme.ink3)
        }
        .frame(maxWidth: .infinity).padding(.top, 40)
    }

    private func degraded(_ message: String) -> some View {
        Label(message, systemImage: "exclamationmark.triangle")
            .font(.system(size: 13)).foregroundStyle(Theme.ink2).padding(.top, 20)
    }

    private var prompt: some View {
        Label("Pick a station above to see the walk from one platform to every other.",
              systemImage: "figure.walk")
            .font(.system(size: 13)).foregroundStyle(Theme.ink3).padding(.top, 20)
    }

    private var footerNote: some View {
        Label(note, systemImage: "figure.walk")
            .font(.system(size: 12)).foregroundStyle(Theme.ink3).padding(.top, 12)
    }

    private var note: String {
        let base = "Tap a reachable platform to see the walk as a full 3D view."
        return settings.avoidElevators
            ? base + " Routed without lifts; the green figure marks a reachable platform."
            : base
    }

    private func humanReason(_ reason: String?) -> String {
        switch reason {
        case "platform_not_found":            return "platform not on the map here"
        case "disconnected",
             "no_coordinates_for_platform_nodes": return "no mapped path between them"
        case "exceeded_plausibility_bound":   return "no plausible walking route"
        default:                              return "not connected on the map"
        }
    }

    // MARK: - Actions

    /// Debounced station lookup for the focused field (mirrors InputView): under two
    /// characters shows nothing; a cancelled task supersedes the last keystroke.
    private func scheduleSearch(_ query: String) {
        searchTask?.cancel()
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard q.count >= 2 else { suggestions = []; return }
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(180))
            if Task.isCancelled { return }
            let results = await model.stations(matching: q)
            if Task.isCancelled { return }
            suggestions = results
        }
    }

    /// Commit a suggestion: set the field, dismiss the list, and resolve the
    /// station's platforms so the source picker and results adapt to it.
    private func pick(_ s: StationSuggestion) {
        station = s.name
        searchTask?.cancel(); suggestions = []; stationFocused = false
        guard let lat = s.latitude, let lon = s.longitude else { return }
        Task { await resolve(name: s.name, lat: lat, lon: lon) }
    }

    /// On first appear (or after the view is re-pushed), resolve the default
    /// station so the tool is populated out of the box. No-op once resolved.
    private func resolveDefaultIfNeeded() async {
        guard resolved == nil, !resolving else { return }
        let name = station.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        let hits = await model.stations(matching: name)
        guard let top = hits.first, let lat = top.latitude, let lon = top.longitude else { return }
        station = top.name
        await resolve(name: top.name, lat: lat, lon: lon)
    }

    /// Resolve a station's coordinate to its platforms (+ relation id) and default
    /// the source platform to the first one. Setting `resolvedForStation` / `source`
    /// changes `fetchKey`, which drives the `/station-walk` fetch.
    private func resolve(name: String, lat: Double, lon: Double) async {
        resolving = true
        defer { resolving = false }
        coord = (lat, lon)
        let r = await model.stationPlatforms(lat: lat, lon: lon)
        resolved = r
        resolvedForStation = name.trimmingCharacters(in: .whitespaces)
        if let r, r.found, !r.platforms.isEmpty {
            if !r.platforms.contains(source) { source = r.platforms.first ?? "" }
        } else {
            source = ""      // unmapped: nothing to walk from; content shows the degraded state
        }
    }

    /// Fetch the full station walk for the resolved station + source platform.
    /// Guards until a station is resolved and a source is chosen; fails soft to a
    /// nil `walk` (the degraded/empty state) via the non-throwing model passthrough.
    private func runWalk() async {
        guard let coord, !source.isEmpty,
              resolvedForStation == station.trimmingCharacters(in: .whitespaces) else { walk = nil; return }
        loadingWalk = true
        defer { loadingWalk = false }
        walk = await model.stationWalk(lat: coord.lat, lon: coord.lon,
                                       fromPlatform: source, stepFree: settings.avoidElevators)
    }

    /// Open the full walk view for a real `(relation, from, to)`. relationId 0
    /// (sample tier) still navigates — `WalkLookupView` draws its schematic there.
    private func openWalk(_ row: StationWalkRow) {
        guard let rel = walk?.relationId else { return }
        model.walkLookup = TripModel.WalkLookup(
            station: walk?.station ?? station,
            relationId: rel,
            fromPlatform: source,
            toPlatform: row.toPlatform)
        model.path.append(.walkLookup)
    }
}
