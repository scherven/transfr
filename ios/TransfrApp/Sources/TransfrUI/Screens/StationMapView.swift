import SwiftUI
import TransfrCore

/// Advanced-mode **station map** — browse any station's real 3D layout with no
/// walk selected: platforms (labelled + lifted to their floor), walkways, and
/// every stair / escalator / lift. Search a station, then rotate and zoom the
/// exploded model. Uses the same interactive renderer the walk views use, in
/// `browse` mode (all connectors, no route). Reached from the Advanced hub.
struct StationMapView: View {
    @Environment(TripModel.self) private var model

    @State private var query = ""
    @State private var suggestions: [StationSuggestion] = []
    @State private var station: Resolved?
    @State private var scene: WalkScene?
    @State private var loading = false
    @State private var message: String?

    private struct Resolved {
        let name: String
        let platforms: [String]
        /// The feed's platform-number labels (the ones OSM lacks) for the overlay.
        let markers: [PlatformMarker]
        /// The platform refs OSM already labels — the overlay's neutral/accent split.
        let osmRefs: Set<String>
    }

    var body: some View {
        Group {
            if let station, let scene { mapStage(station, scene) }
            else { search }
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Station map").navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - Search

    private var search: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Explore a station in 3D")
                    .font(.system(size: 20, weight: .semibold)).foregroundStyle(Theme.ink)
                Text("Search a station to see its platforms, walkways, and the stairs, escalators and lifts between them.")
                    .font(.system(size: 13)).foregroundStyle(Theme.ink2)

                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass").foregroundStyle(Theme.ink3)
                    TextField("Station name", text: $query)
                        .textFieldStyle(.plain).autocorrectionDisabled()
                        .onChange(of: query) { _, q in Task { await autocomplete(q) } }
                }
                .padding(12)
                .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line, lineWidth: 1))

                if loading { ProgressView().frame(maxWidth: .infinity).padding(.top, 6) }
                if let message {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 12)).foregroundStyle(Theme.tight)
                }

                ForEach(suggestions.indices, id: \.self) { i in
                    let s = suggestions[i]
                    Button { Task { await resolve(s) } } label: {
                        HStack(spacing: 10) {
                            Image(systemName: "tram.fill").font(.system(size: 13)).foregroundStyle(Theme.accent)
                            Text(s.name).font(.system(size: 14)).foregroundStyle(Theme.ink)
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                        }
                        .padding(.horizontal, 14).padding(.vertical, 12)
                        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
                        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(20)
        }
    }

    // MARK: - Map

    private func mapStage(_ st: Resolved, _ sc: WalkScene) -> some View {
        VStack(spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(st.name).font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.ink)
                    Text("\(st.platforms.count) platforms · \(sc.levelsAsc.count) levels")
                        .font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
                }
                Spacer()
                Button("Change") { station = nil; scene = nil }
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.accent)
            }
            .padding(.horizontal, 20).padding(.vertical, 12)
            .overlay(alignment: .bottom) { Rectangle().fill(Theme.line2).frame(height: 1) }

            IsoGeometryCanvas(scene: sc, browse: true,
                              markers: st.markers, osmPlatforms: st.osmRefs)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            legend
        }
    }

    private var legend: some View {
        HStack(spacing: 14) {
            legendItem("Platform", Theme.panel3)
            legendItem("Stairs", Theme.stair)
            legendItem("Escalator", Theme.esc)
            legendItem("Lift", Theme.elev)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20).padding(.vertical, 10)
        .overlay(alignment: .top) { Rectangle().fill(Theme.line2).frame(height: 1) }
    }

    private func legendItem(_ name: String, _ color: Color) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(name).font(.system(size: 11)).foregroundStyle(Theme.ink3)
        }
    }

    // MARK: - Data

    private func autocomplete(_ q: String) async {
        let q = q.trimmingCharacters(in: .whitespaces)
        guard q.count >= 2 else { suggestions = []; return }
        suggestions = await model.stations(matching: q)
    }

    /// Resolve a picked station to a drawable whole-station scene: its platforms +
    /// relation from `/station-platforms`, then one `/walk` across the station
    /// (first ↔ last platform) whose context ways carry the layout. Rendered in
    /// browse mode, so the route is ignored and every connector shows.
    private func resolve(_ s: StationSuggestion) async {
        guard let lat = s.latitude, let lon = s.longitude else {
            message = "That station has no location to map."; return
        }
        loading = true; message = nil
        defer { loading = false }
        guard let resp = await model.stationPlatforms(lat: lat, lon: lon),
              resp.found, let rel = resp.relationId, resp.platforms.count >= 2 else {
            message = "Couldn't load this station's platforms."; return
        }
        let refs = resp.platforms
        // Browse the whole station: `allPlatforms` pulls in every platform, not
        // just the ones on the first→last corridor.
        let key = WalkKey(relationId: rel, fromPlatform: refs.first!, toPlatform: refs.last!,
                          stepFree: false, allPlatforms: true)
        guard let result = await model.walk(for: key), result.ok, let export = result.export else {
            message = "No drawable geometry for this station yet."; return
        }
        // Overlay the feed's platform labels (the ones OSM lacks). Honest-empty
        // when the host has no harvested overlay or this station wasn't harvested,
        // so the map then shows OSM refs only — never fabricated labels.
        let feed = await model.stationPlatformMarkers(lat: lat, lon: lon)
        let markers = feed?.found == true ? feed!.platforms : []
        station = Resolved(name: s.name, platforms: refs,
                           markers: markers, osmRefs: Set(refs))
        scene = WalkScene(export)
        query = ""; suggestions = []
    }
}
