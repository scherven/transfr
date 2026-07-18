import SwiftUI
import TransfrCore

/// Nearest facility — the prototype's `#s-nearest` (§6.10), now a **map**. Query a
/// station, pick a category chip, and the whole station is drawn in 3D with EVERY
/// facility of that type pinned on it (all toilets, all coffee, …). Tap a pin to
/// select it: a card names it (level · distance) and offers "Show walk", which opens
/// the walk to the platform beside it. Facilities + geometry come from `/facility-map`
/// in one round trip; when a station has none mapped — or the POI layer isn't
/// available on the host — we say so rather than guess.
struct NearestFacilityView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings

    private struct Coord: Equatable { var lat: Double; var lon: Double }
    private struct Cat: Identifiable, Equatable { let id: String; let label: String; let icon: String }
    private let cats: [Cat] = [
        .init(id: "toilets", label: "Toilets", icon: "toilet"),
        .init(id: "coffee", label: "Coffee", icon: "cup.and.saucer"),
        .init(id: "food", label: "Food", icon: "fork.knife"),
        .init(id: "atm", label: "ATM", icon: "creditcard"),
        .init(id: "tickets", label: "Tickets", icon: "ticket"),
        .init(id: "shops", label: "Shops", icon: "bag"),
        .init(id: "pharmacy", label: "Pharmacy", icon: "cross.case"),
        .init(id: "taxi", label: "Taxi", icon: "car"),
    ]

    @State private var category = "toilets"
    @State private var stationText = "Berlin Hbf"
    @State private var coord: Coord?
    @State private var map: FacilityMapResponse?
    @State private var scene: WalkScene?
    @State private var selected: Int?
    @State private var loading = false

    // Station autocomplete (mirrors InputView).
    @FocusState private var stationFocused: Bool
    @State private var suggestions: [StationSuggestion] = []
    @State private var searchTask: Task<Void, Never>?

    private var imperial: Bool { settings.units == .imperial }
    private var categoryLabel: String { cats.first { $0.id == category }?.label ?? category.capitalized }
    private var categoryIcon: String { cats.first { $0.id == category }?.icon ?? "mappin" }

    var body: some View {
        VStack(spacing: 0) {
            controls
            mapArea
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Nearest facility").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text("Nearest facility").font(.system(size: 16, weight: .semibold))
                    Text(subtitle).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                }
            }
        }
        .task { if map == nil { await resolve(stationText) } }
        .onChange(of: category) { _, _ in Task { await loadMap() } }
    }

    private var subtitle: String {
        guard let m = map, m.found else { return stationName }
        let n = m.facilities.count
        return "\(m.station ?? stationName) · \(n) \(categoryLabel.lowercased())"
    }
    private var stationName: String { map?.station ?? stationText }

    // MARK: - Controls (station + category chips)

    private var controls: some View {
        VStack(alignment: .leading, spacing: 8) {
            stationField
            if stationFocused && !suggestions.isEmpty { suggestionList }
            categoryChips
        }
        .padding(.horizontal, 16).padding(.top, 12).padding(.bottom, 10)
        .background(Theme.paper)
    }

    private var stationField: some View {
        Panel(padding: 6) {
            HStack(spacing: 12) {
                Circle().fill(Theme.accent).frame(width: 10, height: 10)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Station").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                    TextField("Station", text: $stationText)
                        .font(.system(size: 17, weight: .semibold)).foregroundStyle(Theme.ink)
                        .textInputAutocapitalization(.words).autocorrectionDisabled()
                        .focused($stationFocused)
                        .submitLabel(.search)
                        .onChange(of: stationText) { _, new in if stationFocused { scheduleSearch(new) } }
                        .onSubmit { Task { await resolve(stationText) } }
                }
                Spacer(minLength: 0)
                if loading { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 12).padding(.vertical, 12)
        }
    }

    private var suggestionList: some View {
        Panel(padding: 0) {
            VStack(spacing: 0) {
                ForEach(Array(suggestions.prefix(6).enumerated()), id: \.offset) { i, s in
                    if i > 0 { Divider().overlay(Theme.line).padding(.leading, 44) }
                    Button { pick(s) } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "mappin.circle.fill")
                                .font(.system(size: 18)).foregroundStyle(Theme.ink3).frame(width: 20)
                            Text(s.name).font(.system(size: 15, weight: .medium)).foregroundStyle(Theme.ink)
                            Spacer(minLength: 8)
                            if let c = s.country, !c.isEmpty {
                                Text(c).font(.system(size: 11, weight: .semibold, design: .monospaced))
                                    .foregroundStyle(Theme.ink3)
                            }
                        }
                        .padding(.horizontal, 12).padding(.vertical, 12).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
            }
        }
    }

    private var categoryChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(cats) { c in
                    Button { withAnimation(.snappy) { category = c.id } } label: {
                        Label(c.label, systemImage: c.icon)
                            .font(.system(size: 12.5, weight: .medium))
                            .foregroundStyle(category == c.id ? .white : Theme.ink2)
                            .padding(.horizontal, 12).padding(.vertical, 8)
                            .background(Capsule().fill(category == c.id ? Theme.accent : Theme.panel))
                            .overlay(Capsule().strokeBorder(category == c.id ? .clear : Theme.line, lineWidth: 1))
                    }.buttonStyle(.plain)
                }
            }
        }
    }

    // MARK: - Map area (the 3D station + every pin) + the tapped-pin card

    private var mapArea: some View {
        ZStack(alignment: .bottom) {
            Group {
                if let scene {
                    IsoGeometryCanvas(
                        scene: scene, browse: true, selectedPOI: selected,
                        onSelectPOI: { i in withAnimation(.snappy) { selected = (selected == i ? nil : i) } }
                    )
                } else if let map, !map.found, !loading {
                    emptyState(map)
                } else if !loading {
                    Text("Pick a station and a category to see them on the map.")
                        .font(.system(size: 13)).foregroundStyle(Theme.ink3)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            if loading && scene == nil {
                VStack(spacing: 8) {
                    ProgressView()
                    Text("Finding \(categoryLabel.lowercased())…").font(.system(size: 13)).foregroundStyle(Theme.ink3)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            if let sel = selected, let m = map, sel < m.facilities.count {
                detailCard(m.facilities[sel])
                    .padding(16)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            } else if scene != nil {
                hint.padding(.bottom, 12)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipped()
    }

    private var hint: some View {
        HStack(spacing: 6) {
            Circle().fill(Theme.poi).frame(width: 8, height: 8)
            Text("Tap a pin for details").font(.system(size: 11)).foregroundStyle(Theme.ink2)
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(Capsule().fill(Theme.panel.opacity(0.95)))
        .overlay(Capsule().strokeBorder(Theme.line, lineWidth: 1))
    }

    private func detailCard(_ f: Facility) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                SetIcon(categoryIcon, tint: .white, bg: Theme.poi)
                VStack(alignment: .leading, spacing: 2) {
                    Text(f.name ?? categoryLabel).font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
                    Text(cardSub(f)).font(.system(size: 12)).foregroundStyle(Theme.ink3)
                }
                Spacer(minLength: 8)
                Button { withAnimation(.snappy) { selected = nil } } label: {
                    Image(systemName: "xmark.circle.fill").font(.system(size: 22)).foregroundStyle(Theme.ink3)
                }.buttonStyle(.plain)
            }
            Button { showWalk(f) } label: {
                HStack(spacing: 8) {
                    Image(systemName: "figure.walk")
                    Text("Show walk").fontWeight(.semibold)
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold))
                }
                .font(.system(size: 14)).foregroundStyle(.white)
                .padding(.vertical, 12).padding(.horizontal, 14)
                .frame(maxWidth: .infinity)
                .background(RoundedRectangle(cornerRadius: 12).fill(Theme.accent))
            }.buttonStyle(.plain)
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(Theme.line, lineWidth: 1))
        .shadow(color: .black.opacity(0.14), radius: 14, y: 4)
    }

    /// "Level 1 · 40 m · near Pl 8" — the mapped level, straight-line distance from
    /// the station centre, and the platform the facility sits by.
    private func cardSub(_ f: Facility) -> String {
        var parts: [String] = []
        if let lvl = f.level, !lvl.isEmpty { parts.append("Level \(lvl)") }
        parts.append(Fmt.distance(f.distanceM, imperial: imperial))
        if let p = f.nearestPlatform { parts.append("near Pl \(p)") }
        return parts.joined(separator: " · ")
    }

    // MARK: - Empty / degraded states (honest, per reason)

    @ViewBuilder private func emptyState(_ r: FacilityMapResponse) -> some View {
        let (icon, title, body): (String, String, String) = {
            switch r.reason {
            case "no_poi_layer":
                return ("map", "Facility map not available here",
                        "The OSM amenity/shop layer isn't loaded for \(r.station ?? "this station") on this server, so we can't map facilities rather than guess. Routing and platform data still work.")
            case "none_mapped":
                return ("mappin.slash", "None mapped",
                        "\(r.station ?? "This station") has no \(categoryLabel.lowercased()) tagged in OpenStreetMap. That's an honest gap, not a routing error.")
            case "station_unresolved":
                return ("questionmark.circle", "No station found",
                        "We couldn't resolve a station near that place. Try another name.")
            case "too_sparse":
                return ("square.dashed", "Not enough map detail",
                        "\(r.station ?? "This station") isn't mapped in enough detail to draw a 3D model yet.")
            case "unsupported_category":
                return ("tag", "Category unavailable",
                        "We don't map \(categoryLabel.lowercased()) as a facility yet.")
            default:
                return ("exclamationmark.triangle", "No facilities",
                        r.reason.map { "Couldn't map facilities (\($0))." } ?? "Couldn't map facilities.")
            }
        }()
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: icon).font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
            Text(body).font(.system(size: 13)).foregroundStyle(Theme.ink3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line, lineWidth: 1))
        .padding(20)
    }

    // MARK: - Actions

    /// Open the walk to a facility: from the station's first platform to the platform
    /// it sits by (a drawn route + the facility pinned at the end). When the facility
    /// has no distinct platform anchor, browse the whole station with it highlighted.
    private func showWalk(_ f: Facility) {
        guard let m = map, let rel = m.relationId, rel != 0, let export = m.export else { return }
        let poi = f.lat.flatMap { lat in f.lon.map { lon in
            WalkPOI(lat: lat, lon: lon, name: f.name, category: f.category, subtype: f.subtype, level: f.level)
        } }
        let refs = platformRefs(export)
        let station = m.station ?? stationName
        if let to = f.nearestPlatform, let from = refs.first, to != from {
            model.walkLookup = TripModel.WalkLookup(
                station: station, relationId: rel, fromPlatform: from, toPlatform: to, poi: poi)
        } else if let first = refs.first, let last = refs.last, first != last {
            model.walkLookup = TripModel.WalkLookup(
                station: station, relationId: rel, fromPlatform: first, toPlatform: last, poi: poi, browse: true)
        } else {
            return
        }
        model.path.append(.walkLookup)
    }

    /// Distinct platform refs the station's geometry carries, numerically sorted —
    /// the anchors a walk can start from / route to.
    private func platformRefs(_ export: VizExport) -> [String] {
        var refs = Set<String>()
        for w in export.ways where w.kind == "platform" { if let r = w.ref { refs.insert(r) } }
        return refs.sorted { $0.compare($1, options: .numeric) == .orderedAscending }
    }

    // MARK: - Autocomplete + loading

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

    private func pick(_ s: StationSuggestion) {
        stationText = s.name
        searchTask?.cancel()
        suggestions = []
        stationFocused = false
        Task { await resolve(s.name) }
    }

    private func resolve(_ name: String) async {
        let q = name.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        loading = true
        defer { loading = false }
        let hits = await model.stations(matching: q)
        guard let top = hits.first(where: { $0.latitude != nil && $0.longitude != nil }),
              let lat = top.latitude, let lon = top.longitude else {
            map = FacilityMapResponse(lat: 0, lon: 0, category: category,
                                      found: false, reason: "station_unresolved")
            scene = nil
            return
        }
        coord = Coord(lat: lat, lon: lon)
        stationText = top.name
        await loadMap()
    }

    private func loadMap() async {
        guard let c = coord else { return }
        loading = true
        defer { loading = false }
        selected = nil
        let r = await model.facilityMap(lat: c.lat, lon: c.lon, category: category)
        map = r
        if let r, r.found, let export = r.export {
            scene = WalkScene(export)
        } else {
            scene = nil
        }
    }
}
