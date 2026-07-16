import SwiftUI
import TransfrCore

/// The transition screen between the journey timeline and the walk carousel.
///
/// Progressive load (DESIGN §13.9): `/journeys` returns fast and the timeline
/// shows immediately, while each transfer's drawable geometry streams in behind
/// it. When the user dives into a change whose walk hasn't landed yet, this screen
/// stands in — it shows every transfer filling in live (spinner → walk time, or a
/// clean "no map here"), and slides on to the carousel the moment *their* walk is
/// ready. In the common case the prefetch has already finished and `openTransfers`
/// skips straight past this, so it only ever appears when it has something to say.
struct PreparingWalksView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings
    let startIndex: Int

    @State private var proceeded = false

    private var transfers: [Transfer] { model.transfers }
    private var prefetch: TripModel.WalkPrefetchState { model.walkPrefetch }
    private var imperial: Bool { settings.units == .imperial }

    private var targetSettled: Bool {
        switch model.walkStatus(at: startIndex) { case .ready, .unavailable: true; default: false }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                progressHeader
                VStack(spacing: 10) {
                    ForEach(Array(transfers.enumerated()), id: \.offset) { i, t in
                        row(index: i, transfer: t)
                    }
                }
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Getting ready")
        .navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom) { bottomBar }
        // The prefetch lives on the model (it survives this view), but seed it
        // defensively in case we arrived without the timeline having started it.
        .task { model.prefetchWalks(stepFree: settings.stepFree) }
        // Slide on as soon as the tapped transfer's drawing is ready — a short
        // beat so its checkmark is seen, never a jarring instant cut.
        .onChange(of: prefetch) { advanceIfReady() }
        .onAppear { advanceIfReady() }
    }

    private func advanceIfReady() {
        guard !proceeded, targetSettled else { return }
        proceeded = true
        Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            model.proceedToWalks(startIndex: startIndex)
        }
    }

    // MARK: Header

    private var progressHeader: some View {
        Panel {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 10) {
                    ZStack {
                        Circle().fill(Theme.accentSoft).frame(width: 40, height: 40)
                        Image(systemName: "figure.walk.motion").font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Drawing your transfers").font(.system(size: 17, weight: .bold)).foregroundStyle(Theme.ink)
                        Text(subtitle).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.ink3)
                    }
                    Spacer()
                }
                ProgressView(value: Double(prefetch.settled), total: Double(max(prefetch.total, 1)))
                    .tint(Theme.accent)
            }
        }
    }

    private var subtitle: String {
        if prefetch.isComplete { return "ready · \(prefetch.readyCount) walk\(prefetch.readyCount == 1 ? "" : "s") mapped" }
        return "\(prefetch.settled) of \(prefetch.total) ready"
    }

    // MARK: Transfer row

    @ViewBuilder
    private func row(index i: Int, transfer t: Transfer) -> some View {
        let isTarget = i == startIndex
        HStack(spacing: 12) {
            statusIcon(model.walkStatus(at: i))
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(t.atStation ?? "—").font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
                    if isTarget {
                        Text("your walk").font(.system(size: 10, weight: .bold)).foregroundStyle(Theme.accent)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Capsule().fill(Theme.accentSoft))
                    }
                }
                Text(detail(i, t)).font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            PlatformChip(text: "\(t.arrivalPlatform ?? "?")→\(t.departurePlatform ?? "?")")
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
            .fill(isTarget ? Theme.accentSoft : Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
            .strokeBorder(isTarget ? Theme.accent.opacity(0.35) : Theme.line, lineWidth: 1))
    }

    @ViewBuilder
    private func statusIcon(_ status: TripModel.WalkLoad) -> some View {
        switch status {
        case .loading, .pending:
            ProgressView().controlSize(.small)
        case .ready:
            Image(systemName: "checkmark.circle.fill").font(.system(size: 18)).foregroundStyle(Theme.go)
        case .unavailable:
            Image(systemName: "minus.circle").font(.system(size: 17)).foregroundStyle(Theme.nodata)
        }
    }

    private func detail(_ i: Int, _ t: Transfer) -> String {
        switch model.walkStatus(at: i) {
        case .ready:
            return "\(Fmt.walkTime(t.walkTimeS)) · \(Fmt.distance(t.walkDistanceM, imperial: imperial))"
        case .unavailable:
            return "no map here"
        case .loading, .pending:
            return "resolving platforms…"
        }
    }

    // MARK: Bottom

    private var bottomBar: some View {
        Button {
            proceeded = true
            model.proceedToWalks(startIndex: startIndex)
        } label: {
            HStack {
                Text(targetSettled ? "View walks" : "Go ahead")
                Image(systemName: "arrow.right")
            }
        }
        .buttonStyle(PrimaryButtonStyle())
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.thinMaterial)
    }
}
