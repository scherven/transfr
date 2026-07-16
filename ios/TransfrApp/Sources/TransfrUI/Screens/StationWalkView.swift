import SwiftUI

/// Full station walk — the prototype's `#s-stationwalk` (§6.10). From one source
/// platform, distance / walk time / level Δ to every other, sorted nearest-first,
/// with a stairs-free marker. Tapping a row opens the full walk view (§6.5). Content
/// is the Berlin Hbf example; a live build runs one pathfind per platform.
struct StationWalkView: View {
    @Environment(TripModel.self) private var model
    @State private var source = "Pl 1"

    private struct Dest: Identifiable { let id = UUID(); let name: String; let note: String; let walk: String; let dlvl: String; let stepFree: Bool }
    private let dests: [Dest] = [
        .init(name: "Platform 2", note: "same island — step across", walk: "15 m", dlvl: "0", stepFree: true),
        .init(name: "Platform 3", note: "across the underpass", walk: "41 m", dlvl: "0", stepFree: true),
        .init(name: "Platform 4", note: "across the underpass", walk: "44 m", dlvl: "0", stepFree: true),
        .init(name: "Platform 7", note: "far end of the underpass", walk: "73 m", dlvl: "0", stepFree: true),
        .init(name: "Platform 8", note: "far end of the underpass", walk: "78 m", dlvl: "0", stepFree: true),
        .init(name: "Platform 12", note: "upper Stadtbahn — escalator + lift", walk: "99 m", dlvl: "+4", stepFree: true),
        .init(name: "Platform 16", note: "upper Stadtbahn — escalator + lift", walk: "122 m", dlvl: "+4", stepFree: true),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text("Walk from").font(.system(size: 11.5)).foregroundStyle(Theme.ink3).padding(.bottom, 8)
                SegmentedControl(options: ["Pl 1", "Pl 5", "Pl 8", "Pl 14"], selection: $source) { $0 }
                    .padding(.bottom, 14)

                HStack {
                    StatCell(key: "Reachable", value: "15 / 15")
                    StatCell(key: "Nearest", value: "15 m")
                    StatCell(key: "Farthest", value: "122 m")
                }
                .padding(.bottom, 14)

                HStack {
                    Text("From Platform 1 · lower level (L−2)").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.ink3)
                    Spacer()
                    Text("walk").font(.system(size: 11)).foregroundStyle(Theme.ink3).frame(width: 52, alignment: .trailing)
                    Text("Δlvl").font(.system(size: 11)).foregroundStyle(Theme.ink3).frame(width: 34, alignment: .trailing)
                }
                .padding(.horizontal, 4).padding(.bottom, 8)

                // Source row
                platformRow(name: "Platform 1", note: "you are here · N–S · L−2", walk: "—", dlvl: "0", stepFree: false, isSource: true)
                ForEach(dests) { d in
                    Button { model.path.append(.walkLookup) } label: {
                        platformRow(name: d.name, note: d.note, walk: d.walk, dlvl: d.dlvl, stepFree: d.stepFree, isSource: false)
                    }.buttonStyle(.plain)
                }

                Label("Tap any platform to see the walk as a full 3D view. The green figure marks a stairs-free reachable platform (Stairs-free is on in Settings). One pathfind per platform — same walk cost the transfer verdict uses.",
                      systemImage: "figure.walk")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3).padding(.top, 12)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Full station walk").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text("Full station walk").font(.system(size: 16, weight: .semibold))
                    Text("Berlin Hbf · 15 platforms").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                }
            }
        }
    }

    private func platformRow(name: String, note: String, walk: String, dlvl: String, stepFree: Bool, isSource: Bool) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Text(note).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            Text(walk).font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
                .frame(width: 52, alignment: .trailing)
            Text(dlvl).font(.system(size: 13, design: .monospaced)).foregroundStyle(Theme.ink2)
                .frame(width: 34, alignment: .trailing)
            Image(systemName: stepFree ? "figure.walk" : "")
                .font(.system(size: 12)).foregroundStyle(Theme.go).frame(width: 16)
        }
        .padding(.horizontal, 12).padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: 12).fill(isSource ? Theme.accentSoft : Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(isSource ? Theme.accent.opacity(0.3) : Theme.line, lineWidth: 1))
        .padding(.bottom, 6)
    }
}
