import Foundation
import TransfrCore

/// The one seam that makes the whole UI **API-agnostic**. Every screen talks to a
/// `JourneyRepository`, never to `TransfrClient` directly, so the app runs today
/// against bundled sample data (`SampleRepository`) and flips to the real service
/// (`LiveRepository`) with a one-line change once `api/` is ready — no view edits.
///
/// It also drops a natural place for the offline cache to live later (DESIGN.md
/// §13.9): a `CachingRepository` decorator can wrap `LiveRepository` and persist
/// each planned journey + its prefetched walks, and nothing upstream changes.
public protocol JourneyRepository: Sendable {
    /// Plan a trip. `when` is the desired departure; nil means "now". `assess:
    /// false` returns the itineraries instantly with `pending` transfers, to be
    /// filled in via `assess(_:)` — the progressive load.
    func journeys(from: String, to: String, when: Date?, assess: Bool) async throws -> JourneysResponse

    /// Assess a batch of changes of train, returning the real transfers. The
    /// client fires these per-interchange, concurrently, to stream a journey's
    /// verdicts in behind a fast `journeys(assess: false)`.
    func assess(_ interchanges: [AssessInterchange]) async throws -> [Transfer]

    /// Station autocomplete. (The app also bundles `stations.csv` for instant
    /// offline suggestions; this is the online-refresh path — DESIGN.md §13.2.)
    func stations(query: String) async throws -> [StationSuggestion]

    /// The platforms (and the `relationId` a subsequent `walk(...)` uses) at the
    /// station nearest a coordinate. Powers the walk-only door: the platform
    /// pickers adapt to the entered station.
    func platforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse

    /// Every platform's walk FROM one source platform at the station nearest a
    /// coordinate — the "full station walk" Advanced tool (§6.10). One pathfind per
    /// platform, sorted nearest-first.
    func stationWalk(lat: Double, lon: Double, fromPlatform: String, stepFree: Bool) async throws -> StationWalkResponse

    /// A single station's platform-connectivity breakdown (connected / stitchable
    /// / island), for the Map-health tool's per-station query.
    func stationHealth(lat: Double, lon: Double) async throws -> StationHealthResponse

    /// One transfer's drawable walk geometry, keyed by the triple a `Transfer`
    /// already carries. May be `ok == false` when no geometry exists yet.
    func walk(for key: WalkKey) async throws -> WalkResult
}

public enum RepositoryError: Error, LocalizedError, Sendable {
    case notAvailable(String)

    public var errorDescription: String? {
        switch self {
        case .notAvailable(let what): return "\(what) isn't available yet."
        }
    }
}

// MARK: - Live: the real FastAPI service

/// Thin adapter over `TransfrCore.TransfrClient`. Converts the UI's `Date` to the
/// ISO-8601 string the `/journeys` endpoint expects and forwards the rest.
public struct LiveRepository: JourneyRepository {
    public var client: TransfrClient

    public init(baseURL: URL, apiKey: String? = nil, transport: Transport = URLSession.shared) {
        self.client = TransfrClient(baseURL: baseURL, transport: transport, apiKey: apiKey)
    }

    public func journeys(from: String, to: String, when: Date?, assess: Bool) async throws -> JourneysResponse {
        let iso = when.map { ISO8601DateFormatter.transfr.string(from: $0) }
        return try await client.journeys(from: from, to: to, when: iso, assess: assess)
    }

    public func assess(_ interchanges: [AssessInterchange]) async throws -> [Transfer] {
        try await client.assess(interchanges).transfers
    }

    public func stations(query: String) async throws -> [StationSuggestion] {
        try await client.stations(query: query)
    }

    public func platforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse {
        try await client.stationPlatforms(lat: lat, lon: lon)
    }

    public func stationWalk(lat: Double, lon: Double, fromPlatform: String, stepFree: Bool) async throws -> StationWalkResponse {
        try await client.stationWalk(lat: lat, lon: lon, fromPlatform: fromPlatform, stepFree: stepFree)
    }

    public func stationHealth(lat: Double, lon: Double) async throws -> StationHealthResponse {
        try await client.stationHealth(lat: lat, lon: lon)
    }

    public func walk(for key: WalkKey) async throws -> WalkResult {
        try await client.walk(relationId: key.relationId, from: key.fromPlatform,
                              to: key.toPlatform, stepFree: key.stepFree)
    }
}

extension ISO8601DateFormatter {
    /// Local wall-clock ISO string (no forced Z), matching what the prototype and
    /// the `/journeys` `time` param treat as a departure time.
    nonisolated(unsafe) static let transfr: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        f.timeZone = .current
        return f
    }()
}
