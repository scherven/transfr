import SwiftUI
import UIKit
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
    /// labelled azure legs, changes the verdict-ringed dots between them. Distinct
    /// shapes for distinct things — a leg you ride vs. a point you change — where the
    /// old design drew both as identical pills. Every train is named (the old flow
    /// named only the first); a long trip wraps, each wrapped row ending in a small
    /// continuation arrow (`WrappingRail`).
    private var rail: some View {
        let changes = journey.transfers
        let names = journey.legs.compactMap(\.trainName)
        // A well-formed journey has one more transit leg than it has changes; pad or
        // trim the names to that shape so the rail's stops and legs always line up.
        let legCount = max(changes.count + 1, 1)
        let legs = (0 ..< legCount).map { $0 < names.count ? names[$0] : "—" }
        var stops: [RailKind] = [.origin]
        for c in changes {
            stops.append(.change(
                station: shortStation(c.atStation ?? "—"),
                plat: "\(c.arrivalPlatform ?? "?")→\(c.departurePlatform ?? "?")",
                verdict: c.verdictKind))
        }
        stops.append(.dest)
        return WrappingRail(stops: stops, legs: legs).padding(.top, 2)
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

/// Vertical + spacing geometry for the rail. Each row is [service-label zone] /
/// [dot + line band] / [two-line caption], so labels sit above the line and stations
/// below it; rows stack at a fixed height when a long trip wraps.
private enum Rail {
    static let labelH: CGFloat = 14
    static let trackH: CGFloat = 16
    static let lineH: CGFloat = 3
    static let dot: CGFloat = 12
    static let dotChange: CGFloat = 14
    static let terminalW: CGFloat = 16
    static let labelPad: CGFloat = 10   // min slack around a service label within its leg
    static let capGap: CGFloat = 12     // min clearance between two neighbouring captions
    static let arrowW: CGFloat = 18     // continuation arrow at the end of a wrapped row
    static let rowGap: CGFloat = 10     // vertical gap between wrapped rows
    static let captionH: CGFloat = 30   // station line + platform line

    static var lineCenterY: CGFloat { labelH + trackH / 2 }
    static var rowHeight: CGFloat { labelH + trackH + captionH }
}

private enum RailKind {
    case origin, dest
    case change(station: String, plat: String, verdict: Verdict)
}

/// SF metrics for the rail's text, used to size legs and captions *before* layout so
/// the wrap maths and the centering can be computed up front.
private enum RailFont {
    static let leg = UIFont.monospacedSystemFont(ofSize: 10.5, weight: .semibold)
    static let station = UIFont.systemFont(ofSize: 12, weight: .semibold)
    static let plat = UIFont.monospacedSystemFont(ofSize: 11, weight: .medium)
    static func width(_ s: String, _ f: UIFont) -> CGFloat {
        ceil((s as NSString).size(withAttributes: [.font: f]).width)
    }
}

/// The width a stop reserves around its dot: a terminus reserves only the dot; a
/// change reserves its widest caption line. Drives both wrapping and centering.
private func railCapWidth(_ k: RailKind) -> CGFloat {
    switch k {
    case .origin, .dest: return Rail.terminalW
    case .change(let s, let p, _):
        return max(RailFont.width(s, RailFont.station), RailFont.width(p, RailFont.plat))
    }
}

private struct RailLeg { let label: String; let x0: CGFloat; let x1: CGFloat }
private struct RailStop { let kind: RailKind; let cx: CGFloat; let capW: CGFloat }
private struct RailRow { var legs: [RailLeg]; var stops: [RailStop]; var arrowX: CGFloat? }

/// The route as a line diagram that WRAPS. It stays on one centered line when it fits
/// the card; a long trip breaks onto more rows, and every wrapped row ends in a small
/// continuation arrow. Text is measured (SF metrics) so each dot is placed and each
/// service label centers over its own leg — the ends included.
private struct WrappingRail: View {
    let stops: [RailKind]   // origin, change…, dest   (count == legs.count + 1)
    let legs: [String]      // one service label per leg
    @State private var availW: CGFloat = 320

    var body: some View {
        let rows = solve(width: availW)
        return VStack(alignment: .leading, spacing: Rail.rowGap) {
            ForEach(rows.indices, id: \.self) { r in
                RailRowView(row: rows[r]).frame(height: Rail.rowHeight)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(GeometryReader { g in
            Color.clear
                .onAppear { availW = g.size.width }
                .onChange(of: g.size.width) { _, nv in availW = nv }
        })
    }

    /// A continuation row leads with the "bridge" leg from the previous row's last
    /// stop; reserve room for its label.
    private func bridgeW(_ a: Int) -> CGFloat {
        max(RailFont.width(legs[a - 1], RailFont.leg) + Rail.labelPad, 44)
    }

    /// Break the trip into rows that each fit `W`, then place the dots + legs.
    private func solve(width W: CGFloat) -> [RailRow] {
        let n = stops.count
        guard n >= 2, legs.count == n - 1 else { return [] }
        let W = max(W, 1)
        let cap = stops.map(railCapWidth)
        var seg = [CGFloat](repeating: 0, count: legs.count)
        for i in legs.indices {
            seg[i] = max(RailFont.width(legs[i], RailFont.leg) + Rail.labelPad,
                         cap[i] / 2 + cap[i + 1] / 2 + Rail.capGap)
        }

        // 1. pack contiguous stops into rows.
        var ranges: [(a: Int, b: Int)] = []
        var a = 0
        while a < n {
            var b = a
            while b + 1 < n {
                let nb = b + 1
                let left = a == 0 ? cap[a] / 2 : bridgeW(a)
                let span = (a ..< nb).reduce(CGFloat(0)) { $0 + seg[$1] }
                let right = cap[nb] / 2 + (nb < n - 1 ? Rail.arrowW + 6 : 0)
                if left + span + right <= W { b = nb } else { break }
            }
            ranges.append((a, b))
            if b == n - 1 { break }
            a = b + 1
        }

        // 2. place each row: always stretch the legs to fill the card width, so the
        //    rail spans the card whatever the number of changes, and a wrapped row's
        //    arrow sits at the right edge.
        return ranges.map { place(a: $0.a, b: $0.b, n: n, W: W, cap: cap, seg: seg) }
    }

    private func place(a: Int, b: Int, n: Int, W: CGFloat,
                       cap: [CGFloat], seg: [CGFloat]) -> RailRow {
        let isCont = a > 0
        let hasArrow = b < n - 1
        var x = [CGFloat](repeating: 0, count: n)
        x[a] = isCont ? bridgeW(a) : cap[a] / 2
        for d in a ..< b { x[d + 1] = x[d] + seg[d] }
        let natural = x[b] + cap[b] / 2 + (hasArrow ? 6 + Rail.arrowW : 0)

        if b > a {                                            // stretch legs to fill the width
            let per = max(W - natural, 0) / CGFloat(b - a)
            for d in a ..< b { x[d + 1] += per * CGFloat(d - a + 1) }
        }

        var placedLegs: [RailLeg] = []
        if isCont { placedLegs.append(RailLeg(label: legs[a - 1], x0: 0, x1: x[a])) }
        for d in a ..< b { placedLegs.append(RailLeg(label: legs[d], x0: x[d], x1: x[d + 1])) }
        let placedStops = (a ... b).map { RailStop(kind: stops[$0], cx: x[$0], capW: cap[$0]) }
        let arrowX: CGFloat? = hasArrow ? W - Rail.arrowW : nil
        return RailRow(legs: placedLegs, stops: placedStops, arrowX: arrowX)
    }
}

/// One row of the rail, drawn by absolute placement: leg lines first, dots on top,
/// labels above the line, captions below it, then a continuation arrow if it wraps.
private struct RailRowView: View {
    let row: RailRow

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.clear
            ForEach(row.legs.indices, id: \.self) { i in line(row.legs[i]) }
            if let ax = row.arrowX, let last = row.stops.last {          // stub joining the arrow
                Rectangle().fill(Theme.accent)
                    .frame(width: max(ax - last.cx, 0), height: Rail.lineH)
                    .offset(x: last.cx, y: Rail.lineCenterY - Rail.lineH / 2)
            }
            ForEach(row.legs.indices, id: \.self) { i in label(row.legs[i]) }
            ForEach(row.stops.indices, id: \.self) { i in dot(row.stops[i]) }
            ForEach(row.stops.indices, id: \.self) { i in caption(row.stops[i]) }
            if let ax = row.arrowX {
                Image(systemName: "arrow.right")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Theme.ink3)
                    .frame(width: Rail.arrowW, height: Rail.trackH)
                    .offset(x: ax, y: Rail.labelH)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func line(_ leg: RailLeg) -> some View {
        Rectangle().fill(Theme.accent)
            .frame(width: max(leg.x1 - leg.x0, 0), height: Rail.lineH)
            .offset(x: leg.x0, y: Rail.lineCenterY - Rail.lineH / 2)
    }

    private func label(_ leg: RailLeg) -> some View {
        Text(leg.label)
            .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
            .foregroundStyle(Theme.ink2)
            .lineLimit(1)
            .frame(width: max(leg.x1 - leg.x0, 0), height: Rail.labelH)
            .offset(x: leg.x0, y: 0)
    }

    @ViewBuilder private func dot(_ s: RailStop) -> some View {
        switch s.kind {
        case .origin, .dest:
            Circle().fill(Theme.accent)
                .frame(width: Rail.dot, height: Rail.dot)
                .offset(x: s.cx - Rail.dot / 2, y: Rail.lineCenterY - Rail.dot / 2)
        case .change(_, _, let v):
            Circle().fill(Theme.panel)
                .overlay(Circle().strokeBorder(v.color, lineWidth: 3))
                .frame(width: Rail.dotChange, height: Rail.dotChange)
                .offset(x: s.cx - Rail.dotChange / 2, y: Rail.lineCenterY - Rail.dotChange / 2)
        }
    }

    @ViewBuilder private func caption(_ s: RailStop) -> some View {
        if case .change(let station, let plat, let v) = s.kind {
            VStack(spacing: 1) {
                Text(station).font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(v.color).lineLimit(1).fixedSize()
                Text(plat).font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(Theme.ink3).lineLimit(1).fixedSize()
            }
            .frame(width: s.capW)
            .offset(x: s.cx - s.capW / 2, y: Rail.labelH + Rail.trackH + 3)
        }
    }
}
