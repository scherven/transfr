import SwiftUI
import Observation

/// The user's preferences (DESIGN.md §6.8 / §7.9), persisted via `@AppStorage`.
/// These are real and durable; whether each one yet *affects routing* is tracked
/// in `ios/SUI_TODO.md` (e.g. `stepFree` should ride on every walk request; the
/// makeable cut-off should re-verdict client-side). The theme override is fully
/// wired to `.preferredColorScheme`.
@MainActor
@Observable
public final class SettingsStore {
    public enum Pace: String, CaseIterable { case relaxed, normal, brisk
        public var label: String { rawValue.capitalized }
    }
    public enum Units: String, CaseIterable { case metric, imperial
        public var label: String { rawValue.capitalized }
    }
    public enum ThemeMode: String, CaseIterable { case system, light, dark
        public var label: String { rawValue.capitalized }
        public var colorScheme: ColorScheme? {
            switch self { case .system: nil; case .light: .light; case .dark: .dark }
        }
    }

    // Getting around
    public var stepFree = false
    public var pace: Pace = .normal
    public var preferEscalators = true
    // Making the connection
    public var makeablePct = 70
    public var bufferS = 60
    // Appearance
    public var theme: ThemeMode = .system
    public var units: Units = .metric
    // On the move
    public var liveActivity = true
    public var autoARLeadS = 90   // 0 = off

    public init() { load() }

    // MARK: - Persistence (@AppStorage-style, but on the observable)

    private let d = UserDefaults.standard

    private func load() {
        stepFree = d.bool(forKey: "stepFree")
        if let p = d.string(forKey: "pace").flatMap(Pace.init) { pace = p }
        preferEscalators = d.object(forKey: "preferEscalators") as? Bool ?? true
        makeablePct = d.object(forKey: "makeablePct") as? Int ?? 70
        bufferS = d.object(forKey: "bufferS") as? Int ?? 60
        if let t = d.string(forKey: "theme").flatMap(ThemeMode.init) { theme = t }
        if let u = d.string(forKey: "units").flatMap(Units.init) { units = u }
        liveActivity = d.object(forKey: "liveActivity") as? Bool ?? true
        autoARLeadS = d.object(forKey: "autoARLeadS") as? Int ?? 90
    }

    public func persist() {
        d.set(stepFree, forKey: "stepFree")
        d.set(pace.rawValue, forKey: "pace")
        d.set(preferEscalators, forKey: "preferEscalators")
        d.set(makeablePct, forKey: "makeablePct")
        d.set(bufferS, forKey: "bufferS")
        d.set(theme.rawValue, forKey: "theme")
        d.set(units.rawValue, forKey: "units")
        d.set(liveActivity, forKey: "liveActivity")
        d.set(autoARLeadS, forKey: "autoARLeadS")
    }

    /// Worked example for the makeable slider: how much walking is allowed on an
    /// 8-minute (480 s) layover before the connection is flagged tight.
    public var makeableExample: String {
        let s = 480 * makeablePct / 100
        return "\(s / 60):\(String(format: "%02d", s % 60))"
    }
}
