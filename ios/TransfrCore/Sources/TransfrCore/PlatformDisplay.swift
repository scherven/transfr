import Foundation

/// How to render one platform end (a leg's departure/arrival, or a transfer's
/// arrival/departure) honestly from its LIVE and PLANNED values.
///
/// `live` is the realtime/current platform (MOTIS `track`), `planned` the
/// timetable's scheduled platform (`scheduledTrack`); `actual` is the
/// coordinate-recovered real sign (an ORTHOGONAL data-quality correction — see
/// `Transfer.arrivalPlatformActual`). This is the exact mirror of
/// `api/transfers.py:platform_display`, the way `Verdict` mirrors `classify`; the
/// two must stay in lockstep.
///
/// It NEVER invents a number: every associated value it carries came from the
/// feed. `actual` relabels the *live* platform for display but is deliberately not
/// used to decide whether a change happened (that compares the raw live/planned
/// codes), so a station that merely renumbers a platform (Köln "89" → "7") is not
/// mistaken for a platform change.
public enum PlatformDisplay: Equatable, Sendable {
    /// Neither live nor planned present → render nothing (the FR/IT/ES empty state).
    case none
    /// Live present, planned absent → confirmed; show it with no qualifier.
    case confirmed(String)
    /// Planned present and (live absent or live == planned) → the schedule's guess;
    /// show it with a subtle "planned · may change" qualifier.
    case planned(String)
    /// Live present and live != planned → a platform CHANGE; show the current
    /// platform and indicate it changed from the planned one. The high-value signal.
    case changed(current: String, from: String)

    /// Build the decision from a platform end's raw values. Empty strings count as
    /// absent (the feed sometimes emits `""` for "no platform").
    public static func make(live rawLive: String?, planned rawPlanned: String?,
                            actual rawActual: String? = nil) -> PlatformDisplay {
        let live = nonEmpty(rawLive)
        let planned = nonEmpty(rawPlanned)
        let actual = nonEmpty(rawActual)
        let shownLive = actual ?? live   // the real sign relabels the live platform

        if live == nil && planned == nil { return .none }
        if let l = live, let p = planned, l != p {
            return .changed(current: shownLive ?? l, from: p)
        }
        if let p = planned {              // live == nil, or live == planned
            return .planned(shownLive ?? p)
        }
        return .confirmed(shownLive ?? live!)   // planned == nil, live present
    }

    private static func nonEmpty(_ s: String?) -> String? {
        guard let s, !s.isEmpty else { return nil }
        return s
    }

    /// The platform number to render in the main chip, or nil for `.none` (so the
    /// caller can render an honest empty state rather than a placeholder).
    public var shownNumber: String? {
        switch self {
        case .none: return nil
        case .confirmed(let s), .planned(let s): return s
        case .changed(let current, _): return current
        }
    }

    /// True for a platform change — the signal to render prominently.
    public var isChange: Bool { if case .changed = self { return true }; return false }

    /// True when the platform is only the schedule's guess (`planned` state). The UI
    /// does not currently caption these — MOTIS echoes the scheduled track into the
    /// live one, so "planned" can't be told from "confirmed" (only a change is shown);
    /// kept for the classification tests and a future IRIS-backed planned-vs-actual split.
    public var isPlannedGuess: Bool { if case .planned = self { return true }; return false }
}
