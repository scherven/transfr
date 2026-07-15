import SwiftUI

/// Map health — the prototype's `#s-maphealth` (§6.10, §7.11). Per-database
/// connectivity (connected / stitchable / island), a region selector, and an
/// all-databases comparison. Read-only and diagnostic. Numbers are the measured
/// `stitch_survey.py` figures (EU/KR) plus an illustrative JP.
struct MapHealthView: View {
    @State private var region = "Europe"

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

                Label("transfr_eu (71 / 5 / 24, 1,401 platforms) and transfr_kr (2 / 5 / 93, 899) are measured stitch_survey.py sweeps; JP is illustrative until its sweep finishes. Health is intrinsic to each map — nothing here is user-editable.",
                      systemImage: "info.circle")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3).padding(.top, 12)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Map health").navigationBarTitleDisplayMode(.inline)
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
