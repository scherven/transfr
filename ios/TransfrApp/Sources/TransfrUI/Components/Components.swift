import SwiftUI
import TransfrCore

// Small, reusable pieces shared by the screens — the prototype's recurring
// building blocks (verdict pill, platform chip, panel card, eyebrow).

/// The verdict pill (go / tight / miss / nodata). Colour + icon come from the
/// `Verdict` extension in Theme.swift.
struct VerdictBadge: View {
    let verdict: Verdict
    var compact: Bool = false

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: verdict.iconName)
                .font(.system(size: compact ? 9 : 11, weight: .bold))
            if !compact { Text(verdict.label).font(.system(size: 12, weight: .semibold)) }
        }
        .foregroundStyle(verdict.color)
        .padding(.horizontal, compact ? 7 : 9)
        .padding(.vertical, compact ? 4 : 5)
        .background(Capsule().fill(verdict.softColor))
    }
}

/// "Pl 4 → 5" style chip. `emphasis` bolds the platform numbers.
struct PlatformChip: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 13, weight: .semibold, design: .monospaced))
            .foregroundStyle(Theme.ink)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(Capsule().fill(Theme.panel2))
    }
}

/// A small step-free badge — shown when DB InfraGO's OpenStation crosswalk reports
/// this change of train is step-free (both platforms reachable without steps). A
/// served lift is noted too. Absence means "unknown", never "not step-free", so the
/// badge only ever appears as a positive.
struct StepFreeChip: View {
    var lift: Bool = false
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "figure.roll").font(.system(size: 10, weight: .semibold))
            Text(lift ? "Step-free · lift" : "Step-free").font(.system(size: 11, weight: .semibold))
        }
        .foregroundStyle(Theme.go)
        .padding(.horizontal, 7).padding(.vertical, 3)
        .background(Capsule().fill(Theme.goSoft))
        .accessibilityElement()
        .accessibilityLabel(lift ? "Step-free, with a lift" : "Step-free")
    }
}

/// Hint treatment C: a small muted pill reconciling the real platform shown beside
/// it with the operator timetable's own code, for a platform the feed labels with
/// an internal number the station signs don't use (Köln Hbf "89" vs Gleis 7). Info
/// icon + code, so it reads as a note about the number, not another platform.
struct FeedCodeChip: View {
    let text: String
    var accessibility: String? = nil
    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: "info.circle").font(.system(size: 9, weight: .semibold))
            Text(text).font(.system(size: 11, weight: .medium, design: .monospaced))
        }
        .foregroundStyle(Theme.ink3)
        .padding(.horizontal, 7).padding(.vertical, 3)
        .background(Capsule().fill(Theme.panel))
        .overlay(Capsule().strokeBorder(Theme.line, lineWidth: 1))
        .accessibilityElement()
        .accessibilityLabel(accessibility ?? text)
    }
}

/// A rounded panel — the prototype's card surface, with the shared radius, a
/// hairline border, and a soft shadow.
struct Panel<Content: View>: View {
    var padding: CGFloat = 16
    var tint: Color = Theme.panel
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .background(
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).fill(tint)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .strokeBorder(Theme.line, lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.06), radius: 16, x: 0, y: 10)
    }
}

/// The small uppercase section label.
struct Eyebrow: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 11, weight: .semibold))
            .tracking(0.8)
            .foregroundStyle(Theme.ink3)
    }
}

/// A labelled stat cell ("Distance / 78 m").
struct StatCell: View {
    let key: String
    let value: String
    var valueColor: Color = Theme.ink
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(key).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            Text(value).font(.system(size: 15, weight: .semibold, design: .monospaced))
                .foregroundStyle(valueColor)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A circular countdown ring: `used`/`total` of the layover, coloured by verdict.
///
/// **Neither half is ever fabricated.** Both are optional and a `0` reads the same as
/// `nil` (the server returns `walk_time_s: 0.0` for a walk it couldn't measure — the
/// rule `Fmt.walkTime` and `Transfer.spareSeconds` already apply). The arc is a
/// *proportion*, so it can only be stroked when both numbers are real; otherwise the
/// track goes dashed and the missing half reads "—". An empty ring over a fabricated
/// "0s" said the opposite of the truth — that this change needs no walking at all —
/// and a `layover ?? 1` gave the same ring a confident "of 0m" beneath it.
struct WalkRing: View {
    let usedSeconds: Double?
    let totalSeconds: Double?
    let verdict: Verdict

    /// The walk time only when it's a usable figure; nil when it was never measured.
    private var measured: Double? {
        guard let s = usedSeconds, s > 0 else { return nil }
        return s
    }

    /// The layover only when the journey actually carries one.
    private var total: Double? {
        guard let s = totalSeconds, s > 0 else { return nil }
        return s
    }

    private var fraction: Double {
        guard let m = measured, let t = total else { return 0 }
        return min(max(m / t, 0), 1)
    }

