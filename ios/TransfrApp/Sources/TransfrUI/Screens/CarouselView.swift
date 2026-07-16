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
                        TransferDetailCard(
                            transfer: t,
                            onOpenWalk: { model.path.append(.walk(transferIndex: i)) },
                            onOpenAR: { model.path.append(.ar(transferIndex: i)) }
                        )
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
    @Environment(SettingsStore.self) private var settings
    @Environment(TripModel.self) private var model
    let transfer: Transfer
    var onOpenWalk: () -> Void
    var onOpenAR: () -> Void

    // Real boarding/step-off guidance + level note, from the transfer's /walk.
    @State private var walk: WalkResult?

    private var v: Verdict { transfer.verdictKind }

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 16) {
                header
                platformMove
                BoardingCard(guidance: walk?.boarding, levelNote: levelNote)
                stats
                buttons
            }
        }
        // Same key as the walk screen: flipping step-free refetches the elevator-
        // free route, whose step-off (and level note) can differ.
        .task(id: settings.stepFree) { await loadWalk() }
    }

    /// A step-free / stairs summary from the walk's real `transitions` — nil until
    /// the geometry loads (or on the sample tier), so the card shows only the
    /// boarding half rather than inventing a level story.
    private var levelNote: String? {
        guard let p = walk?.export?.path, p.found else { return nil }
        let transitions = p.transitions ?? []
        if transitions.isEmpty { return "Step-free — same level, no stairs or lifts." }
        let kinds = Set(transitions.map { WalkConnector.label($0.kind).lowercased() }).sorted()
        let n = transitions.count
        return "\(n) level change\(n == 1 ? "" : "s") — \(kinds.joined(separator: " + "))."
    }

    private func loadWalk() async {
        guard let key = WalkKey(transfer: transfer, stepFree: settings.stepFree) else { return }
        walk = await model.walk(for: key)
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

    private var stats: some View {
        HStack {
            StatCell(key: "Distance", value: Fmt.distance(transfer.walkDistanceM, imperial: settings.units == .imperial))
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
            Button(action: onOpenAR) {
                Label("AR", systemImage: "arkit")
            }.buttonStyle(PrimaryButtonStyle())
        }
    }
}
