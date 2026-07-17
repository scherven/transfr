import Foundation
import Observation
import TransfrCore

/// Past searches, persisted for one-tap reuse (TODO.md §8, #38). The durable half
/// of the "Recent" section: `TripModel` records a route here every time a plan
/// succeeds, `InputView` reads `items` to offer them back.
///
/// Same shape as `SettingsStore` — an `@Observable` that *is* its own persistence
/// layer, writing straight through to `UserDefaults` so a change survives the next
/// launch with no `onDisappear` plumbing. The list is stored as one JSON blob
/// (Codable `[RecentSearch]`); the pure dedup / cap / ordering rules live in
/// `TransfrCore` (`Array.recordingSearch`) so they're unit-tested without a
/// simulator. `defaults` is injectable so a test can use an isolated suite.
///
/// A `CachingRepository` (#37) is a *separate, heavier* layer — it would persist
/// whole planned journeys + walk geometry for offline reopen. This only remembers
/// the query (origin → destination), which is independent and needs no server.
@MainActor
@Observable
public final class RecentSearchStore {
    /// Newest-first, deduped, capped. Read-only to callers; mutate via `record`/`clear`.
    public private(set) var items: [RecentSearch] = []

    /// Upper bound on remembered searches (older ones fall off the end).
    public let cap: Int

    private let d: UserDefaults
    private let key = "recentSearches"

    public init(cap: Int = RecentSearch.historyCap, defaults: UserDefaults = .standard) {
        self.cap = cap
        self.d = defaults
        load()
    }

    /// Remember a completed search. Blank/whitespace endpoints are ignored (nothing
    /// to reuse); a repeat route moves to the front with a fresh timestamp instead
    /// of duplicating. Persists immediately.
    public func record(origin: String, destination: String, date: Date = Date()) {
        let o = origin.trimmingCharacters(in: .whitespacesAndNewlines)
        let dst = destination.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !o.isEmpty, !dst.isEmpty else { return }
        items = items.recordingSearch(RecentSearch(origin: o, destination: dst, date: date), cap: cap)
        persist()
    }

    /// Forget all past searches (the "clear history" affordance).
    public func clear() {
        items = []
        persist()
    }

    // MARK: - Persistence (write-through, like SettingsStore)

    private func persist() {
        if let data = try? JSONEncoder().encode(items) { d.set(data, forKey: key) }
    }

    private func load() {
        guard let data = d.data(forKey: key),
              let stored = try? JSONDecoder().decode([RecentSearch].self, from: data) else { return }
        // Re-apply the cap on read so a smaller `cap` (or a shrunk build) trims an
        // over-long stored list rather than surfacing it. Clamped: `prefix` traps on
        // a negative length, and a non-positive cap means "keep nothing" — the same
        // rule `recordingSearch` applies on write.
        items = Array(stored.prefix(max(0, cap)))
    }
}
