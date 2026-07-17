import SwiftUI

/// The Advanced hub — the prototype's `#s-advanced` (§6.10). Power tools that
/// answer *station* questions (not journey questions), all over the same
/// `viz_export` / pathfinder. Reached from the Plan header — the shield button
/// beside Settings (#26).
struct AdvancedView: View {
    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                SectionHeader(text: "This station")
                NavRow(icon: "rotate.3d", title: "Station map (3D)",
                       subtitle: "Rotate & zoom any station",
                       route: .stationMap).padding(.bottom, 8)
                NavRow(icon: "chart.line.uptrend.xyaxis", title: "Full station walk",
                       subtitle: "Distance & time from one platform to every other",
                       route: .stationWalk).padding(.bottom, 8)
                NavRow(icon: "mappin.and.ellipse", title: "Walk to nearest…",
                       subtitle: "Toilets, lifts, exits, tickets, coffee",
                       route: .nearestFacility).padding(.bottom, 8)
                NavRow(icon: "waveform.path.ecg", title: "Map health",
                       subtitle: "Is this station fully mapped? Why a walk may be missing",
                       route: .mapHealth)

                SectionHeader(text: "Data")
                NavRow(icon: "cylinder.split.1x2", title: "Offline & regions",
                       subtitle: "Prefetch stations, manage storage",
                       route: .offlineRegions)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Advanced").navigationBarTitleDisplayMode(.inline)
    }
}
