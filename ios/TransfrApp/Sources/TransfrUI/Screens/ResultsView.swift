import SwiftUI
import TransfrCore

/// The options list — the prototype's `#s-results`. One `JourneyCard` per option,
/// the first flagged "Best". Tapping a card selects it and pushes the timeline.
///
/// This screen is reached the instant "Find connections" is tapped (#17), so it
/// owns the whole search, not just its result. It has no journeys at all until
/// `/journeys` lands, and that window is drawn as skeletons — never as fabricated
/// journeys. The phases, mirroring `TripModel.plan()`:
///
///   A  `.loading`  skeleton cards + a "Searching…" spinner in the subtitle
///   B  `.loaded`   the real cards, every verdict `pending` → "Checking…"
///   C             `streamVerdicts` settles each verdict in place, as it lands
///
/// C needs no code here: `JourneyCard` already reads `verdictKind`, and `pending`
/// already renders as ink-3 / panel-2 / "Checking…" (Theme.swift).
struct ResultsView: View {
    @Environment(TripModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                content
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle(navTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text(navTitle).font(.system(size: 16, weight: .semibold))
                    subtitle
                }
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch model.load {
        case .loading:
            searching
        case .failed(let message):
            failed(message)
        case .idle, .loaded:
            if model.journeys.isEmpty { empty } else { list }
        }
    }

    /// Phase A. Three skeletons — a neutral "a few options" shape, carrying no
    /// data of its own; the real count is unknowable until the search returns.
    private var searching: some View {
        ForEach(0 ..< 3, id: \.self) { _ in
            JourneyCardSkeleton()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Searching for connections")
    }

    private var list: some View {
        ForEach(Array(model.journeys.enumerated()), id: \.element.id) { idx, journey in
            Button {
                model.select(journey)
            } label: {
                // "Best" is a claim about the whole option, so it waits until this
                // journey's verdicts are all in. Painted at `idx == 0` alone it
                // appeared while the walks were still streaming — and was never
                // revised, so a first option that settled `tight` (or with a change
                // we couldn't assess) kept a badge saying it was the one to take.
                JourneyCard(journey: journey, isBest: idx == 0 && !journey.hasPendingTransfers)
            }
            .buttonStyle(.plain)
        }
    }

    /// The search failed *after* we already navigated here, so the error has to be
    /// recoverable on this screen rather than stranding the user on an empty list:
    /// re-run the same query, or go back and change it.
    private func failed(_ message: String) -> some View {
        Panel {
            VStack(alignment: .leading, spacing: 14) {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.miss)
                Text("The search didn't finish, so there's nothing to show yet.")
                    .font(.system(size: 13)).foregroundStyle(Theme.ink3)
                Button {
                    Task { await model.plan() }
                } label: {
                    HStack(spacing: 7) {
                        Image(systemName: "arrow.clockwise")
                        Text("Try again")
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                Button("Change the search") { model.path.removeAll() }
                    .buttonStyle(GhostButtonStyle())
            }
        }
    }

    /// The search succeeded and honestly returned nothing. Distinct from `failed`:
    /// nothing broke, there just isn't a connection to show.
    private var empty: some View {
        Panel {
            VStack(alignment: .leading, spacing: 14) {
                Label("No connections found", systemImage: "magnifyingglass")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.ink)
                Text("Nothing runs between these stations at this time.")
                    .font(.system(size: 13)).foregroundStyle(Theme.ink3)
                Button("Change the search") { model.path.removeAll() }
                    .buttonStyle(GhostButtonStyle())
            }
        }
    }

    private var navTitle: String {
        "\(short(model.origin)) → \(short(model.destination))"
    }

    @ViewBuilder private var subtitle: some View {
        switch model.load {
        case .loading:
            HStack(spacing: 5) {
                ProgressView().controlSize(.mini)
                Text("Searching…")
            }
            .font(.system(size: 12, design: .monospaced))
            .foregroundStyle(Theme.ink3)
        case .failed:
            Text("Search failed")
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(Theme.miss)
        case .idle, .loaded:
            Text(settledSubtitle)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(Theme.ink3)
        }
    }

    private var settledSubtitle: String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_GB"); f.dateFormat = "HH:mm"
        let n = model.journeys.count
        // The real departure day, not a hardcoded "Today" — the picker happily
        // searches tomorrow or next week, and the header was calling all of it today.
        return "\(Fmt.relativeFutureDay(model.departure)) · from \(f.string(from: model.departure)) · \(n) option\(n == 1 ? "" : "s")"
    }

    private func short(_ s: String) -> String {
        s.replacingOccurrences(of: " Hbf", with: "")
    }
}

/// Phase A's stand-in for one option: the `JourneyCard` skeleton, at the same
/// geometry (Panel, 16pt inset, 12pt rows) so the real card lands in place rather
/// than jumping. Deliberately contentless — bars, never plausible-looking times.
struct JourneyCardSkeleton: View {
    var body: some View {
        Panel(padding: 0) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    SkeletonBar(width: 146, height: 19)   // "08:12 → 12:47"
                    Spacer()
                    SkeletonBar(width: 62, height: 23, radius: 8)   // verdict badge
                }
                HStack(spacing: 6) {                      // the change flow
                    SkeletonBar(width: 40, height: 23)
                    SkeletonBar(width: 106, height: 23)
                    SkeletonBar(width: 86, height: 23)
                }
                HStack(spacing: 14) {                     // duration · changes
                    SkeletonBar(width: 56, height: 13)
                    SkeletonBar(width: 70, height: 13)
                    Spacer()
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityHidden(true)
    }
}

