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
                JourneyCard(journey: journey, isBest: idx == 0)
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
        return "Today · from \(f.string(from: model.departure)) · \(n) option\(n == 1 ? "" : "s")"
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
                    rail
                    meta
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
    }

    /// The route as a little line diagram (issue #19's successor): trains are the
    /// labelled azure segments, changes are the verdict-ringed dots between them.
    /// Distinct shapes for distinct things — a leg you ride vs. a point you change —
    /// where the old design drew both as identical pills. Every train is named (the
    /// old flow named only the first). `RailStrip` centers the rail when it fits and
    /// scrolls it back and forth when a long trip overruns the card.
    private var rail: some View {
        let names = journey.legs.compactMap(\.trainName)
        let legNames = names.isEmpty ? ["—"] : names          // a journey always has ≥1 named leg
        let changes = journey.transfers
        return RailStrip {
            RailLayout {
                RailNode(kind: .origin)
                ForEach(legNames.indices, id: \.self) { i in
                    RailEdge(label: legNames[i])
                    if i < changes.count {
                        RailNode(kind: .change(
                            station: shortStation(changes[i].atStation ?? "—"),
                            plat: "\(changes[i].arrivalPlatform ?? "?")→\(changes[i].departurePlatform ?? "?")",
                            verdict: changes[i].verdictKind))
                    }
                }
                RailNode(kind: .dest)
            }
        }
        .padding(.top, 2)
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

    private var metaTag: String {
        let pending = journey.transfers.filter { $0.verdictKind.isPending }.count
        if pending > 0 { return "checking…" }
        let tights = journey.transfers.filter { $0.verdictKind == .tight }.count
        let unknowns = journey.transfers.filter { if case .unknown = $0.verdictKind { return true }; return false }.count
        if unknowns > 0 { return "\(unknowns) unknown" }
        if tights > 0 { return "\(tights) tight" }
        return "all clear"
    }

    private func shortStation(_ s: String) -> String {
        s.replacingOccurrences(of: " Hbf", with: "")
         .replacingOccurrences(of: " (Main)", with: "")
    }
}

// MARK: - Stops rail

/// Geometry shared by `RailNode` and `RailEdge` so their bands line up: a fixed
/// label zone on top, then the track band (dot / line); a change's caption hangs
/// below. Keeping the top two zones the same height on every child is what keeps the
/// dots and the edges' line colinear once `RailLayout` top-aligns them.
private enum Rail {
    static let labelH: CGFloat = 14
    static let trackH: CGFloat = 16
    static let lineH: CGFloat = 3
    static let dot: CGFloat = 12
    static let dotChange: CGFloat = 14
    static let terminalW: CGFloat = 16
    /// Fixed height of the whole strip (label + track + a two-line caption).
    static let height: CGFloat = 66
    /// How long the scroll pauses at each end so the stops there can be read.
    static let scrollDwell: TimeInterval = 1.0
}

private enum RailKind {
    case origin, dest
    case change(station: String, plat: String, verdict: Verdict)
}

/// A stop on the rail. Origin/destination are filled accent dots; a change is a
/// verdict-ringed dot with the station and platform swap centered beneath it. The
/// stop draws only the dot — the line is drawn by the edges (which meet under each
/// dot), so the caption's width sets the stop's width and nothing offsets the dot.
private struct RailNode: View {
    let kind: RailKind

    private var isTerminal: Bool { if case .change = kind { return false }; return true }

    var body: some View {
        let node = VStack(spacing: 0) {
            Color.clear.frame(height: Rail.labelH)          // align the dot band with the edges' label zone
            dot.frame(height: Rail.trackH)
            caption
        }
        if isTerminal { node.frame(width: Rail.terminalW) } else { node.fixedSize(horizontal: true, vertical: false) }
    }

    @ViewBuilder private var dot: some View {
        switch kind {
        case .origin, .dest:
            Circle().fill(Theme.accent).frame(width: Rail.dot, height: Rail.dot)
        case .change(_, _, let verdict):
            Circle().fill(Theme.panel)
                .overlay(Circle().strokeBorder(verdict.color, lineWidth: 3))
                .frame(width: Rail.dotChange, height: Rail.dotChange)
        }
    }

    @ViewBuilder private var caption: some View {
        if case .change(let station, let plat, let verdict) = kind {
            VStack(spacing: 1) {
                Text(station).font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(verdict.color).lineLimit(1).fixedSize()
                Text(plat).font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(Theme.ink3).lineLimit(1).fixedSize()
            }
            .padding(.top, 5)
        }
    }
}

/// A train leg on the rail: the service name centered above its stretch of line.
/// `RailLayout` stretches the edge to span the exact gap between the two dots, so
/// the label lands dead-center over the leg and the lines tile into one rail.
private struct RailEdge: View {
    let label: String
    var body: some View {
        VStack(spacing: 0) {
            Text(label)
                .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(Theme.ink2)
                .lineLimit(1).fixedSize()
                .frame(height: Rail.labelH)
            Color.clear.frame(height: Rail.trackH)
                .overlay(Rectangle().fill(Theme.accent).frame(height: Rail.lineH))
        }
    }
}

/// Keeps the rail on one line. When it fits the card it's centered; when a long trip
/// overruns, it eases back and forth so every stop can be read (a plain horizontal
/// scroll instead, under Reduce Motion). The natural width is measured off a hidden
/// copy, then compared with the available width to pick the behaviour.
private struct RailStrip<Content: View>: View {
    @ViewBuilder var content: Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var contentW: CGFloat = 0

    var body: some View {
        GeometryReader { geo in
            let vw = geo.size.width
            let overflow = max(contentW - vw, 0)
            ZStack(alignment: .topLeading) {
                content.fixedSize().hidden().measureWidth($contentW)   // measure the natural width
                if overflow == 0 {
                    content.fixedSize().frame(width: vw, alignment: .center)
                } else if reduceMotion {
                    ScrollView(.horizontal, showsIndicators: false) { content.fixedSize() }
                        .frame(width: vw)
                } else {
                    Color.clear.frame(width: vw, height: Rail.height)
                        .overlay(alignment: .topLeading) {
                            // Ping-pong between the two ends, dwelling at each (the
                            // `.delay`) so the stops there can be read before it moves.
                            content.fixedSize()
                                .phaseAnimator([false, true]) { view, atEnd in
                                    view.offset(x: atEnd ? -overflow : 0)
                                } animation: { _ in
                                    .easeInOut(duration: max(Double(overflow) / 42, 1.2))
                                        .delay(Rail.scrollDwell)
                                }
                        }
                        .clipped()
                }
            }
        }
        .frame(height: Rail.height)
    }
}

private extension View {
    /// Report this view's laid-out width back into a binding.
    func measureWidth(_ width: Binding<CGFloat>) -> some View {
        background(GeometryReader { g in
            Color.clear
                .onAppear { width.wrappedValue = g.size.width }
                .onChange(of: g.size.width) { _, newValue in width.wrappedValue = newValue }
        })
    }
}
