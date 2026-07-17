import Foundation

// MARK: - Recent searches (persisted history, client-side)

/// One past search the user can tap to re-run — an `origin → destination` pair
/// plus when it was last run. A pure, `Codable` value type (like `WalkKey`): the
/// dedup / cap / ordering rules live here so they're unit-tested headlessly, while
/// the `@Observable` store that persists a list of these to `UserDefaults` lives in
/// the app layer (see `RecentSearchStore`). Departure time is deliberately *not*
/// part of a search's identity — you re-plan a route "now", not at a stale time —
/// so it isn't stored.
public struct RecentSearch: Codable, Hashable, Sendable, Identifiable {
    public var origin: String
    public var destination: String
    /// When this route was last searched — drives the "yesterday" / "Mon" label and
    /// (as the insert order) the newest-first sort.
    public var date: Date

    public init(origin: String, destination: String, date: Date = Date()) {
        self.origin = origin
        self.destination = destination
        self.date = date
    }

    /// Stable identity for `ForEach` and dedup: the normalized route. Two searches
    /// for the same `origin → destination` (ignoring case and surrounding spaces)
    /// share it, so re-running a route updates the existing row instead of adding a
    /// duplicate. Computed, so it never encodes.
    public var id: String { "\(Self.norm(origin))\u{1}\(Self.norm(destination))" }

    /// Same route as `other` — case- and whitespace-insensitive on both ends. The
    /// dedup key; direction matters (A→B ≠ B→A, they're different trips).
    public func sameRoute(as other: RecentSearch) -> Bool { id == other.id }

    private static func norm(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}

public extension RecentSearch {
    /// How many past searches to keep. Bounded so the history can't grow without
    /// limit; older entries fall off the end once it's reached.
    static let historyCap = 12
}

public extension Array where Element == RecentSearch {
    /// Return a new history with `search` recorded: any existing entry for the same
    /// route is dropped, `search` goes to the front (newest-first), and the result
    /// is capped at `cap` (oldest trimmed off the end). Pure — the caller persists
    /// the result. `cap <= 0` yields an empty history.
    func recordingSearch(_ search: RecentSearch, cap: Int = RecentSearch.historyCap) -> [RecentSearch] {
        guard cap > 0 else { return [] }
        var out = filter { !$0.sameRoute(as: search) }
        out.insert(search, at: 0)
        if out.count > cap { out.removeLast(out.count - cap) }
        return out
    }
}
