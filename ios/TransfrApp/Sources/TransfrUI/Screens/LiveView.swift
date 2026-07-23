import SwiftUI
import TransfrCore

/// On-trip mode — the prototype's `#s-live` (§6.7). The **next-transfer card is
/// real**: verdict, platforms, walk, spare, and the step-off cue all come from the
/// selected journey and its `/walk` boarding. The route map stays a *labelled
/// preview* — live position/countdown/delays still need CoreLocation + the live
/// feed (see repo-root `TODO.md` §4), so nothing here claims a real GPS fix or a
/// counting-down clock.
struct LiveView: View {
    @Environment(TripModel.self) private var model
    @State private var boarding: BoardingGuidance?

    // The next at-risk transfer to feature, else simply the first change.
    private var nextTransfer: Transfer? {
        model.transfers.first { $0.verdictKind == .tight || $0.verdictKind == .infeasible }
            ?? model.transfers.first
    }

    /// The train currently being ridden — the first transit (named) leg of the
    /// selected journey. Real, not a fixed "ICE 271".
    private var currentLeg: Leg? {
        model.selected?.legs.first { $0.trainName != nil }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let journey = model.selected {
                    RouteMapView(journey: journey, fromCurrent: true, youProgress: 0.42)
                        .frame(height: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(Theme.line, lineWidth: 1))
                        .overlay(alignment: .topLeading) {
                            Text("PREVIEW · live tracking coming")
                                .font(.system(size: 9, weight: .semibold)).tracking(0.8)
                                .foregroundStyle(.white)
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(Capsule().fill(.black.opacity(0.4)))
                                .padding(10)
                        }
                }

                if let t = nextTransfer { nextCard(t) }

                Label("We'll nudge you to open AR ~90 s before arrival, so the path is ready the moment the doors open.",
                      systemImage: "location.north.circle")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("On your way")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text("On your way").font(.system(size: 16, weight: .semibold))
                    Text(currentLegLabel).font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
                }
            }
            ToolbarItem(placement: .topBarTrailing) { StatusBadge(text: "Preview", color: Theme.ink3, showDot: false) }
        }
        .task(id: nextTransfer) { await loadBoarding() }
    }

    private var currentLegLabel: String {
        guard let leg = currentLeg else { return "your itinerary" }
        let name = leg.trainName ?? leg.mode
        if let dest = leg.destination.name { return "\(name) · to \(dest)" }
        return name
    }

    private func nextCard(_ t: Transfer) -> some View {
        let v = t.verdictKind
        return SetCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("NEXT TRANSFER").font(.system(size: 10.5, weight: .semibold)).tracking(1.2).foregroundStyle(Theme.ink3)
                    Spacer()
                    VerdictBadge(verdict: v)
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text(t.atStation ?? "your change")
                        .font(.system(size: 22, weight: .bold)).foregroundStyle(Theme.ink)
                    Text(spareLine(t)).font(.system(size: 13))
                        .foregroundStyle(v == .tight || v == .infeasible ? v.color : Theme.ink2)
                }
                // Step-off cue — real boarding once the walk resolves it. Platforms
                // read the recovered public sign, like every other screen.
                BoardingStepoffCue(guidance: boarding, departurePlatform: t.shownDeparturePlatform ?? "?")
                    .padding(11)
                    .background(RoundedRectangle(cornerRadius: 11).fill(Theme.accentSoft))

                // Platform move + Preview
                HStack(spacing: 12) {
                    moveEnd("Arrive", t.shownArrivalPlatform ?? "?")
                    Image(systemName: "arrow.right").font(.system(size: 15, weight: .bold)).foregroundStyle(Theme.ink3)
                    moveEnd("Depart", t.shownDeparturePlatform ?? "?")
                    Spacer()
                    VStack(alignment: .trailing, spacing: 1) {
                        Text("Walk").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                        Text(Fmt.walkTime(t.walkTimeS)).font(.system(size: 16, weight: .bold, design: .monospaced)).foregroundStyle(Theme.ink)
                    }
                    Button { model.path.append(.carousel(startIndex: 0)) } label: {
                        Label("Preview", systemImage: "cube.transparent").font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white).padding(.horizontal, 12).padding(.vertical, 9)
                            .background(RoundedRectangle(cornerRadius: 10).fill(Theme.accent))
                    }.buttonStyle(.plain)
                }
            }
        }
    }

    /// Real slack: the journey's own layover minus the routed walk.
    private func spareLine(_ t: Transfer) -> String {
        guard let spare = t.spareSeconds else { return "Platform change at \(t.atStation ?? "the interchange")" }
        if spare < 0 { return "The walk runs ~\(Fmt.walkTime(-spare)) past the layover" }
        return "~\(Fmt.walkTime(spare)) spare after the platform walk"
    }

    private func loadBoarding() async {
        boarding = nil
        guard let t = nextTransfer, let key = WalkKey(transfer: t) else { return }
        boarding = await model.walk(for: key)?.boarding
    }

    private func moveEnd(_ k: String, _ n: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(k).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            Text(n).font(.system(size: 22, weight: .bold, design: .monospaced)).foregroundStyle(Theme.ink)
        }
    }
}
