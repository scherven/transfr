import SwiftUI
import TransfrCore

/// Map health — the prototype's `#s-maphealth` (§6.10, §7.11). Per-database
/// connectivity (connected / stitchable / island), a region selector, and an
/// all-databases comparison. Read-only and diagnostic. The region figures are the
/// measured `stitch_survey.py` sweeps (EU/KR) plus an illustrative JP; the "Query
/// a station" section drills into ONE station live via `/station-health`, running
/// the same connected/stitchable/island classification over its platform pairs.
struct MapHealthView: View {
    @Environment(TripModel.self) private var model
    @State private var region = "Europe"

    // Query-a-station state: a debounced autocomplete over `model.stations`, then
    // the picked suggestion's coordinate drives one `/station-health` call. `result`
    // holds that station's live breakdown; `querying` covers the in-flight call.
    @State private var stationQuery = ""
    @FocusState private var searchFocused: Bool
    @State private var suggestions: [StationSuggestion] = []
    @State private var searchTask: Task<Void, Never>?
    @State private var result: StationHealthResponse?
    @State private var queriedName = ""
    @State private var querying = false

    private struct Health { let connected, stitchable, island: Int; let sampled: String; let measured: Bool
        let stations: [(String, String, Color)] }

    private var health: Health {
        switch region {
        case "Korea":
            return .init(connected: 2, stitchable: 5, island: 93, sampled: "899 platforms", measured: true,
                         stations: [("Seoul", "mainline — only tracks between islands", Theme.miss),
                                    ("Busan", "routes via building perimeter", Theme.miss),
                                    ("Daejeon", "concourse unmapped", Theme.tight)])
        case "Japan":
            return .init(connected: 61, stitchable: 16, island: 23, sampled: "illustrative until the sweep runs", measured: false,
                         stations: [("Tōkyō", "dense concourse, well mapped", Theme.go),
                                    ("Shinjuku", "partial — some islands unlinked", Theme.tight)])
        default:
            return .init(connected: 71, stitchable: 5, island: 24, sampled: "1,401 platforms", measured: true,
                         stations: [("Berlin Hbf", "fully connected — 15/15 platforms route", Theme.go),
                                    ("Colmar", "A→E stitchable — a 3.9 m inferred bridge", Theme.tight),
                                    ("Olten", "9→12 disconnected — straight-line estimate", Theme.miss)])
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text("Database").font(.system(size: 11.5)).foregroundStyle(Theme.ink3).padding(.bottom, 8)
                SegmentedControl(options: ["Europe", "Korea", "Japan"], selection: $region) { $0 }
                    .padding(.bottom, 14)

                SetCard {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("\(health.connected)% connected across \(health.sampled)\(health.measured ? "" : " · illustrative")")
                            .font(.system(size: 11.5)).foregroundStyle(Theme.ink3)
                        ZoneBar(zones: [(health.connected, Theme.go), (health.stitchable, Theme.tight), (health.island, Theme.miss)])
                            .padding(.top, 12)
                        HStack {
                            legendDot("connected", Theme.go); Spacer()
                            legendDot("stitchable", Theme.tight); Spacer()
                            legendDot("island", Theme.miss)
                        }.padding(.top, 8)
                    }
                }

                SectionHeader(text: "Representative stations")
                ForEach(Array(health.stations.enumerated()), id: \.offset) { _, st in
                    HStack(spacing: 10) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(st.0).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                            Text(st.1).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                        }
                        Spacer()
                        Circle().fill(st.2).frame(width: 10, height: 10)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 11)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line, lineWidth: 1))
                    .padding(.bottom, 6)
                }

                queryStationSection

                SectionHeader(text: "All databases · connectivity")
                SetCard {
                    VStack(spacing: 10) {
                        compareRow("EU", 71, 5, 24)
                        compareRow("JP", 61, 16, 23)
                        compareRow("KR", 2, 5, 93)
                        HStack(spacing: 12) {
                            legendDot("connected", Theme.go)
                            legendDot("stitchable", Theme.tight)
                            legendDot("island", Theme.miss)
                            Spacer()
                        }.padding(.top, 4)
                    }
                }

                Label("transfr_eu (71 / 5 / 24, 1,401 platforms) and transfr_kr (2 / 5 / 93, 899) are measured stitch_survey.py sweeps; JP is illustrative until its sweep finishes. Query a station to run the same connected / stitchable / island classification over its own platform pairs, live.",
                      systemImage: "info.circle")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3).padding(.top, 12)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Map health").navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - Query a station

    private var queryStationSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(text: "Query a station")

            // Search field — a debounced autocomplete over the same station index
            // the planner uses; picking a suggestion runs one /station-health call.
            SetCard {
                HStack(spacing: 10) {
                    Image(systemName: "magnifyingglass").font(.system(size: 14, weight: .medium))
                        .foregroundStyle(Theme.ink3)
                    TextField("Search a station", text: $stationQuery)
                        .font(.system(size: 15, weight: .medium)).foregroundStyle(Theme.ink)
                        .textInputAutocapitalization(.words).autocorrectionDisabled()
                        .focused($searchFocused)
                        .submitLabel(.search)
                        .onChange(of: stationQuery) { _, new in
                            if searchFocused { scheduleSearch(new) }
                        }
                    if !stationQuery.isEmpty {
                        Button {
                            stationQuery = ""; suggestions = []; searchTask?.cancel()
                        } label: {
                            Image(systemName: "xmark.circle.fill").font(.system(size: 15))
                                .foregroundStyle(Theme.ink3)
                        }.buttonStyle(.plain)
                    }
                }
            }

            if searchFocused && !suggestions.isEmpty {
                suggestionList.padding(.top, 8)
            }

            queryResult.padding(.top, 12)
        }
        .padding(.top, 6)
    }

    @ViewBuilder private var suggestionList: some View {
        SetCard {
            VStack(spacing: 0) {
                ForEach(Array(suggestions.prefix(6).enumerated()), id: \.offset) { i, s in
                    if i > 0 { Divider().overlay(Theme.line) }
                    Button { pick(s) } label: {
                        HStack(spacing: 10) {
                            Image(systemName: "mappin.circle.fill").font(.system(size: 16))
                                .foregroundStyle(Theme.ink3).frame(width: 18)
                            Text(s.name).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                            Spacer(minLength: 8)
                            if let c = s.country, !c.isEmpty {
                                Text(c).font(.system(size: 11, weight: .semibold, design: .monospaced))
                                    .foregroundStyle(Theme.ink3)
                            }
                        }
                        .padding(.vertical, 9).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
            }
        }
    }

    @ViewBuilder private var queryResult: some View {
        if querying {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Classifying \(queriedName.isEmpty ? "the station" : queriedName)'s platform pairs…")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
        } else if let r = result {
            if r.found {
                foundResult(r)
            } else {
                Label("No mapped station near \(queriedName). Health needs a station in the loaded map.",
                      systemImage: "mappin.slash")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
        } else {
            Label("Pick a station to score every one of its platform pairs — the same connected / stitchable / island split as the region sweep, run live for that one station.",
                  systemImage: "sparkle.magnifyingglass")
                .font(.system(size: 12)).foregroundStyle(Theme.ink3)
        }
    }

    private func foundResult(_ r: StationHealthResponse) -> some View {
        SetCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text(r.station ?? queriedName).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink)
                    Spacer(minLength: 8)
                    Text("\(r.platformCount) platforms").font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Theme.ink3)
                }
                if r.pairCount == 0 {
                    Text("Only \(r.platformCount) platform — no pairs to connect.")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).padding(.top, 6)
                } else {
                    Text("\(Int(r.connectedPct.rounded()))% connected across \(r.pairCount) platform pairs\(r.sampled ? " · sampled" : "")")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).padding(.top, 6)
                    ZoneBar(zones: [(r.connected, Theme.go), (r.stitchable, Theme.tight), (r.island, Theme.miss)])
                        .padding(.top, 12)
                    HStack {
                        legendDot("connected \(r.connected)", Theme.go); Spacer()
                        legendDot("stitchable \(r.stitchable)", Theme.tight); Spacer()
                        legendDot("island \(r.island)", Theme.miss)
                    }.padding(.top, 8)

                    if !r.examples.isEmpty {
                        Divider().overlay(Theme.line).padding(.vertical, 10)
                        Text("DISCONNECTED PAIRS").font(.system(size: 9.5, weight: .semibold)).tracking(1)
                            .foregroundStyle(Theme.ink3).padding(.bottom, 6)
                        ForEach(Array(r.examples.enumerated()), id: \.offset) { _, p in
                            HStack(spacing: 8) {
                                Circle().fill(kindColor(p.kind)).frame(width: 8, height: 8)
                                Text("\(p.fromPlatform) → \(p.toPlatform)")
                                    .font(.system(size: 12, weight: .medium, design: .monospaced)).foregroundStyle(Theme.ink2)
                                Spacer(minLength: 8)
                                Text(p.kind).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                            }
                            .padding(.vertical, 3)
                        }
                    }
                }
            }
        }
    }

    private func kindColor(_ kind: String) -> Color { kind == "stitchable" ? Theme.tight : Theme.miss }

    /// Debounced station lookup for the search field. Under two characters we show
    /// nothing; a cancelled task supersedes the last keystroke so results never race.
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

    /// Commit a suggestion, dismiss the list, and — if it carries a coordinate —
    /// resolve its live station health. A suggestion without a coordinate can't be
    /// located, so it just fills the field.
    private func pick(_ s: StationSuggestion) {
        stationQuery = s.name
        searchTask?.cancel()
        suggestions = []
        searchFocused = false
        guard let lat = s.latitude, let lon = s.longitude else { return }
        Task { await runQuery(name: s.name, lat: lat, lon: lon) }
    }

    private func runQuery(name: String, lat: Double, lon: Double) async {
        querying = true
        queriedName = name
        defer { querying = false }
        result = await model.stationHealth(lat: lat, lon: lon)
    }

    private func legendDot(_ text: String, _ color: Color) -> some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 8, height: 8)
            Text(text).font(.system(size: 10.5)).foregroundStyle(Theme.ink3)
        }
    }

    private func compareRow(_ label: String, _ c: Int, _ s: Int, _ i: Int) -> some View {
        HStack(spacing: 10) {
            Text(label).font(.system(size: 12, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink2).frame(width: 24, alignment: .leading)
            ZoneBar(zones: [(c, Theme.go), (s, Theme.tight), (i, Theme.miss)])
            Text("\(c)%").font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.ink2).frame(width: 34, alignment: .trailing)
        }
    }
}
