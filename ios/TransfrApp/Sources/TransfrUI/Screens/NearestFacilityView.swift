import SwiftUI

/// Nearest facility — the prototype's `#s-nearest` (§6.10). Category chips → the
/// nearest one routed from the current platform, plus every instance ranked by
/// distance. Tapping opens the full walk view. Facilities come from the OSM POI
/// layer; content here is the Berlin Hbf toilets example.
struct NearestFacilityView: View {
    @Environment(TripModel.self) private var model
    @State private var category = "Toilets"

    private struct Cat: Identifiable { let id = UUID(); let name: String; let icon: String }
    private let cats: [Cat] = [
        .init(name: "Toilets", icon: "toilet"), .init(name: "Lifts", icon: "arrow.up.arrow.down.square"),
        .init(name: "Exit", icon: "figure.walk.departure"), .init(name: "Tickets", icon: "ticket"),
        .init(name: "Coffee", icon: "cup.and.saucer"), .init(name: "ATM", icon: "creditcard"),
        .init(name: "Taxi", icon: "car"),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                // Category chips
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(cats) { c in
                            Button { withAnimation(.snappy) { category = c.name } } label: {
                                Label(c.name, systemImage: c.icon)
                                    .font(.system(size: 12.5, weight: .medium))
                                    .foregroundStyle(category == c.name ? .white : Theme.ink2)
                                    .padding(.horizontal, 12).padding(.vertical, 8)
                                    .background(Capsule().fill(category == c.name ? Theme.accent : Theme.panel))
                                    .overlay(Capsule().strokeBorder(category == c.name ? .clear : Theme.line, lineWidth: 1))
                            }.buttonStyle(.plain)
                        }
                    }
                }
                .padding(.bottom, 14)

                // Nearest result
                Button { model.path.append(.walkLookup) } label: {
                    HStack(spacing: 10) {
                        SetIcon("toilet", tint: .white, bg: Theme.accent)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Toilets · main concourse").font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                            Text("stairs-free · level 0 · via the Pl 1 escalator").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 1) {
                            Text("38 m").font(.system(size: 14, weight: .bold, design: .monospaced)).foregroundStyle(Theme.ink)
                            Text("0:33").font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
                        }
                    }
                    .padding(12)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.accent.opacity(0.4), lineWidth: 1.5))
                }.buttonStyle(.plain).padding(.bottom, 8)

                // Direction note
                HStack(spacing: 10) {
                    SetIcon("arrow.right", tint: .white, bg: Theme.accent)
                    Text("From Platform 1, take the escalator up to the concourse — the toilets are immediately left of the DB Reisezentrum.")
                        .font(.system(size: 12)).foregroundStyle(Theme.ink2)
                }
                .padding(12)
                .background(RoundedRectangle(cornerRadius: 12).fill(Theme.accentSoft))
                .padding(.bottom, 14)

                Text("All toilets in this station · 3 found").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.ink3)
                    .padding(.horizontal, 4).padding(.bottom, 8)
                facilityRow("Main concourse", "level 0 · stairs-free", "38 m")
                facilityRow("Upper Stadtbahn (Pl 13–14)", "level +2 · stairs-free", "104 m")
                facilityRow("Europaplatz entrance", "level 0", "131 m")

                Label("Tap a facility to walk to it in full 3D. Facilities come from OSM amenity/shop tags. If a station has none mapped, we say so rather than guess.",
                      systemImage: "info.circle")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3).padding(.top, 14)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Nearest facility").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text("Nearest facility").font(.system(size: 16, weight: .semibold))
                    Text("Berlin Hbf · from Platform 1").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                }
            }
        }
    }

    private func facilityRow(_ name: String, _ note: String, _ dist: String) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Text(note).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            Text(dist).font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line, lineWidth: 1))
        .padding(.bottom, 6)
    }
}