/// The prototype's `.sk`: a panel-2 bar with a panel-3 highlight sweeping across.
/// Reduced motion freezes the sweep — the bar stays, it just stops moving
/// (DESIGN.md:133, locked).
struct SkeletonBar: View {
    var width: CGFloat
    var height: CGFloat
    var radius: CGFloat = 7

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var sweeping = false

    private var shape: RoundedRectangle { RoundedRectangle(cornerRadius: radius, style: .continuous) }

    var body: some View {
        shape.fill(Theme.panel2)
            .frame(width: width, height: height)
            .overlay {
                if !reduceMotion {
                    LinearGradient(colors: [.clear, Theme.panel3, .clear],
                                   startPoint: .leading, endPoint: .trailing)
                        .frame(width: width)
                        .offset(x: sweeping ? width : -width)
                }
            }
            .clipShape(shape)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 1.4).repeatForever(autoreverses: false)) {
                    sweeping = true
                }
            }
    }
}

/// A single option summary. Times, verdict, the change flow, and meta line.
struct JourneyCard: View {
    let journey: Journey
    var isBest: Bool = false

    private var verdict: Verdict { journey.recomputedVerdict }

    var body: some View {
        Panel(padding: 0) {
            HStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 12) {
                    if isBest {
                        Text("Best")
                            .font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
                            .padding(.horizontal, 9).padding(.vertical, 3)
                            .background(Capsule().fill(Theme.accent))
                    }
                    HStack {
                        Text("\(Fmt.time(journey.departureISO))  →  \(Fmt.time(journey.arrivalISO))")
                            .font(.system(size: 20, weight: .bold, design: .monospaced))
                            .foregroundStyle(Theme.ink)
                        Spacer()
                        VerdictBadge(verdict: verdict)
                    }
                    flow
                    meta
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
    }

    private var flow: some View {
        // Pills wrap onto multiple rows (issue #19) instead of being crunched into
        // one. Each arrow travels with its following node so a change never splits
        // across a row break, and every pill stays on a single line.
        FlowLayout(spacing: 6, lineSpacing: 6) {
            legPill(journey.legs.first(where: { $0.trainName != nil })?.trainName ?? "Train")
            ForEach(Array(journey.transfers.enumerated()), id: \.offset) { _, t in
                HStack(spacing: 6) {
                    Image(systemName: "arrow.right").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.ink3)
                    transferNode(t)
                }
                .fixedSize(horizontal: true, vertical: false)
            }
        }
    }

    private func legPill(_ name: String) -> some View {
        Text(name)
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundStyle(Theme.ink2)
            .lineLimit(1)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Capsule().fill(Theme.panel2))
            .fixedSize(horizontal: true, vertical: false)
    }

    private func transferNode(_ t: Transfer) -> some View {
        let v = t.verdictKind
        // Prefer the recovered public platform sign over the feed's internal code,
        // so a renumbered change (Köln "89→88") reads as the real "7→6".
        let arr = t.shownArrivalPlatform ?? "?"
        let dep = t.shownDeparturePlatform ?? "?"
        return HStack(spacing: 4) {
            Text(shortStation(t.atStation ?? "")).font(.system(size: 12, weight: .semibold)).lineLimit(1)
            Text("\(arr)→\(dep)").font(.system(size: 11, weight: .medium, design: .monospaced)).opacity(0.7).lineLimit(1)
            if t.stepFree == true {
                Image(systemName: "figure.roll").font(.system(size: 9, weight: .semibold))
                    .opacity(0.75).accessibilityLabel("step-free")
            }
        }
        .foregroundStyle(v.color)
        .padding(.horizontal, 8).padding(.vertical, 5)
        .background(Capsule().fill(v.softColor))
        .fixedSize(horizontal: true, vertical: false)
    }

    private var meta: some View {
        HStack(spacing: 14) {
            Label(Fmt.duration(journey.durationS), systemImage: "clock")
            Label("\(journey.numChanges) change\(journey.numChanges == 1 ? "" : "s")",
                  systemImage: "arrow.triangle.swap")
            Spacer()
            Text(metaTag)
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                .foregroundStyle(verdict == .feasible ? Theme.ink3 : verdict.color)
        }
        .font(.system(size: 12))
        .foregroundStyle(Theme.ink3)
    }

    /// Shared with the transition screen (`Collection<Transfer>.verdictSummary`): an
    /// `infeasible` change used to fall through every count here and print "all
    /// clear" — in red, since the tint follows the rolled-up verdict.
    private var metaTag: String { journey.transfers.verdictSummary }

    private func shortStation(_ s: String) -> String {
        s.replacingOccurrences(of: " Hbf", with: "")
         .replacingOccurrences(of: " (Main)", with: "")
    }
}
