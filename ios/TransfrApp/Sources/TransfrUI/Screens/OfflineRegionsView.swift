import SwiftUI

/// Offline & regions — the prototype's `#s-dbmanage` (§6.10). Install / update /
/// remove regional databases, prefetch a station's 3D detail, and storage. Static
/// example content; real region management is device-side (see `ios/SUI_TODO.md`).
struct OfflineRegionsView: View {
    @State private var prefetch = ""

    private struct Region: Identifiable {
        let id = UUID(); let name: String; let meta: String; let size: String
        let installed: Bool; let state: String?
    }
    private let regions: [Region] = [
        .init(name: "Europe", meta: "transfr_eu · OSM 2026-07-01", size: "1.4 GB · 48,900 stations", installed: true, state: "Active"),
        .init(name: "South Korea", meta: "transfr_kr · OSM 2026-07-10", size: "0.5 GB · 3,140 stations", installed: true, state: "Standby"),
        .init(name: "Japan", meta: "transfr_jp · OSM 2026-07-08", size: "0.7 GB · 9,200 stations", installed: true, state: "Standby"),
        .init(name: "Great Britain", meta: "transfr_gb · not installed", size: "~0.6 GB estimated", installed: false, state: nil),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(text: "Regions")
                ForEach(regions) { r in regionCard(r) }

                SectionHeader(text: "Prefetch a station")
                SetCard {
                    VStack(alignment: .leading, spacing: 11) {
                        Text("Warm the 3D detail for offline").font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                        Text("The base map routes anywhere, but the rich 3D walk (shops, lifts, floor slabs) is fetched per station. Prefetch one so it works with no signal.")
                            .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(2)
                        HStack(spacing: 8) {
                            TextField("e.g. München Hbf", text: $prefetch)
                                .font(.system(size: 13)).padding(.horizontal, 12).padding(.vertical, 9)
                                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.panel2))
                                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Theme.line, lineWidth: 1))
                            SmallButton(title: "Prefetch", kind: .primary)
                        }
                    }
                }

                SectionHeader(text: "Storage")
                SetCard {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("On this device").font(.system(size: 12.5)).foregroundStyle(Theme.ink)
                            Spacer()
                            Text("2.6 GB").font(.system(size: 12.5, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
                        }
                        GeometryReader { geo in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Theme.panel3).frame(height: 7)
                                Capsule().fill(Theme.accent).frame(width: geo.size.width * 0.71, height: 7)
                            }
                        }.frame(height: 7)
                        storageRow("Region databases", "2.6 GB")
                        storageRow("Station detail cache · 42 stations", "146 MB")
                        SmallButton(title: "Clear detail cache", kind: .danger).frame(maxWidth: .infinity).padding(.top, 4)
                    }
                }

                Label("Regions are built offline from an OSM extract (extract_europe.sh) — no live API, so an installed region works fully on a plane.",
                      systemImage: "cube")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3).padding(.top, 12)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Offline & regions").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text("Offline & regions").font(.system(size: 16, weight: .semibold))
                    Text("3 regions · 2.6 GB on device").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                }
            }
        }
    }

    private func regionCard(_ r: Region) -> some View {
        SetCard {
            VStack(alignment: .leading, spacing: 11) {
                HStack(spacing: 11) {
                    SetIcon(r.installed ? "checkmark" : "arrow.down.to.line",
                            tint: r.installed ? Theme.go : Theme.ink2,
                            bg: r.installed ? Theme.goSoft : Theme.panel2)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(r.name).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                        Text(r.meta).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                    }
                    Spacer()
                    if r.installed { StatusBadge(text: "Installed", color: Theme.go) }
                }
                HStack {
                    Text(r.size).font(.system(size: 11.5)).foregroundStyle(Theme.ink3)
                    Spacer()
                    if let s = r.state { Text(s).font(.system(size: 11.5)).foregroundStyle(Theme.ink3) }
                }
                HStack(spacing: 8) {
                    if r.installed {
                        SmallButton(title: "Update", kind: r.state == "Active" ? .primary : .plain)
                        SmallButton(title: "Remove", kind: .danger)
                    } else {
                        SmallButton(title: "Download", kind: .primary)
                    }
                    Spacer()
                }
            }
        }
        .padding(.bottom, 9)
    }

    private func storageRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.system(size: 11.5)).foregroundStyle(Theme.ink2)
            Spacer()
            Text(value).font(.system(size: 11.5, design: .monospaced)).foregroundStyle(Theme.ink2)
        }
    }
}
