import SwiftUI
import TransfrCore

/// The design tokens from `design/prototype.html`, ported 1:1. Each token is a
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

    public static let radius: CGFloat = 22
    public static let mono  = Font.system(.body, design: .monospaced)

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

// MARK: - Verdict → theme

public extension Verdict {
    /// The pill / node / ring colour for this verdict (design § verdict system).
    var color: Color {
        switch self {
        case .feasible:   return Theme.go
        case .tight:      return Theme.tight
        case .infeasible: return Theme.miss
        case .unknown:    return Theme.nodata
        }
    }

    var softColor: Color {
        switch self {
        case .feasible:   return Theme.goSoft
        case .tight:      return Theme.tightSoft
        case .infeasible: return Theme.missSoft
        case .unknown:    return Theme.nodataSoft
        }
    }

    /// Short human label for a pill.
    var label: String {
        switch self {
        case .feasible:   return "Comfortable"
        case .tight:      return "Tight"
        case .infeasible: return "Won't make it"
        case .unknown:    return "Unknown"
        }
    }

    var iconName: String {
        switch self {
        case .feasible:   return "checkmark"
        case .tight:      return "exclamationmark.triangle.fill"
        case .infeasible: return "xmark"
        case .unknown:    return "questionmark"
        }
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
