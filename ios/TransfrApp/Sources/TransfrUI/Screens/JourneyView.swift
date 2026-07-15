import SwiftUI
import TransfrCore

/// The chosen connection as a vertical timeline — the prototype's `#s-journey`.
/// Legs and transfers are interleaved; each transfer is a tappable card that
/// jumps into the carousel at that change.
struct JourneyView: View {
    @Environment(TripModel.self) private var model

    private var journey: Journey? { model.selected }

    var body: some View {
        ScrollView {
            if let journey {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(rows(for: journey)) { row in
                        TimelineRow(row: row)
                    }
                }
                .padding(20)
            }
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Your connection")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) { principal }
        }
        .safeAreaInset(edge: .bottom) { bottomBar }
    }

    private var principal: some View {
        VStack(spacing: 1) {
            Text("Your connection").font(.system(size: 16, weight: .semibold))
            if let j = journey {
                Text("\(Fmt.time(j.departureISO)) → \(Fmt.time(j.arrivalISO)) · \(Fmt.duration(j.durationS)) · \(j.numChanges) changes")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
            }
        }
    }

    private var bottomBar: some View {
        HStack(spacing: 10) {
            Button {
                model.path.append(.live)
            } label: {
                Label("Track live", systemImage: "dot.radiowaves.left.and.right")
            }
            .buttonStyle(GhostButtonStyle())

            Button {
                model.path.append(.carousel(startIndex: 0))
            } label: {
                Label("Transfers (\(model.transfers.count))", systemImage: "figure.walk")
            }
            .buttonStyle(PrimaryButtonStyle())
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.thinMaterial)
    }

    // MARK: - Row model

    /// A flattened timeline entry. `transferIndex` links a transfer card to the
    /// carousel/walk destination.
    struct RowItem: Identifiable {
        enum Kind { case station, train, transfer, terminal }
        let id = UUID()
        let kind: Kind
        var time: String = ""
        var delay: String? = nil
        var late: Bool = false
        var station: String? = nil
        var platform: String? = nil
        var trainName: String? = nil
        var trainSub: String? = nil
        var trainDur: String? = nil
        var transfer: Transfer? = nil
        var transferIndex: Int? = nil
        var isFirst = false
        var isLast = false
    }

    private func rows(for j: Journey) -> [RowItem] {
        var out: [RowItem] = []
        guard let first = j.legs.first else { return out }

        // Origin
        out.append(RowItem(kind: .station, time: Fmt.time(first.departure),
                           station: first.origin.name, platform: first.departurePlatform,
                           isFirst: true))
        out.append(RowItem(kind: .train, trainName: first.trainName,
                           trainSub: "dir. \(first.destination.name ?? "—")",
                           trainDur: dur(first)))

        // Each transfer sits between leg i and leg i+1.
        for (i, t) in j.transfers.enumerated() {
            let arriving = j.legs[safe: i]
            let departing = j.legs[safe: i + 1]
            out.append(RowItem(kind: .transfer,
                               time: Fmt.time(arriving?.arrival),
                               delay: Fmt.delay(arriving?.arrivalDelayS),
                               late: (arriving?.arrivalDelayS ?? 0) > 0,
                               station: t.atStation,
                               transfer: t, transferIndex: i))
            if let departing {
                out.append(RowItem(kind: .train,
                                   time: Fmt.time(departing.departure),
                                   trainName: departing.trainName,
                                   trainSub: "Pl \(departing.departurePlatform ?? "—") · dir. \(departing.destination.name ?? "—")",
                                   trainDur: dur(departing)))
            }
        }

        // Destination
        if let last = j.legs.last {
            out.append(RowItem(kind: .terminal, time: Fmt.time(last.arrival),
                               station: last.destination.name,
                               platform: last.arrivalPlatform.map { "Arrives Pl \($0)" },
                               isLast: true))
        }
        return out
    }

    private func dur(_ leg: Leg) -> String {
        guard let d = Fmt.date(leg.departure), let a = Fmt.date(leg.arrival) else { return "" }
        return Fmt.duration(Int(a.timeIntervalSince(d)))
    }
}

