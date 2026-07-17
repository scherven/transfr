import SwiftUI

/// Data sources & licences — the prototype's `#s-about` (§6.11). A required page,
/// led by the OpenStreetMap/ODbL credit. Static content (a licence obligation,
/// not a design choice); no live data.
struct AttributionsView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                // Hero credit
                SetCard(tint: Theme.accentSoft, border: Theme.accent) {
                    VStack(alignment: .leading, spacing: 9) {
                        HStack(spacing: 11) {
                            SetIcon("mappin.and.ellipse", tint: .white, bg: Theme.accent)
                            Text("Map data © OpenStreetMap contributors")
                                .font(.system(size: 15, weight: .medium)).foregroundStyle(Theme.ink)
                        }
                        Text("Every station map, platform, footway, level and facility in Transfr is derived from OpenStreetMap, available under the Open Database License (ODbL). © OpenStreetMap contributors.")
                            .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(2)
                    }
                }
                .padding(.bottom, 6)

                SectionHeader(text: "Data sources")
                source("OpenStreetMap", "Base map, platforms, walkways, levels, facilities. © OpenStreetMap contributors, ODbL. openstreetmap.org/copyright")
                source("Transitous · MOTIS", "Journeys, live delays and platform assignments, via the open Transitous routing service (MOTIS). Community open data.")
                source("Deutsche Bahn · IRIS", "Real-time platform and track changes for German stations, via DB's public IRIS feed.")
                source("Coach-formation providers", "Train coach order & sector maps from the operators' formation APIs (DB Wagenreihung and equivalents), where published.")
                source("trainline-eu/stations", "Station names, and the English city aliases (info:en) that let you search stations in English. © trainline-eu/stations contributors, ODbL. github.com/trainline-eu/stations")

                SectionHeader(text: "Built with")
                SetCard {
                    Text("Regions are extracted offline from the OSM planet with osmium; routing runs on the local core/ pathfinder. No map tiles are fetched at run time — an installed region is fully self-contained.")
                        .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(2)
                }

                Label("ODbL requires that any public use of this data keep the \u{201C}© OpenStreetMap contributors\u{201D} credit and share adaptations alike.",
                      systemImage: "cube")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3).padding(.top, 14)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Attributions").navigationBarTitleDisplayMode(.inline)
    }

    private func source(_ name: String, _ detail: String) -> some View {
        SetCard {
            VStack(alignment: .leading, spacing: 4) {
                Text(name).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Text(detail).font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(2)
            }
        }
    }
}
