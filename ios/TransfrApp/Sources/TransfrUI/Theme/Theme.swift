import SwiftUI
import TransfrCore

/// The design tokens from `agents/design/prototype.html`, ported 1:1. Each token is a
/// dynamic `Color` that resolves light/dark automatically (via a `UIColor` trait
/// provider), so views never branch on `colorScheme` — they just use `Theme.go`
/// etc. and both appearances are first-class (DESIGN.md §13.8).
///
/// Semantic rules from the design (§ "Semantic mapping"):
///   • `accent` is the path / active state / primary action — never a verdict.
///   • `go / tight / miss / nodata` are verdicts only — never the path.
///   • `stair / esc / elev` are connector kinds (risers, legend, level icons).
public enum Theme {
    // Backdrop + surfaces
    public static let paper   = dyn(0xF6F8FC, 0x0A0E17)
    public static let panel   = dyn(0xFFFFFF, 0x131A28)
    public static let panel2  = dyn(0xEFF3F9, 0x1A2233)
    public static let panel3  = dyn(0xE7ECF4, 0x222C40)
    public static let bg      = dyn(0xE4E9F1, 0x05070D)

    // Ink
    public static let ink     = dyn(0x0E1626, 0xEAF0FA)
    public static let ink2    = dyn(0x55607A, 0x98A4BC)
    public static let ink3    = dyn(0x8A93A6, 0x616C86)

    // Hairlines
    public static let line    = dynA(0x0E1626, 0.10, 0xFFFFFF, 0.10)
    public static let line2   = dynA(0x0E1626, 0.06, 0xFFFFFF, 0.055)

    // Brand / path
    public static let accent     = dyn(0x0A63F0, 0x4EA6FF)
    public static let accentSoft = dynA(0x0A63F0, 0.10, 0x4EA6FF, 0.14)

    // Verdicts
    public static let go        = dyn(0x0FA968, 0x2FD39A)
    public static let goSoft    = dynA(0x0FA968, 0.12, 0x2FD39A, 0.15)
    public static let tight     = dyn(0xC9820A, 0xF5B740)
    public static let tightSoft = dynA(0xC9820A, 0.14, 0xF5B740, 0.16)
    public static let miss      = dyn(0xE0402F, 0xFF6A5E)
    public static let missSoft  = dynA(0xE0402F, 0.12, 0xFF6A5E, 0.15)
    public static let nodata    = dyn(0x6B7688, 0x8894AB)
    public static let nodataSoft = dynA(0x6B7688, 0.14, 0x8894AB, 0.16)

    // Connector kinds
    public static let stair = dyn(0x8B5CF6, 0xA78BFA)
    public static let esc   = dyn(0x0EA5A5, 0x2DD4BF)
    public static let elev  = dyn(0xE0663A, 0xFB8A5C)

    // Facility marker — the "walk to nearest" POI drawn on the station geometry.
    // A warm rose, deliberately clear of the route (accent blue), the start dot
    // (go green) and every connector hue above, so a facility never reads as one.
    public static let poi     = dyn(0xD8437E, 0xFF7EB6)
    public static let poiSoft = dynA(0xD8437E, 0.12, 0xFF7EB6, 0.16)

    // Route-map surfaces (the vector "paper map" — RouteMapView). Sea is the map
    // backdrop, land the country silhouette; both are picked to read as a soft
    // raised shape over the panel, in either appearance.
    public static let mapSea   = dyn(0xEAF0F8, 0x0B1220)
    public static let mapLand  = dyn(0xDFE6F0, 0x182134)
    public static let mapCoast = dynA(0x0E1626, 0.16, 0xFFFFFF, 0.15)
    public static let mapGrat  = dynA(0x0E1626, 0.055, 0xFFFFFF, 0.05)
    /// Internal country borders. Softer than the coast: the shore is a real edge
    /// of the land, a border is an annotation on it.
    public static let mapBorder = dynA(0x0E1626, 0.22, 0xFFFFFF, 0.18)
    public static let mapCity  = dyn(0xAEB7C7, 0x3A465C)

    public static let radius: CGFloat = 22
    public static let mono  = Font.system(.body, design: .monospaced)

    // MARK: - Font vocabulary (Dynamic Type)
    //
    // The prototype's list/settings type scale, ported so it PARTICIPATES in Dynamic
    // Type (#58). `Font.system(size:)` is a *fixed* point size that ignores the user's
    // text-size setting — and there is no `Font.system(size:relativeTo:)` overload — so
    // a plain `Font` token cannot carry the prototype's tight sizes *and* scale.
    // Instead each token is a `ViewModifier` backed by `@ScaledMetric`, which scales its
    // base size against a semantic `TextStyle` (`relativeTo:`): the exact default-size
    // look is preserved, and the size grows with accessibility settings. Apply via
    // `someView.settingFont(.title)` (see the `View` helper below).
    //
    // `mono` above stays a plain semantic `Font` because `.body` already scales.
    // `monoInline` is likewise semantic (`.caption`) so it can be used *inside* `Text`
    // concatenation, where a `ViewModifier` cannot — the `+` operands must stay `Text`.