    var body: some View {
        ZStack {
            if let m = measured, total != nil {
                Circle().stroke(Theme.panel2, lineWidth: 4)
                Circle()
                    .trim(from: 0, to: fraction)
                    .stroke(verdict.color, style: StrokeStyle(lineWidth: 4, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                ringText("\(Int(m.rounded()))s")
            } else {
                Circle().stroke(Theme.nodata.opacity(0.45),
                                style: StrokeStyle(lineWidth: 4, dash: [3, 5]))
                ringText(measured.map { "\(Int($0.rounded()))s" } ?? "—")
            }
        }
        .frame(width: 62, height: 62)
    }

    private func ringText(_ used: String) -> some View {
        VStack(spacing: 0) {
            Text(used)
                .font(.system(size: 16, weight: .bold, design: .monospaced))
                .foregroundStyle(Theme.ink)
            Text("of \(Fmt.duration(total.map { Int($0) }))")
                .font(.system(size: 9)).foregroundStyle(Theme.ink3)
        }
    }
}

/// Primary / ghost button styling used on the CTAs.
///
/// Both render a real disabled state. They used to read only `isPressed`, so a
/// `.disabled(…)` CTA — "Find connections" with an empty field, most visibly — was
/// pixel-identical to a live one: the tap simply did nothing, with no cue as to why.
/// `isEnabled` has to be read from a **View's** environment rather than the style's
/// own (a `ButtonStyle` isn't a view, so an `@Environment` property on it doesn't
/// track `.disabled`), hence the small inner body view in each.
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { Content(configuration: configuration) }

    // Not named `Body`: that's `ButtonStyle`'s own associated type, and a private
    // nested type of that name is inferred as the conformance's `Body`.
    private struct Content: View {
        let configuration: ButtonStyleConfiguration
        @Environment(\.isEnabled) private var isEnabled

        var body: some View {
            configuration.label
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(isEnabled ? Color.white : Theme.ink3)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 15)
                .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(isEnabled ? Theme.accent : Theme.panel3))
                .opacity(isEnabled && configuration.isPressed ? 0.85 : 1)
        }
    }
}

struct GhostButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { Content(configuration: configuration) }

    private struct Content: View {
        let configuration: ButtonStyleConfiguration
        @Environment(\.isEnabled) private var isEnabled

        var body: some View {
            configuration.label
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(isEnabled ? Theme.ink : Theme.ink3)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 13)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.panel2))
                .opacity(isEnabled ? (configuration.isPressed ? 0.85 : 1) : 0.6)
        }
    }
}

/// A left-aligned wrapping flow layout. Lays subviews out left-to-right and wraps
/// to a new row when the next subview would overflow the available width. Each
/// subview keeps its natural (ideal) size — nothing is compressed — so pills flow
/// onto extra rows instead of being crunched into one. iOS 16+ `Layout`.
struct FlowLayout: Layout {
    /// Horizontal gap between subviews on the same row.
    var spacing: CGFloat = 6
    /// Vertical gap between wrapped rows.
    var lineSpacing: CGFloat = 6

    private struct Row {
        var indices: [Int] = []
        var height: CGFloat = 0
    }

    /// Break `sizes` into rows for a given max width and report the content size.
    private func arrange(maxWidth: CGFloat, sizes: [CGSize]) -> (rows: [Row], size: CGSize) {
        var rows: [Row] = []
        var current = Row()
        var x: CGFloat = 0
        for (i, size) in sizes.enumerated() {
            // Wrap when this subview would overflow — but always keep at least one
            // subview per row so an over-wide pill still gets placed.
            if !current.indices.isEmpty && x + size.width > maxWidth {
                rows.append(current)
                current = Row()
                x = 0
            }
            current.indices.append(i)
            current.height = max(current.height, size.height)
            x += size.width + spacing
        }
        if !current.indices.isEmpty { rows.append(current) }

        let width = rows.map { row in
            row.indices.reduce(CGFloat(0)) { $0 + sizes[$1].width + spacing } - spacing
        }.max() ?? 0
        let height = rows.reduce(CGFloat(0)) { $0 + $1.height }
            + lineSpacing * CGFloat(max(rows.count - 1, 0))
        return (rows, CGSize(width: max(width, 0), height: height))
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        let (_, size) = arrange(maxWidth: maxWidth, sizes: sizes)
        // Fill the proposed width when finite so the container aligns to its parent;
        // fall back to the intrinsic content width when width is unconstrained.
        return CGSize(width: maxWidth.isFinite ? maxWidth : size.width, height: size.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        let (rows, _) = arrange(maxWidth: bounds.width, sizes: sizes)
        var y = bounds.minY
        for row in rows {
            var x = bounds.minX
            for i in row.indices {
                let size = sizes[i]
                // Center each subview vertically within its row so short items
                // (e.g. an arrow) align with taller pills.
                let yOffset = (row.height - size.height) / 2
                subviews[i].place(
                    at: CGPoint(x: x, y: y + yOffset),
                    anchor: .topLeading,
                    proposal: ProposedViewSize(size)
                )
                x += size.width + spacing
            }
            y += row.height + lineSpacing
        }
    }
}
