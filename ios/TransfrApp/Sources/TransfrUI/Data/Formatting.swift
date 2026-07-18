import Foundation
import TransfrCore

/// Display helpers shared across screens. The wire carries ISO-8601 strings and
/// seconds; the UI wants "08:34", "5h 15m", "+3 min". Kept here so every screen
/// formats identically (and with tabular figures via `.monospacedDigit()` at the
/// view layer).
enum Fmt {
    nonisolated(unsafe) private static let hhmm: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.dateFormat = "HH:mm"
        return f
    }()

    private static let weekday: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.dateFormat = "EEE"
        return f
    }()

    private static let dayMonth: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.dateFormat = "d MMM"
        return f
    }()

    /// "Today" / "Yesterday" / a weekday inside the last week / "3 Jul" — the
    /// relative day a past search was last run (#38). The weekday form stops at 6
    /// days back, since at 7 the name would repeat today's and read as this week.
    /// `now` is injectable so the boundary is testable.
    static func relativeDay(_ date: Date, now: Date = Date()) -> String {
        let cal = Calendar.current
        if cal.isDateInToday(date) { return "Today" }
        if cal.isDateInYesterday(date) { return "Yesterday" }
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: date),
                                      to: cal.startOfDay(for: now)).day ?? 0
        return (0...6).contains(days) ? weekday.string(from: date) : dayMonth.string(from: date)
    }

    /// Parse an ISO-8601 string (with or without a trailing Z / offset).
    static func date(_ iso: String?) -> Date? {
        guard let iso else { return nil }
        if let d = ISO8601DateFormatter.transfr.date(from: iso) { return d }
        // Fall back for bare "yyyy-MM-dd'T'HH:mm:ss" without an offset.
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f.date(from: iso)
    }

    /// "08:34" from an ISO string, or "--:--" if unparseable.
    static func time(_ iso: String?) -> String {
        guard let d = date(iso) else { return "--:--" }
        return hhmm.string(from: d)
    }

    /// "5h 15m" / "56m" from a second count.
    static func duration(_ seconds: Int?) -> String {
        guard let s = seconds, s > 0 else { return "—" }
        let h = s / 3600, m = (s % 3600) / 60
        return h > 0 ? "\(h)h \(String(format: "%02d", m))m" : "\(m)m"
    }

    /// Walk time in the design's voice: "64 s" under a minute, "2m 30s" over.
    static func walkTime(_ seconds: Double?) -> String {
        guard let s = seconds, s > 0 else { return "—" }
        let t = Int(s.rounded())
        return t < 90 ? "\(t) s" : "\(t / 60)m \(String(format: "%02d", t % 60))s"
    }

    /// Distance in the user's units — "120 m" metric, "394 ft" imperial. Walk
    /// distances here are platform-to-platform (short), so feet reads better than
    /// miles across the whole range; the caller passes `imperial` from Settings.
    static func distance(_ m: Double?, imperial: Bool = false) -> String {
        guard let m, m > 0 else { return "—" }
        if imperial { return "\(Int((m * 3.28084).rounded())) ft" }
        return "\(Int(m.rounded())) m"
    }

    /// A signed delay chip, e.g. "+3 min" / "on time".
    static func delay(_ seconds: Int?) -> String {
        guard let s = seconds, s != 0 else { return "on time" }
        let m = (abs(s) + 30) / 60
        return "\(s > 0 ? "+" : "−")\(max(m, 1)) min"
    }

    /// A coarse "~40s" / "~3 min" used for the boarding time-saved estimate. The
    /// value is an upper bound (worst-end penalty), so it's rounded coarsely and
    /// always read with an "up to" in the copy — precision would overstate it.
    static func approxSaved(_ seconds: Double?) -> String {
        guard let s = seconds, s > 0 else { return "—" }
        if s < 75 { return "~\(Int((s / 5).rounded()) * 5) s" }   // nearest 5s
        return "~\(Int((s / 60).rounded())) min"
    }
}

extension Transfer {
    /// Layover minus walk, i.e. slack the traveller keeps. Nil if either is unknown.
    /// `paceFactor` scales the walk by the user's walking-pace preference (#36) so
    /// the spare shown alongside a paced walk time stays consistent with it; the
    /// default (1) is the engine's pace. DISPLAY-ONLY — never re-verdicts.
    func spareSeconds(paceFactor: Double = 1) -> Double? {
        guard let layover = layoverS, let walk = pacedWalkTimeS(paceFactor) else { return nil }
        return layover - walk
    }

    /// The design's "level Δ" only shows when we know it; here we don't derive it
    /// from geometry (that lives in `viz_export`), so callers show it opportunistically.
    var hasGeometry: Bool { relationId != nil && arrivalPlatform != nil && departurePlatform != nil }
}

extension Journey {
    var departureISO: String? { legs.first?.departure }
    var arrivalISO: String? { legs.last?.arrival }

    var originName: String { legs.first?.origin.name ?? "—" }
    var destinationName: String { legs.last?.destination.name ?? "—" }
}
