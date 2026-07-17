import SwiftUI
import TransfrCore

/// Nearest facility — the prototype's `#s-nearest` (§6.10), now live. Query any
/// station, pick a category chip, and see the nearest instance (routed from the
/// reference platform) plus every instance ranked by distance. Tapping a routable
/// facility opens the walk to the platform beside it. Facilities come from the OSM
/// `amenity`/`shop` POI layer via `/facilities`; when a station has none mapped —
/// or the POI layer isn't available on the host — we say so rather than guess.
struct NearestFacilityView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings

    /// A resolved station: coordinate + the relation/platforms a routed walk uses.
    private struct Resolved: Equatable {
        var name: String
        var lat: Double
        var lon: Double
        var relationId: Int
        var platforms: [String]
    }

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
    @State private var resolved: Resolved?
    @State private var fromPlatform = ""
    @State private var result: FacilitiesResponse?
    @State private var loading = false
    @State private var infoNote: String?

    // Station autocomplete (mirrors InputView).
    @FocusState private var stationFocused: Bool
    @State private var suggestions: [StationSuggestion] = []
    @State private var searchTask: Task<Void, Never>?

    private var imperial: Bool { settings.units == .imperial }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                stationField
                if stationFocused && !suggestions.isEmpty { suggestionList.padding(.bottom, 8) }
                if let resolved, resolved.platforms.count > 1 { fromPlatformPicker(resolved).padding(.bottom, 12) }

                categoryChips.padding(.bottom, 14)

                content
            }
            .padding(20)
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
        .task { if resolved == nil { await resolveStation(stationText) } }
        .onChange(of: category) { _, _ in Task { await loadFacilities() } }
    }

    private var subtitle: String {
        guard let r = resolved else { return "Pick a station" }
        return fromPlatform.isEmpty ? r.name : "\(r.name) · from Platform \(fromPlatform)"
    }

    // MARK: - Station field + autocomplete

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
                        .onSubmit { Task { await resolveStation(stationText) } }
                }
                Spacer(minLength: 0)
                if loading { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 12).padding(.vertical, 12)
        }
        .padding(.bottom, suggestions.isEmpty || !stationFocused ? 12 : 6)
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
        Task { await resolveStation(s.name) }
    }

    // MARK: - From-platform picker (the reference the nearest is routed from)

    private func fromPlatformPicker(_ r: Resolved) -> some View {
        HStack(spacing: 8) {
            Text("From").font(.system(size: 12)).foregroundStyle(Theme.ink3)
            Menu {
                Picker("From platform", selection: $fromPlatform) {
                    ForEach(r.platforms, id: \.self) { Text("Platform \($0)").tag($0) }
                }
            } label: {
                HStack(spacing: 6) {
                    Text(fromPlatform.isEmpty ? "—" : "Platform \(fromPlatform)")
                        .font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 10, weight: .bold)).foregroundStyle(Theme.ink3)
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(Capsule().fill(Theme.panel))
                .overlay(Capsule().strokeBorder(Theme.line, lineWidth: 1))
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: - Category chips

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

    // MARK: - Content

    @ViewBuilder private var content: some View {
        if loading && result == nil {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Finding \(categoryLabel.lowercased())…").font(.system(size: 13)).foregroundStyle(Theme.ink3)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 20)
        } else if let r = result, r.found, !r.facilities.isEmpty {
            found(r)
        } else if let r = result {
            emptyState(r)
        } else {
            Text("Pick a station to find facilities near it.")
                .font(.system(size: 13)).foregroundStyle(Theme.ink3).padding(.vertical, 20)
        }
    }

    private var categoryLabel: String { cats.first { $0.id == category }?.label ?? category.capitalized }
    private var categoryIcon: String { cats.first { $0.id == category }?.icon ?? "mappin" }

    @ViewBuilder private func found(_ r: FacilitiesResponse) -> some View {
        let nearest = r.facilities[0]
        // Nearest result card (routable = has a platform anchor we can walk to).
        Button { open(nearest) } label: { nearestCard(nearest) }
            .buttonStyle(.plain).padding(.bottom, 8)

        if let note = infoNote {
            HStack(spacing: 10) {
                Image(systemName: "info.circle").foregroundStyle(Theme.accent)
                Text(note).font(.system(size: 12)).foregroundStyle(Theme.ink2)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12).fill(Theme.accentSoft))
            .padding(.bottom, 14)
        }

        Text("All \(categoryLabel.lowercased()) at this station · \(r.facilities.count) found")
            .font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.ink3)
            .padding(.horizontal, 4).padding(.bottom, 8)

        ForEach(r.facilities) { f in
            Button { open(f) } label: { facilityRow(f) }.buttonStyle(.plain)
        }
    }

    private func nearestCard(_ f: Facility) -> some View {
        HStack(spacing: 10) {
            SetIcon(categoryIcon, tint: .white, bg: Theme.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(f.name ?? categoryLabel).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Text(subLine(f)).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 1) {
                Text(Fmt.distance(f.distanceM, imperial: imperial))
                    .font(.system(size: 14, weight: .bold, design: .monospaced)).foregroundStyle(Theme.ink)
                if let w = f.walkTimeS {
                    Text(Fmt.walkTime(w)).font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.accent.opacity(0.4), lineWidth: 1.5))
    }

    private func facilityRow(_ f: Facility) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(f.name ?? categoryLabel).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Text(subLine(f)).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            if isRoutable(f) {
                Image(systemName: "figure.walk").font(.system(size: 11)).foregroundStyle(Theme.go)
            }
            Text(Fmt.distance(f.distanceM, imperial: imperial))
                .font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
                .frame(minWidth: 52, alignment: .trailing)
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line, lineWidth: 1))
        .padding(.bottom, 6)
    }

    /// "level 0 · via Platform 3" — the mapped level and the routed anchor.
    private func subLine(_ f: Facility) -> String {
        var parts: [String] = []
        if let lvl = f.level, !lvl.isEmpty { parts.append("level \(lvl)") }
        if let sub = f.subtype, f.name == nil { parts.append(sub) }
        if let p = f.nearestPlatform { parts.append("via Platform \(p)") }
        return parts.isEmpty ? (f.subtype ?? f.category) : parts.joined(separator: " · ")
    }

    // MARK: - Empty / degraded states (honest, per reason)

    @ViewBuilder private func emptyState(_ r: FacilitiesResponse) -> some View {
        let (icon, title, body): (String, String, String) = {
            switch r.reason {
            case "no_poi_layer":
                return ("map", "Facility map not available here",
                        "The OSM amenity/shop layer isn't loaded for \(r.station ?? "this station") on this server, so we can't list facilities rather than guess. Routing and platform data still work.")
            case "none_mapped":
                return ("mappin.slash", "None mapped",
                        "\(r.station ?? "This station") has no \(categoryLabel.lowercased()) tagged in OpenStreetMap. That's an honest gap, not a routing error.")
            case "station_unresolved":
                return ("questionmark.circle", "No station found",
                        "We couldn't resolve a station near that place. Try another name.")
            case "unsupported_category":
                return ("tag", "Category unavailable",
                        "We don't map \(categoryLabel.lowercased()) as a facility yet.")
            default:
                return ("exclamationmark.triangle", "No facilities",
                        r.reason.map { "Couldn't list facilities (\($0))." } ?? "Couldn't list facilities.")
            }
        }()
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: icon).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink)
            Text(body).font(.system(size: 12)).foregroundStyle(Theme.ink3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line, lineWidth: 1))
    }

    // MARK: - Actions

    /// A facility is routable when it has a platform anchor at a real (non-sample)
    /// relation that differs from the reference platform — i.e. there's a
    /// platform-to-platform walk to draw.
    private func isRoutable(_ f: Facility) -> Bool {
        guard let to = f.nearestPlatform, let r = resolved, r.relationId != 0 else { return false }
        return to != fromPlatform
    }

    private func open(_ f: Facility) {
        guard let r = resolved else { return }
        guard let to = f.nearestPlatform else {
            infoNote = "\(f.name ?? categoryLabel) isn't anchored to a platform, so there's no walk to draw — it's \(Fmt.distance(f.distanceM, imperial: imperial)) from the station centre."
            return
        }
        if r.relationId == 0 || to == fromPlatform {
            infoNote = to == fromPlatform
                ? "You're already on the platform nearest \(f.name ?? "this facility")."
                : "Walk geometry isn't available offline — this is the ranked list; connect to route it."
            return
        }
        infoNote = nil
        // Carry the tapped facility along so the walk geometry draws it beside the
        // destination platform — the POI's own coordinate/level (from `/facilities`)
        // is all the server needs to place it in the 3D model.
        let poi = f.lat.flatMap { lat in f.lon.map { lon in
            WalkPOI(lat: lat, lon: lon, name: f.name, category: f.category,
                    subtype: f.subtype, level: f.level)
        } }
        model.walkLookup = TripModel.WalkLookup(
            station: r.name, relationId: r.relationId,
            fromPlatform: fromPlatform.isEmpty ? to : fromPlatform, toPlatform: to,
            poi: poi)
        model.path.append(.walkLookup)
    }

    // MARK: - Loading

    private func resolveStation(_ name: String) async {
        let q = name.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        loading = true
        defer { loading = false }
        // Coordinate from autocomplete, then platforms + relation from that point.
        let hits = await model.stations(matching: q)
        guard let top = hits.first(where: { $0.latitude != nil && $0.longitude != nil }),
              let lat = top.latitude, let lon = top.longitude else {
            result = FacilitiesResponse(lat: 0, lon: 0, category: category, found: false,
                                        reason: "station_unresolved")
            return
        }
        let platforms = await model.stationPlatforms(lat: lat, lon: lon)
        let r = Resolved(name: platforms?.station ?? top.name, lat: lat, lon: lon,
                         relationId: platforms?.relationId ?? 0, platforms: platforms?.platforms ?? [])
        resolved = r
        if fromPlatform.isEmpty || !r.platforms.contains(fromPlatform) {
            fromPlatform = r.platforms.first ?? ""
        }
        await loadFacilities()
    }

    private func loadFacilities() async {
        guard let r = resolved else { return }
        infoNote = nil
        loading = true
        defer { loading = false }
        result = await model.facilities(lat: r.lat, lon: r.lon, category: category)
    }
}