    /// Semantic monospaced inline value (e.g. the "up to 3 min" figure inside a
    /// sentence). A `Font` — not a modifier — so it composes with `Text + Text`.
    public static let monoInline = Font.system(.caption, design: .monospaced).weight(.semibold)

    /// A system font at a prototype-fixed design `size` that scales with Dynamic Type,
    /// anchored to `textStyle`. Applied through `View.settingFont(_:)`.
    struct ScaledSystemFont: ViewModifier {
        @ScaledMetric private var size: CGFloat
        private let weight: Font.Weight
        private let design: Font.Design
        init(size: CGFloat, relativeTo textStyle: Font.TextStyle,
             weight: Font.Weight = .regular, design: Font.Design = .default) {
            _size = ScaledMetric(wrappedValue: size, relativeTo: textStyle)
            self.weight = weight
            self.design = design
        }
        func body(content: Content) -> some View {
            content.font(.system(size: size, weight: weight, design: design))
        }
    }

    /// The named tokens of the settings/list type scale. Each resolves to a
    /// `ScaledSystemFont`; apply with `View.settingFont(_:)`.
    enum SettingFont {
        case title       // row / card titles
        case subtitle    // row & card supporting text
        case header      // uppercase section labels
        case segment     // segmented-control pills
        case value       // the makeable % readout
        case micro       // the zone (makeable/tight/miss) labels
        case icon        // a row's SF Symbol tile glyph
        case chevron     // a nav row's trailing chevron

        // `@MainActor`: `ScaledSystemFont` is main-actor-isolated (via its
        // `ViewModifier` conformance), so its initializer can only be called here.
        @MainActor var modifier: ScaledSystemFont {
            switch self {
            case .title:    ScaledSystemFont(size: 14,   relativeTo: .body,     weight: .medium)
            case .subtitle: ScaledSystemFont(size: 11.5, relativeTo: .caption)
            case .header:   ScaledSystemFont(size: 10.5, relativeTo: .caption2, weight: .semibold)
            case .segment:  ScaledSystemFont(size: 12.5, relativeTo: .footnote, weight: .semibold)
            case .value:    ScaledSystemFont(size: 16,   relativeTo: .body,     weight: .bold, design: .monospaced)
            case .micro:    ScaledSystemFont(size: 10.5, relativeTo: .caption2)
            case .icon:     ScaledSystemFont(size: 14,   relativeTo: .body,     weight: .medium)
            case .chevron:  ScaledSystemFont(size: 13,   relativeTo: .body,     weight: .semibold)
            }
        }
    }

    // MARK: - Dynamic color helpers

    static func dyn(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light) })
    }

    static func dynA(_ light: UInt32, _ la: CGFloat, _ dark: UInt32, _ da: CGFloat) -> Color {
        Color(uiColor: UIColor {
            $0.userInterfaceStyle == .dark ? UIColor(hex: dark, alpha: da) : UIColor(hex: light, alpha: la)
        })
    }
}

extension View {
    /// Apply a semantic settings/list font token that scales with Dynamic Type (#58).
    @MainActor func settingFont(_ token: Theme.SettingFont) -> some View {
        modifier(token.modifier)
    }
}

// MARK: - Verdict → theme

public extension Verdict {
    /// The pill / node / ring colour for this verdict (design § verdict system).
    var color: Color {
        switch self {
        case .feasible:   return Theme.go
        case .tight:      return Theme.tight
        case .infeasible: return Theme.miss
        case .unknown:    return Theme.nodata
        case .pending:    return Theme.ink3
        }
    }

    var softColor: Color {
        switch self {
        case .feasible:   return Theme.goSoft
        case .tight:      return Theme.tightSoft
        case .infeasible: return Theme.missSoft
        case .unknown:    return Theme.nodataSoft
        case .pending:    return Theme.panel2
        }
    }

    /// Short human label for a pill.
    var label: String {
        switch self {
        case .feasible:   return "Comfortable"
        case .tight:      return "Tight"
        case .infeasible: return "Won't make it"
        case .unknown:    return "Unknown"
        case .pending:    return "Checking…"
        }
    }

    var iconName: String {
        switch self {
        case .feasible:   return "checkmark"
        case .tight:      return "exclamationmark.triangle.fill"
        case .infeasible: return "xmark"
        case .unknown:    return "questionmark"
        case .pending:    return "ellipsis"
        }
    }
}

extension Color {
    /// A fixed (non-dynamic) colour from a hex literal — for the AR view's camera
    /// overlay, which is always dark regardless of app theme.
    init(hex: UInt32, alpha: Double = 1) {
        self.init(uiColor: UIColor(hex: hex, alpha: alpha))
    }
}

extension UIColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1) {
        self.init(
            red:   CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue:  CGFloat(hex & 0xFF) / 255,
            alpha: alpha
        )
    }
}
