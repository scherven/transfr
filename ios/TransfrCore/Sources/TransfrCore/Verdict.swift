import Foundation

/// The four transfer outcomes, mirroring the constants in `api/transfers.py`.
/// `unknown` carries the server's reason string (e.g. `no_platform_data`,
/// `platform_not_found`, `cross_station`) so the UI can be honest about *why*
/// a transfer couldn't be assessed rather than faking a verdict (DESIGN.md §7.5).
public enum Verdict: Equatable, Sendable {
    case feasible
    case tight
    case infeasible
    case unknown(String?)

    /// Build from the raw `verdict` string + optional `reason` as they arrive
    /// on the wire. An unrecognised verdict is treated as `unknown` (fail soft),
    /// matching `rollup_verdict`'s default in `api/pipeline.py`.
    public init(raw: String, reason: String? = nil) {
        switch raw {
        case "feasible":   self = .feasible
        case "tight":      self = .tight
        case "infeasible": self = .infeasible
        default:           self = .unknown(reason)
        }
    }

    /// Lower rank = worse. Must match `_VERDICT_RANK` in `api/pipeline.py`:
    /// a definite infeasible dominates an unknown (a broken leg breaks the trip
    /// regardless), and unknown dominates tight/feasible (we can't promise a
    /// trip with an unassessable change).
    public var rank: Int {
        switch self {
        case .infeasible: return 0
        case .unknown:    return 1
        case .tight:      return 2
        case .feasible:   return 3
        }
    }

    /// The wire string (without the reason), for round-tripping / analytics.
    public var raw: String {
        switch self {
        case .feasible:   return "feasible"
        case .tight:      return "tight"
        case .infeasible: return "infeasible"
        case .unknown:    return "unknown"
        }
    }
}

public extension Sequence where Element == Verdict {
    /// Worst-wins rollup — the port of `api/pipeline.py:rollup_verdict`. A journey
    /// with no transfers (direct) is `feasible`; otherwise the journey takes its
    /// worst transfer's verdict. This is the §7.1 "makeable only if EVERY transfer
    /// is makeable" rule the whole product hangs on.
    func rolledUp() -> Verdict {
        self.min(by: { $0.rank < $1.rank }) ?? .feasible
    }
}
