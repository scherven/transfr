import Foundation
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
                source("OpenStreetMap", "Base map, platforms, walkways, levels, facilities. © OpenStreetMap contributors, available under the Open Database License (ODbL).",
                       link: "https://www.openstreetmap.org/copyright")
                source("Transitous · MOTIS", "Journeys, live delays and platform assignments, via the open Transitous routing service (MOTIS). Community open data.",
                       link: "https://transitous.org/sources/")
                source("Deutsche Bahn · IRIS", "Real-time platform and track changes for German stations, from DB's public IRIS feed, served through derf's db-infoscreen (finalrewind.org).",
                       link: "https://dbf.finalrewind.org")
                source("Coach-formation providers", "Train coach order & sector maps from the operators' formation feeds — DB Wagenreihung and ÖBB — where published.")
                source("trainline-eu/stations", "Station names, and the English city aliases (info:en) that let you search stations in English. © trainline-eu/stations contributors, ODbL.",
                       link: "https://github.com/trainline-eu/stations")
                source("Natural Earth", "Country outlines on the route map. Public domain (CC0) — attribution not required, credited here by choice.",
                       link: "https://www.naturalearthdata.com")

                SectionHeader(text: "Built with")
                SetCard {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Regions are extracted offline from OpenStreetMap planet data (via Geofabrik) with osmium; routing runs on the local core/ pathfinder. No map tiles are fetched at run time — an installed region is fully self-contained.")
                            .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(2)
                        Text("ODbL lets us share the build method rather than the multi-gigabyte database — the full pipeline is open source.")
                            .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(2)
                        if let repo = URL(string: "https://github.com/scherven/transfr") {
                            Link("github.com/scherven/transfr", destination: repo)
                                .font(.system(size: 11.5, weight: .medium)).foregroundStyle(Theme.accent)
                        }
                    }
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

    private func source(_ name: String, _ detail: String, link: String? = nil) -> some View {
        SetCard {
            VStack(alignment: .leading, spacing: 4) {
                Text(name).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Text(detail).font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(2)
                // A tappable source link where the licence/terms ask for one — e.g.
                // Transitous requires a visible link to its sources page.
                if let link = link, let url = URL(string: link) {
                    Link(link, destination: url)
                        .font(.system(size: 11.5, weight: .medium)).foregroundStyle(Theme.accent)
                }
            }
        }
    }
}
