import SwiftUI
import Observation

/// The user's preferences (DESIGN.md §6.8 / §7.9), persisted to `UserDefaults`.
/// `@AppStorage` doesn't compose with `@Observable`, so each preference writes
/// itself straight through on `didSet` — the store is its own persistence layer,
/// so a change is durable the instant it happens (no `onDisappear`/`onChange`
/// plumbing to forget). `load()` seeds the initial values in `init()`.
///
/// These are real and durable; whether each one yet *affects routing* is tracked
/// in the repo-root `TODO.md` §6 (e.g. `stepFree` rides every walk request; the
/// makeable cut-off would re-verdict client-side). The theme override is fully
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
    public var stepFree = false { didSet { write(stepFree, "stepFree") } }
    public var pace: Pace = .normal { didSet { write(pace.rawValue, "pace") } }
    public var preferEscalators = true { didSet { write(preferEscalators, "preferEscalators") } }
    // Making the connection
    public var makeablePct = 70 { didSet { write(makeablePct, "makeablePct") } }
    public var bufferS = 60 { didSet { write(bufferS, "bufferS") } }
    // Appearance
    public var theme: ThemeMode = .system { didSet { write(theme.rawValue, "theme") } }
    public var units: Units = .metric { didSet { write(units.rawValue, "units") } }
    // On the move
    public var liveActivity = true { didSet { write(liveActivity, "liveActivity") } }
    public var autoARLeadS = 90 { didSet { write(autoARLeadS, "autoARLeadS") } }   // 0 = off

    public init() { load() }

    // MARK: - Persistence (write-through on didSet)

    private let d = UserDefaults.standard
    /// Suppresses the `didSet` write-back while `load()` seeds values from disk,
    /// so launch doesn't echo each just-read value straight back to `UserDefaults`.
    private var isLoading = false

    /// Persist one preference immediately. No-op while `load()` is running.
    private func write(_ value: Any, _ key: String) {
        guard !isLoading else { return }
        d.set(value, forKey: key)
    }

    private func load() {
        isLoading = true
        defer { isLoading = false }
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

    /// Worked example for the makeable slider: how much walking is allowed on an
    /// 8-minute (480 s) layover before the connection is flagged tight.
    public var makeableExample: String {
        let s = 480 * makeablePct / 100
        return "\(s / 60):\(String(format: "%02d", s % 60))"
    }
}
