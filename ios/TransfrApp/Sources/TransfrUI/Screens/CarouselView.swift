import SwiftUI
import TransfrCore

/// Swipeable per-transfer detail — the prototype's `#s-carousel`. A paged
/// `TabView` (DESIGN.md §13.10) over the selected journey's transfers.
struct CarouselView: View {
    @Environment(TripModel.self) private var model
    @State private var index: Int

    init(startIndex: Int) { _index = State(initialValue: startIndex) }

    private var transfers: [Transfer] { model.transfers }

    var body: some View {
        VStack(spacing: 8) {
            TabView(selection: $index) {
                ForEach(Array(transfers.enumerated()), id: \.offset) { i, t in
                    ScrollView {
                        TransferDetailCard(transfer: t) {
                            model.path.append(.walk(transferIndex: i))
                        }
                        .padding(20)
                    }
                    .tag(i)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))

            if transfers.count > 1 {
                HStack(spacing: 6) {
                    ForEach(0..<transfers.count, id: \.self) { i in
                        Capsule().fill(i == index ? Theme.accent : Theme.line)
                            .frame(width: i == index ? 20 : 7, height: 7)
                            .animation(.snappy, value: index)
                    }
                }
                .padding(.bottom, 8)
            }

            Label("Walk times come from core/'s platform graph — real footway distance, stairs, level changes, plus a 60 s boarding buffer.",
                  systemImage: "info.circle")
                .font(.system(size: 12)).foregroundStyle(Theme.ink3)
                .padding(.horizontal, 20).padding(.bottom, 12)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Transfers")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// The full transfer card: ring, platform move, boarding guidance, level note,
/// stats, and the 3D / AR entry buttons.
struct TransferDetailCard: View {
    let transfer: Transfer
    var onOpenWalk: () -> Void

    private var v: Verdict { transfer.verdictKind }

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 16) {
                header
                platformMove
                boardingBox
                levelNote
                stats
                buttons
            }
        }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                Text(transfer.atStation ?? "—").font(.system(size: 20, weight: .bold)).foregroundStyle(Theme.ink)
                Text(layoverLine).font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            WalkRing(usedSeconds: transfer.walkTimeS ?? 0,
                     totalSeconds: transfer.layoverS ?? 1, verdict: v)
        }
    }

    private var layoverLine: String {
        "layover \(Fmt.duration(Int(transfer.layoverS ?? 0)))"
    }

    private var platformMove: some View {
        HStack(spacing: 14) {
            platBig("Arrive", transfer.arrivalPlatform ?? "?", Theme.go)
            Image(systemName: "arrow.right").font(.system(size: 18, weight: .bold)).foregroundStyle(Theme.ink3)
            platBig("Depart", transfer.departurePlatform ?? "?", Theme.accent)
            Spacer()
        }
    }

    private func platBig(_ k: String, _ n: String, _ c: Color) -> some View {
        VStack(spacing: 2) {
            Text(k).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            Text(n).font(.system(size: 30, weight: .bold, design: .monospaced)).foregroundStyle(c)
        }
    }

    private var boardingBox: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Where to sit", systemImage: "tram.fill")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.ink)
                Spacer()
                Text(v == .feasible ? "barely matters" : "saves ~30 s")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(v == .feasible ? Theme.ink3 : Theme.accent)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Capsule().fill(v == .feasible ? Theme.panel2 : Theme.accentSoft))
            }
            HStack(spacing: 8) {
                Image(systemName: v == .feasible ? "arrow.left.and.right" : "arrow.turn.up.left")
                    .foregroundStyle(Theme.accent)
                Text(boardingHint).font(.system(size: 13)).foregroundStyle(Theme.ink2)
            }
            sectorStrip
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel2))
    }

    private var boardingHint: String {
        v == .feasible
            ? "Step off anywhere — the next platform is right across the island."
            : "Doors open → walk toward sector C (front). The stairs down are there."
    }

    private var sectorStrip: some View {
        HStack(spacing: 6) {
            ForEach(["A", "B", "C", "D", "E"], id: \.self) { s in
                let on = (v != .feasible && s == "C")
                Text(s)
                    .font(.system(size: 13, weight: .bold, design: .monospaced))
                    .foregroundStyle(on ? .white : (v == .feasible ? Theme.go : Theme.ink3))
                    .frame(maxWidth: .infinity).frame(height: 34)
                    .background(RoundedRectangle(cornerRadius: 8)
                        .fill(on ? Theme.accent : (v == .feasible ? Theme.goSoft : Theme.panel)))
            }
        }
    }

    private var levelNote: some View {
        HStack(spacing: 10) {
            Image(systemName: levelIcon)
                .foregroundStyle(.white)
                .frame(width: 30, height: 30)
                .background(RoundedRectangle(cornerRadius: 8).fill(v == .feasible ? Theme.go : Theme.stair))
            Text(levelText).font(.system(size: 13)).foregroundStyle(Theme.ink2)
        }
    }

    private var levelIcon: String { v == .feasible ? "checkmark" : "stairs" }
    private var levelText: String {
        v == .feasible
            ? "Same island platform — step across, no stairs. Very comfortable."
            : "Down to the underpass, along, back up. Escalator on both ends."
    }

    private var stats: some View {
        HStack {
            StatCell(key: "Distance", value: Fmt.meters(transfer.walkDistanceM))
            StatCell(key: "Walk", value: Fmt.walkTime(transfer.walkTimeS))
            if let spare = transfer.spareSeconds {
                StatCell(key: "Spare", value: Fmt.duration(Int(spare)),
                         valueColor: v == .tight ? Theme.tight : Theme.go)
            }
        }
    }

    private var buttons: some View {
        HStack(spacing: 10) {
            Button(action: onOpenWalk) {
                Label("3D view", systemImage: "cube.transparent")
            }.buttonStyle(GhostButtonStyle())
            Button {
                // AR entry — RealityView route is a documented follow-up (§13.5).
            } label: {
                Label("AR", systemImage: "arkit")
            }.buttonStyle(PrimaryButtonStyle())
            .disabled(true)
        }
    }
}
