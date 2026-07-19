import SwiftUI

/// Offline & regions — the prototype's `#s-dbmanage` (§6.10). On-device regional
/// databases (install / update / remove, per-station 3D prefetch, storage) are a
/// PLANNED capability: transfr currently plans over the live service and keeps the
/// routing database + pathfinder server-side (see repo-root `TODO.md` §7). Rather
/// than show fabricated installed regions and storage figures as if they were real
/// device state, this screen states honestly that the feature isn't available yet.
struct OfflineRegionsView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SetCard {
                    VStack(alignment: .leading, spacing: 10) {
                        SetIcon("arrow.down.circle", tint: Theme.ink2, bg: Theme.panel2)
                        Text("Offline regions aren't available yet")
                            .font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.ink)
                        Text("transfr plans your journey over the live service, so it needs a connection for now. Downloading a region for fully offline routing and rich 3D station detail is planned — this screen will manage those downloads and on-device storage once it ships.")
                            .font(.system(size: 13)).foregroundStyle(Theme.ink2).lineSpacing(3)
                    }
                }

                Label("Regions will be built offline from an OSM extract (extract_europe.sh) with no live API, so an installed region will route fully on a plane.",
                      systemImage: "cube")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3).lineSpacing(2)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Offline & regions").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text("Offline & regions").font(.system(size: 16, weight: .semibold))
                    Text("Planned feature").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                }
            }
        }
    }
}
