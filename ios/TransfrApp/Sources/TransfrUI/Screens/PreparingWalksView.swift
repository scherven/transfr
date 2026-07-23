import SwiftUI
import TransfrCore

/// The transition between the results list and a journey's timeline.
///
/// Progressive load: `/journeys?assess=false` returns the itineraries instantly
/// with `pending` transfers, and the real verdicts stream in behind the list via
/// `/assess`. When you pick a journey whose verdicts are still streaming, this screen
/// stands in — it shows each change of train filling in live (spinner → verdict +
/// walk time) and slides on to the timeline the moment they're all in. When the
/// verdicts already landed (the common, fast case), `select` skips straight past
/// this to the timeline, so it only appears when it has something to show.
struct PreparingWalksView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings

    @State private var proceeded = false

    private var transfers: [Transfer] { model.transfers }
    private var settled: Int { transfers.filter { !$0.verdictKind.isPending }.count }
    private var allSettled: Bool { !transfers.isEmpty && settled == transfers.count }
    private var imperial: Bool { settings.units == .imperial }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                VStack(spacing: 10) {
                    ForEach(Array(transfers.enumerated()), id: \.offset) { _, t in
                        row(t)
                    }
                }
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Checking your connection")
        .navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom) { bottomBar }
        // Slide on once every transfer's verdict is in — a short beat so the last
        // one is seen resolving, never a jarring instant cut.
        .onChange(of: allSettled) { advanceIfReady() }
        .onAppear { advanceIfReady() }
    }

    private func advanceIfReady() {
        guard !proceeded, allSettled else { return }
        proceeded = true
        Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            model.proceedToTimeline()
        }
    }

    // MARK: Header

    private var header: some View {
        Panel {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 10) {
                    ZStack {
                        Circle().fill(Theme.accentSoft).frame(width: 40, height: 40)
                        Image(systemName: "figure.walk.motion").font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Checking your transfers").font(.system(size: 17, weight: .bold)).foregroundStyle(Theme.ink)
                        Text(subtitle).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.ink3)
                    }
                    Spacer()
                }
                ProgressView(value: Double(settled), total: Double(max(transfers.count, 1)))
                    .tint(Theme.accent)
            }
        }
    }

    /// `allSettled` only says the verdicts have landed — never that they were good.
    /// This used to read "all clear" the moment the last one arrived, whatever it
    /// said, so a change we'd just assessed as missed announced itself as fine on the
    /// way to the timeline. The settled half is now the real worst-wins summary, the
    /// same sentence the results card shows (`verdictSummary`).
    private var subtitle: String {
        allSettled ? "\(transfers.verdictSummary) · \(transfers.count) transfer\(transfers.count == 1 ? "" : "s") assessed"
                   : "\(settled) of \(transfers.count) assessed"
    }

    // MARK: Transfer row

    private func row(_ t: Transfer) -> some View {
        let v = t.verdictKind
        return HStack(spacing: 12) {
            statusIcon(v).frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(t.atStation ?? "—").font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
                Text(detail(t)).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            PlatformChip(text: "\(t.shownArrivalPlatform ?? "?")→\(t.shownDeparturePlatform ?? "?")")
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
            .fill(v.isPending ? Theme.panel : v.softColor))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
            .strokeBorder(v.isPending ? Theme.line : v.color.opacity(0.28), lineWidth: 1))
    }

    @ViewBuilder
    private func statusIcon(_ v: Verdict) -> some View {
        if v.isPending {
            ProgressView().controlSize(.small)
        } else {
            Image(systemName: v.iconName).font(.system(size: 15, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 24, height: 24)
                .background(Circle().fill(v.color))
        }
    }

    private func detail(_ t: Transfer) -> String {
        let v = t.verdictKind
        if v.isPending { return "resolving platforms…" }
        if let walk = t.walkTimeS {
            return "\(v.label) · \(Fmt.walkTime(walk)) · \(Fmt.distance(t.walkDistanceM, imperial: imperial))"
        }
        return v.label
    }

    // MARK: Bottom

    private var bottomBar: some View {
        Button {
            proceeded = true
            model.proceedToTimeline()
        } label: {
            HStack {
                Text(allSettled ? "View connection" : "Go ahead")
                Image(systemName: "arrow.right")
            }
        }
        .buttonStyle(PrimaryButtonStyle())
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.thinMaterial)
    }
}
