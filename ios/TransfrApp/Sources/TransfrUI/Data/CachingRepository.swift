import Foundation
import TransfrCore

/// Offline decorator over any `JourneyRepository` (DESIGN.md §13.9, #37). On a
/// successful fetch it writes the result **through** to an on-device JSON cache; when
/// the inner repo throws (offline, a server error, a timeout) it serves the last
/// cached copy for that exact query — so a trip planned with signal reopens in
/// airplane mode. Because every screen already talks to a `JourneyRepository` and
/// never to `TransfrClient` directly, wrapping `LiveRepository` in this changes
/// nothing upstream (the seam was designed for exactly this decorator).
///
/// Scope is deliberately the issue's two units — the **planned journeys**
/// (`journeys(...)`) and the **walk geometry** (`walk(for:)`, the self-contained
/// `viz_export` DESIGN §13.9 calls "the offline unit"). Everything else forwards
/// straight to the inner repo: station autocomplete already has a bundled offline
/// corpus (§13.2), and the station-question tools (facilities, health, full-station
/// walk) aren't part of "reopen a planned trip". `assess(...)` is a pass-through too
/// — its streamed verdicts are spliced in `TripModel`, not re-fetched through this
/// seam, so reopening offline restores the itinerary + geometry with transfer
/// verdicts re-streaming when back online (a `/assess` cache is a possible follow-up).
///
/// Policy (sensible defaults — see the PR discussion / FLAGS):
///  - **Freshness:** live always wins; the cache is read *only* when the inner call
///    fails. Staleness is thus bounded — you get fresh data whenever online.
///  - **"Offline":** *any* inner error triggers the fallback, not just `URLError`.
///    It's safe because a fallback only succeeds when a cached copy for that exact
///    key exists, and one exists only if that query previously succeeded; a genuinely
///    new or bad query has nothing cached and rethrows.
///  - **Eviction:** none (last-write-wins per key). Entries are small (KB–low-MB);
///    an LRU / size cap is a future refinement.
public struct CachingRepository: JourneyRepository {
    /// The decorated repository (`LiveRepository` in the app; a stub in tests).
    public let inner: any JourneyRepository
    private let cache: JourneyCache

    /// On-disk namespaces (subdirectories) keeping the two cached kinds apart.
    static let journeysNamespace = "journeys"
    static let walksNamespace = "walks"

    /// Wrap `inner`, persisting to `cache`. `cache` defaults to the on-device
    /// location (`Caches/TransfrJourneyCache`); a test injects a temp-dir cache.
    public init(wrapping inner: any JourneyRepository, cache: JourneyCache? = nil) {
        self.inner = inner
        self.cache = cache ?? Self.defaultCache()
    }

    /// `<Caches>/TransfrJourneyCache`. The **Caches** directory (not Application
    /// Support) because this is a genuine, re-fetchable cache: the OS may purge it
    /// under storage pressure, which degrades gracefully to re-planning when back
    /// online. Falls back to the temp dir if Caches can't be resolved, so
    /// construction never fails.
    static func defaultCache() -> JourneyCache {
        let base = (try? FileManager.default.url(for: .cachesDirectory, in: .userDomainMask,
                                                 appropriateFor: nil, create: true))
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return JourneyCache(root: base.appendingPathComponent("TransfrJourneyCache", isDirectory: true))
    }

    // MARK: - Cached: planned journeys + walk geometry

    public func journeys(from: String, to: String, when: Date?, assess: Bool,
                         noElevators: Bool) async throws -> JourneysResponse {
        let key = Self.journeyKey(from: from, to: to, when: when, assess: assess, noElevators: noElevators)
        do {
            let fresh = try await inner.journeys(from: from, to: to, when: when,
                                                 assess: assess, noElevators: noElevators)
            cache.write(fresh, namespace: Self.journeysNamespace, key: key)
            return fresh
        } catch {
            if let cached = cache.read(JourneysResponse.self, namespace: Self.journeysNamespace, key: key) {
                return cached
            }
            throw error
        }
    }

    public func walk(for key: WalkKey) async throws -> WalkResult {
        let cacheKey = Self.walkKey(key)
        do {
            let fresh = try await inner.walk(for: key)
            // Only persist usable geometry: an `ok == false` reply (no export) carries
            // nothing to draw offline and must not overwrite a previously-cached good
            // walk, so a transient bad reply can't evict the offline copy.
            if fresh.ok { cache.write(fresh, namespace: Self.walksNamespace, key: cacheKey) }
            return fresh
        } catch {
            if let cached = cache.read(WalkResult.self, namespace: Self.walksNamespace, key: cacheKey) {
                return cached
            }
            throw error
        }
    }

    // MARK: - Pass-through (not part of "reopen a planned trip")

    public func assess(_ interchanges: [AssessInterchange], noElevators: Bool) async throws -> [Transfer] {
        try await inner.assess(interchanges, noElevators: noElevators)
    }

    public func stations(query: String) async throws -> [StationSuggestion] {
        try await inner.stations(query: query)
    }

    public func platforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse {
        try await inner.platforms(lat: lat, lon: lon)
    }

    public func stationWalk(lat: Double, lon: Double, fromPlatform: String, stepFree: Bool) async throws -> StationWalkResponse {
        try await inner.stationWalk(lat: lat, lon: lon, fromPlatform: fromPlatform, stepFree: stepFree)
    }

    public func facilities(lat: Double, lon: Double, category: String) async throws -> FacilitiesResponse {
        try await inner.facilities(lat: lat, lon: lon, category: category)
    }

    public func facilityMap(lat: Double, lon: Double, category: String) async throws -> FacilityMapResponse {
        // Forward explicitly so the live service's map is reached; without this the
        // protocol's "not available" default would shadow the inner implementation.
        try await inner.facilityMap(lat: lat, lon: lon, category: category)
    }

    public func stationHealth(lat: Double, lon: Double) async throws -> StationHealthResponse {
        try await inner.stationHealth(lat: lat, lon: lon)
    }

    // MARK: - Cache keys

    /// Stable identity for a plan query — its endpoints, departure, assessment mode
    /// and routing profile. A different route, time or profile caches apart; the same
    /// query overwrites (last plan wins). `when` uses the wire ISO string so the key
    /// matches what was actually queried; nil ("now") is its own key. `\u{1}` (a
    /// control char that can't appear in a station name) separates the fields, like
    /// `RecentSearch.id`.
    static func journeyKey(from: String, to: String, when: Date?, assess: Bool, noElevators: Bool) -> String {
        let w = when.map { ISO8601DateFormatter.transfr.string(from: $0) } ?? "now"
        return "\(from)\u{1}\(to)\u{1}\(w)\u{1}\(assess)\u{1}\(noElevators)"
    }

    /// Stable identity for a walk — the exact `WalkKey` (relation, both platforms,
    /// step-free, all-platforms, and any focus POI). JSON with sorted keys, so it's
    /// deterministic and auto-covers every field (a new `WalkKey` field just refines
    /// the key). Falls back to a joined string if encoding ever fails.
    static func walkKey(_ key: WalkKey) -> String {
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys]
        if let data = try? enc.encode(key), let s = String(data: data, encoding: .utf8) { return s }
        return "\(key.relationId)\u{1}\(key.fromPlatform)\u{1}\(key.toPlatform)\u{1}\(key.stepFree)\u{1}\(key.allPlatforms)"
    }
}
