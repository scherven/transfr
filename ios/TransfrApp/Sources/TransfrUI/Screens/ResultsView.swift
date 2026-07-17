import SwiftUI
import TransfrCore

/// The options list — the prototype's `#s-results`. One `JourneyCard` per option,
/// the first flagged "Best". Tapping a card selects it and pushes the timeline.
struct ResultsView: View {
    @Environment(TripModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                ForEach(Array(model.journeys.enumerated()), id: \.element.id) { idx, journey in
                    Button {
                        model.select(journey)
                    } label: {
                        JourneyCard(journey: journey, isBest: idx == 0)
                    }
                    .buttonStyle(.plain)
                }
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
                    Text(subtitle).font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.ink3)
                }
            }
        }
    }

    private var navTitle: String {
        "\(short(model.origin)) → \(short(model.destination))"
    }

    private var subtitle: String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_GB"); f.dateFormat = "HH:mm"
        return "Today · from \(f.string(from: model.departure)) · \(model.journeys.count) options"
    }

    private func short(_ s: String) -> String {
        s.replacingOccurrences(of: " Hbf", with: "")
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
        let plat = "\(t.arrivalPlatform ?? "?")→\(t.departurePlatform ?? "?")"
        return HStack(spacing: 4) {
            Text(shortStation(t.atStation ?? "")).font(.system(size: 12, weight: .semibold)).lineLimit(1)
            Text(plat).font(.system(size: 11, weight: .medium, design: .monospaced)).opacity(0.7).lineLimit(1)
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
