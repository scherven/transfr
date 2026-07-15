import SwiftUI

// The prototype's settings / list vocabulary, ported to SwiftUI: section headers,
// setting rows, cards, the pill segmented control, the toggle, the 3-zone bar, and
// the navigation (arrow) rows used across Settings, Advanced, and the tool screens.

/// Uppercase section label (`.set-h`).
struct SectionHeader: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10.5, weight: .semibold)).tracking(1.3)
            .foregroundStyle(Theme.ink3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 2).padding(.top, 14).padding(.bottom, 8)
    }
}

/// A rounded panel row (`.set-row`) — icon, title/subtitle, trailing control.
struct SettingRow<Trailing: View>: View {
    let icon: String
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 12) {
            SetIcon(icon)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                if let subtitle {
                    Text(subtitle).font(.system(size: 11.5)).foregroundStyle(Theme.ink3).lineSpacing(1)
                }
            }
            Spacer(minLength: 8)
            trailing
        }
        .padding(.horizontal, 13).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 13).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(Theme.line, lineWidth: 1))
    }
}

/// A stacked setting row: icon + title/subtitle on top, a full-width control below
/// (used for the segmented controls and the makeable slider).
struct SettingStack<Content: View>: View {
    let icon: String
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(spacing: 12) {
                SetIcon(icon)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                    if let subtitle {
                        Text(subtitle).font(.system(size: 11.5)).foregroundStyle(Theme.ink3)
                    }
                }
                Spacer(minLength: 0)
            }
            content
        }
        .padding(.horizontal, 13).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 13).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(Theme.line, lineWidth: 1))
    }
}

/// A tappable navigation row (`.arow`) — icon, title/subtitle, chevron.
struct NavRow: View {
    let icon: String
    let title: String
    var subtitle: String? = nil
    let route: Route

    var body: some View {
        NavigationLink(value: route) {
            HStack(spacing: 12) {
                SetIcon(icon)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                    if let subtitle {
                        Text(subtitle).font(.system(size: 11.5)).foregroundStyle(Theme.ink3)
                    }
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.ink3)
            }
            .padding(.horizontal, 13).padding(.vertical, 12)
            .background(RoundedRectangle(cornerRadius: 13).fill(Theme.panel))
            .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(Theme.line, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }
}

/// The 32×32 rounded icon tile (`.set-ic`).
struct SetIcon: View {
    let name: String
    var tint: Color = Theme.ink2
    var bg: Color = Theme.panel2
    init(_ name: String, tint: Color = Theme.ink2, bg: Color = Theme.panel2) {
        self.name = name; self.tint = tint; self.bg = bg
    }
    var body: some View {
        Image(systemName: name).font(.system(size: 14, weight: .medium))
            .foregroundStyle(tint)
            .frame(width: 32, height: 32)
            .background(RoundedRectangle(cornerRadius: 9).fill(bg))
    }
}

/// A bordered panel card (`.set-card`).
struct SetCard<Content: View>: View {
    var tint: Color = Theme.panel
    var border: Color = Theme.line
    @ViewBuilder var content: Content
    var body: some View {
        content
            .padding(13)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 13).fill(tint))
            .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(border, lineWidth: 1))
    }
}

/// The pill segmented control (`.sseg`). Generic over any `Hashable` option.
struct SegmentedControl<T: Hashable>: View {
    let options: [T]
    @Binding var selection: T
    var label: (T) -> String

    var body: some View {
        HStack(spacing: 2) {
            ForEach(options, id: \.self) { opt in
                Button {
                    withAnimation(.snappy(duration: 0.18)) { selection = opt }
                } label: {
                    Text(label(opt))
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundStyle(selection == opt ? Theme.ink : Theme.ink2)
                        .frame(maxWidth: .infinity).padding(.vertical, 7)
                        .background(
                            RoundedRectangle(cornerRadius: 7)
                                .fill(selection == opt ? Theme.panel : .clear)
                                .shadow(color: selection == opt ? .black.opacity(0.18) : .clear, radius: 3, y: 1)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(RoundedRectangle(cornerRadius: 9).fill(Theme.panel2))
    }
}

/// The custom toggle (`.tgl`) — accent when on.
struct TransfrToggle: View {
    @Binding var isOn: Bool
    var body: some View {
        Button { withAnimation(.snappy(duration: 0.18)) { isOn.toggle() } } label: {
            RoundedRectangle(cornerRadius: 13)
                .fill(isOn ? Theme.accent : Theme.panel3)
                .frame(width: 44, height: 26)
                .overlay(alignment: isOn ? .trailing : .leading) {
                    Circle().fill(.white).frame(width: 20, height: 20)
                        .shadow(color: .black.opacity(0.2), radius: 2, y: 1)
                        .padding(3)
                }
        }
        .buttonStyle(.plain)
    }
}

/// The 3-zone makeable/tight/miss bar (`.zbar`). Weights are flex-grow ratios.
struct ZoneBar: View {
    let zones: [(weight: Int, color: Color)]
    var height: CGFloat = 8
    var body: some View {
        GeometryReader { geo in
            let total = max(zones.reduce(0) { $0 + $1.weight }, 1)
            let gap: CGFloat = 2
            let usable = geo.size.width - gap * CGFloat(zones.count - 1)
            HStack(spacing: gap) {
                ForEach(Array(zones.enumerated()), id: \.offset) { _, z in
                    RoundedRectangle(cornerRadius: 3).fill(z.color)
                        .frame(width: usable * CGFloat(z.weight) / CGFloat(total))
                }
            }
        }
        .frame(height: height)
    }
}

/// The badge pill used outside the verdict system (e.g. "Installed", "Live").
struct StatusBadge: View {
    let text: String
    var color: Color = Theme.go
    var showDot = false
    var body: some View {
        HStack(spacing: 6) {
            if showDot { Circle().fill(color).frame(width: 7, height: 7) }
            Text(text).font(.system(size: 11.5, weight: .semibold))
        }
        .foregroundStyle(color)
        .padding(.horizontal, 9).padding(.vertical, 5)
        .background(RoundedRectangle(cornerRadius: 8).fill(color.opacity(0.14)))
    }
}

/// A small pill button (`.btn-s`) with primary / danger / plain variants.
struct SmallButton: View {
    enum Kind { case plain, primary, danger }
    let title: String
    var kind: Kind = .plain
    var action: () -> Void = {}

    var body: some View {
        Button(action: action) {
            Text(title).font(.system(size: 12, weight: .medium))
                .foregroundStyle(fg)
                .padding(.horizontal, 13).padding(.vertical, 7)
                .background(RoundedRectangle(cornerRadius: 9).fill(bg))
                .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(Theme.line2, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    private var fg: Color { kind == .primary ? .white : (kind == .danger ? Theme.miss : Theme.ink) }
    private var bg: Color { kind == .primary ? Theme.accent : (kind == .danger ? Theme.missSoft : Theme.panel2) }
}