/// One timeline row: time column, node+track spine, body.
private struct TimelineRow: View {
    @Environment(TripModel.self) private var model
    let row: JourneyView.RowItem

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // Time column
            VStack(alignment: .trailing, spacing: 1) {
                Text(row.time).font(.system(size: 14, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Theme.ink)
                if let delay = row.delay {
                    Text(delay).font(.system(size: 10))
                        .foregroundStyle(row.late ? Theme.miss : Theme.ink3)
                }
            }
            .frame(width: 48, alignment: .trailing)
            .opacity(row.kind == .train ? 0.55 : 1)

            // Spine
            VStack(spacing: 0) {
                Circle()
                    .fill(row.kind == .terminal || row.isFirst ? Theme.accent : Theme.panel)
                    .frame(width: 12, height: 12)
                    .overlay(Circle().strokeBorder(Theme.accent, lineWidth: 2))
                if !row.isLast {
                    Rectangle().fill(Theme.line).frame(width: 2).frame(maxHeight: .infinity)
                }
            }
            .frame(width: 12)

            // Body
            body(for: row)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.bottom, row.isLast ? 0 : 16)
        }
        .frame(minHeight: 44)
    }

    @ViewBuilder
    private func body(for row: JourneyView.RowItem) -> some View {
        switch row.kind {
        case .station, .terminal:
            VStack(alignment: .leading, spacing: 6) {
                Text(row.station ?? "—").font(.system(size: 17, weight: .bold)).foregroundStyle(Theme.ink)
                if let p = row.platform {
                    PlatformChip(text: p.hasPrefix("Arrives") ? p : "Platform \(p)")
                }
            }
        case .train:
            trainCard(row)
        case .transfer:
            VStack(alignment: .leading, spacing: 8) {
                Text(row.station ?? "—").font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.ink)
                if let t = row.transfer, let idx = row.transferIndex {
                    TransferCard(transfer: t) { model.path.append(.carousel(startIndex: idx)) }
                }
            }
        }
    }

    private func trainCard(_ row: JourneyView.RowItem) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "tram.fill").font(.system(size: 15))
                .foregroundStyle(Theme.accent)
                .frame(width: 34, height: 34)
                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.accentSoft))
            VStack(alignment: .leading, spacing: 1) {
                Text(row.trainName ?? "—").font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink)
                Text(row.trainSub ?? "").font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
            Spacer()
            Text(row.trainDur ?? "").font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.ink3)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line, lineWidth: 1))
    }
}

/// The transfer card inside the timeline: verdict-tinted, walk/layover summary,
/// tap to open the carousel.
struct TransferCard: View {
    let transfer: Transfer
    var onTap: () -> Void

    private var v: Verdict { transfer.verdictKind }

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    HStack(spacing: 6) {
                        Image(systemName: v.iconName).font(.system(size: 11, weight: .bold))
                        Text(title).font(.system(size: 13, weight: .semibold))
                    }
                    .foregroundStyle(v.color)
                    Spacer()
                    HStack(spacing: 2) {
                        Text("View").font(.system(size: 12, weight: .semibold))
                        Image(systemName: "chevron.right").font(.system(size: 10, weight: .bold))
                    }.foregroundStyle(Theme.ink3)
                }
                HStack(spacing: 12) {
                    PlatformChip(text: "Pl \(transfer.arrivalPlatform ?? "?") → \(transfer.departurePlatform ?? "?")")
                    walkItem("Walk", Fmt.walkTime(transfer.walkTimeS))
                    if let spare = transfer.spareSeconds {
                        walkItem("Left", Fmt.duration(Int(spare)), color: v == .tight ? Theme.tight : Theme.ink)
                    }
                }
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 16).fill(v.softColor))
            .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(v.color.opacity(0.25), lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    private var title: String {
        switch v {
        case .feasible:   return "Cross-platform · comfortable"
        case .tight:      return "Tight — move promptly"
        case .infeasible: return "Very likely to miss"
        case .unknown:    return "No platform data here"
        }
    }

    private func walkItem(_ k: String, _ val: String, color: Color = Theme.ink) -> some View {
        HStack(spacing: 4) {
            Text(k).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            Text(val).font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(color)
        }
    }
}

extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
