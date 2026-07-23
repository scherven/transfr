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

    /// "Today" / "Tomorrow" / a weekday inside the coming week / "3 Jul" — the
    /// relative day of a date ahead of `now`, e.g. the departure a search was run
    /// for. The mirror of `relativeDay`, which reads *backwards* and would label a
    /// departure three days out as the weekday of a day that has already passed.
    /// The weekday form stops at 6 days out for the same reason it does going back:
    /// at 7 the name repeats today's. A departure can also sit in the past (the
    /// picker allows it), so anything before today defers to `relativeDay`.
    static func relativeFutureDay(_ date: Date, now: Date = Date()) -> String {
        let cal = Calendar.current
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: now),
                                      to: cal.startOfDay(for: date)).day ?? 0
        if days < 0 { return relativeDay(date, now: now) }
        if days == 0 { return "Today" }
        if days == 1 { return "Tomorrow" }
        return days <= 6 ? weekday.string(from: date) : dayMonth.string(from: date)
    }

    /// "Today" / "Yesterday" / a weekday inside the last week / "3 Jul" — the
    /// relative day a past search was last run (#38). The weekday form stops at 6
    /// days back, since at 7 the name would repeat today's and read as this week.
    /// `now` is injectable so the boundary is testable.
    static func relativeDay(_ date: Date, now: Date = Date()) -> String {
        let cal = Calendar.current
        // Resolve "Today"/"Yesterday" against the injected `now`, NOT the system
        // clock. `isDateInToday`/`isDateInYesterday` ignore `now`, which made the
        // boundary untestable and returned the wrong label for any caller passing a
        // `now` other than the real current date.
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: date),
                                      to: cal.startOfDay(for: now)).day ?? 0
        if days == 0 { return "Today" }
        if days == 1 { return "Yesterday" }
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
    /// Layover minus walk, i.e. slack the traveller keeps. Nil unless the walk was
    /// actually measured.
    ///
    /// A missing walk isn't only `nil` on the wire: the server also returns
    /// `walk_time_s: 0.0` for a walk it couldn't measure (Utrecht Centraal 5→7, two
    /// platforms ~13 m apart). `Fmt.walkTime`/`Fmt.distance` already read that as the
    /// unknown they show as "—", so guarding on nil alone computed `layover − 0` and
    /// printed a confident "Spare 2m · Comfortable" beside a walk the same card had
    /// just said it couldn't measure. Same `> 0` rule as the formatters, so the two
    /// can't disagree.
    var spareSeconds: Double? {
        guard let layover = layoverS, let walk = walkTimeS, walk > 0 else { return nil }
        return layover - walk
    }

    /// The platform to show at each end of the change: the recovered public sign
    /// when the feed labels the platform with an internal code the station signs
    /// don't use (Köln Hbf "89" → "7"), else the feed's own label.
    ///
    /// Every screen must read these rather than the raw `arrivalPlatform` /
    /// `departurePlatform`, or the same change of train shows one number on the
    /// timeline and a different one on the card beside it. The raw fields stay the
    /// routing key (that's what `WalkKey` forwards to the server); these are the
    /// display half.
    var shownArrivalPlatform: String? { arrivalPlatformActual ?? arrivalPlatform }
    var shownDeparturePlatform: String? { departurePlatformActual ?? departurePlatform }

    /// The design's "level Δ" only shows when we know it; here we don't derive it
    /// from geometry (that lives in `viz_export`), so callers show it opportunistically.
    var hasGeometry: Bool { relationId != nil && arrivalPlatform != nil && departurePlatform != nil }
}

extension Leg {
    /// The platform to show for this leg's own ends — the `Transfer` rule applied to
    /// a leg (see `Transfer.shownArrivalPlatform`), for the origin/terminal rows of
    /// the timeline, which read the leg rather than a change of train.
    var shownDeparturePlatform: String? { departurePlatformActual ?? departurePlatform }
    var shownArrivalPlatform: String? { arrivalPlatformActual ?? arrivalPlatform }
}

extension Collection where Element == Transfer {
    /// The one-line, worst-wins summary of these transfers — the results card's meta
    /// tag and the transition screen's subtitle read this same sentence, so a journey
    /// can never report "all clear" on one screen while carrying a missed change on
    /// the other.
    ///
    /// Order mirrors `Verdict.rank`: anything still streaming reads as checking, then
    /// a definite miss, then an unassessable change, then tight — "all clear" only
    /// once every transfer is in and none of them is any of those.
    var verdictSummary: String {
        let kinds = map(\.verdictKind)
        if kinds.contains(where: \.isPending) { return "checking…" }
        let missed = kinds.filter { $0 == .infeasible }.count
        if missed > 0 { return "\(missed) won't make it" }
        let unknowns = kinds.filter { if case .unknown = $0 { return true }; return false }.count
        if unknowns > 0 { return "\(unknowns) unknown" }
        let tights = kinds.filter { $0 == .tight }.count
        if tights > 0 { return "\(tights) tight" }
        return "all clear"
    }
}

extension Journey {
    var departureISO: String? { legs.first?.departure }
    var arrivalISO: String? { legs.last?.arrival }

    /// At least one change of train is still waiting on its verdict (`/assess` is
    /// mid-stream). Anything that reads as a claim about the whole journey has to
    /// wait for this to go false.
    var hasPendingTransfers: Bool { transfers.contains { $0.verdictKind.isPending } }

    var originName: String { legs.first?.origin.name ?? "—" }
    var destinationName: String { legs.last?.destination.name ?? "—" }
}
