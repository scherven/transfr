import XCTest
import TransfrCore
@testable import TransfrUI

/// The offline decorator (#37): `CachingRepository` writes each successful plan +
/// walk through to disk, and serves the cached copy when the inner repo throws
/// (offline / server error). Drives the decorator with a togglable stub inner repo
/// and a `JourneyCache` pointed at a temp dir, and pins the three behaviours the
/// seam was designed for — cache write-through, cache-hit-on-offline, and a miss
/// that rethrows — for both cached kinds (planned journeys + walk geometry).
final class CachingRepositoryTests: XCTestCase {
    private var root: URL!
    private var cache: JourneyCache!

    override func setUp() {
        super.setUp()
        root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("CachingRepositoryTests-\(UUID().uuidString)", isDirectory: true)
        cache = JourneyCache(root: root)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
        root = nil; cache = nil
        super.tearDown()
    }

    /// A stub inner repo: returns a fixed plan + walk while `online`; once flipped
    /// offline every fetch throws `URLError`, the real network failure the cache must
    /// ride out. `walkOK` lets a test make the walk come back `ok == false` (a
    /// no-geometry reply) to prove such a reply doesn't clobber a cached good walk.
    actor StubInner: JourneyRepository {
        private var online = true
        private var walkOK = true
        func goOffline() { online = false }
        func setWalkOK(_ v: Bool) { walkOK = v }

        /// One direct journey (no transfers). `JourneysResponse` has no public init,
        /// so it's built the way the real path builds it — decoded from the wire JSON
        /// through the shared coder.
        static let planJSON = """
        {"origin":{"name":"Hamburg Hbf"},"destination":{"name":"Stuttgart Hbf"},
         "departure_time":"2026-07-18T09:00:00Z",
         "journeys":[{"id":"j1","num_changes":0,"verdict":"feasible","transfers":[],"legs":[
            {"mode":"train","train_name":"ICE","cancelled":false,
             "origin":{"name":"Hamburg Hbf","latitude":53.55,"longitude":10.0},
             "destination":{"name":"Stuttgart Hbf","latitude":48.78,"longitude":9.18},
             "departure":"2026-07-18T09:00:00Z","arrival":"2026-07-18T11:00:00Z"}]}]}
        """

        func journeys(from: String, to: String, when: Date?, assess: Bool,
                      noElevators: Bool) async throws -> JourneysResponse {
            guard online else { throw URLError(.notConnectedToInternet) }
            return try TransfrJSON.decode(JourneysResponse.self, from: Data(Self.planJSON.utf8))
        }

        func walk(for key: WalkKey) async throws -> WalkResult {
            guard online else { throw URLError(.notConnectedToInternet) }
            return WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                              toPlatform: key.toPlatform, stepFree: key.stepFree,
                              ok: walkOK, reason: walkOK ? nil : "no_geometry")
        }

        // Pass-throughs (not exercised here).
        func assess(_ i: [AssessInterchange], noElevators: Bool) async throws -> [Transfer] { [] }
        func stations(query: String) async throws -> [StationSuggestion] { [] }
        func platforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse {
            throw RepositoryError.notAvailable("platforms")
        }
        func stationWalk(lat: Double, lon: Double, fromPlatform: String, stepFree: Bool) async throws -> StationWalkResponse {
            throw RepositoryError.notAvailable("stationWalk")
        }
        func facilities(lat: Double, lon: Double, category: String) async throws -> FacilitiesResponse {
            throw RepositoryError.notAvailable("facilities")
        }
        func stationHealth(lat: Double, lon: Double) async throws -> StationHealthResponse {
            throw RepositoryError.notAvailable("stationHealth")
        }
    }

    // MARK: - Planned journeys

    /// Write-through: a successful plan is persisted on fetch, independent of the
    /// inner repo (the file is there straight away, before any offline read).
    func testJourneyFetchWritesThrough() async throws {
        let repo = CachingRepository(wrapping: StubInner(), cache: cache)
        _ = try await repo.journeys(from: "Hamburg Hbf", to: "Stuttgart Hbf",
                                    when: nil, assess: false, noElevators: false)

        let key = CachingRepository.journeyKey(from: "Hamburg Hbf", to: "Stuttgart Hbf",
                                               when: nil, assess: false, noElevators: false)
        let persisted = cache.read(JourneysResponse.self,
                                   namespace: CachingRepository.journeysNamespace, key: key)
        XCTAssertEqual(persisted?.origin.name, "Hamburg Hbf",
                       "a successful plan is written through to disk on fetch")
        XCTAssertEqual(persisted?.journeys.count, 1)
    }

    /// Cache-hit-on-offline: after one online plan, the same query is served from the
    /// cache when the inner repo goes offline — the trip reopens with no signal.
    func testJourneyServedFromCacheWhenOffline() async throws {
        let inner = StubInner()
        let repo = CachingRepository(wrapping: inner, cache: cache)

        _ = try await repo.journeys(from: "Hamburg Hbf", to: "Stuttgart Hbf",
                                    when: nil, assess: false, noElevators: false)
        await inner.goOffline()

        let cached = try await repo.journeys(from: "Hamburg Hbf", to: "Stuttgart Hbf",
                                             when: nil, assess: false, noElevators: false)
        XCTAssertEqual(cached.origin.name, "Hamburg Hbf")
        XCTAssertEqual(cached.destination.name, "Stuttgart Hbf",
                       "a trip planned with signal reopens offline from the cache")
    }

    /// Miss: a query that was never cached rethrows when the inner repo is offline —
    /// the decorator invents nothing, so `TripModel` surfaces the real failure.
    func testJourneyMissRethrowsWhenOfflineWithNoCache() async throws {
        let inner = StubInner(); await inner.goOffline()
        let repo = CachingRepository(wrapping: inner, cache: cache)
        do {
            _ = try await repo.journeys(from: "Nowhere", to: "Elsewhere",
                                        when: nil, assess: false, noElevators: false)
            XCTFail("an uncached query must rethrow when the inner repo is offline")
        } catch {
            XCTAssertTrue(error is URLError, "the inner error propagates unchanged on a miss")
        }
    }

    /// A different query is its own cache entry: caching Hamburg→Stuttgart does not
    /// answer an unrelated route offline.
    func testDifferentQueryIsADistinctCacheEntry() async throws {
        let inner = StubInner()
        let repo = CachingRepository(wrapping: inner, cache: cache)
        _ = try await repo.journeys(from: "Hamburg Hbf", to: "Stuttgart Hbf",
                                    when: nil, assess: false, noElevators: false)
        await inner.goOffline()
        do {
            _ = try await repo.journeys(from: "Berlin Hbf", to: "Basel SBB",
                                        when: nil, assess: false, noElevators: false)
            XCTFail("a route that was never planned has no cache to serve offline")
        } catch {
            XCTAssertTrue(error is URLError)
        }
    }

    // MARK: - Walk geometry

    /// Write-through + cache-hit-on-offline for a walk: geometry fetched with signal
    /// renders offline.
    func testWalkWritesThroughThenServedFromCacheWhenOffline() async throws {
        let inner = StubInner()
        let repo = CachingRepository(wrapping: inner, cache: cache)
        let key = WalkKey(relationId: 5688517, fromPlatform: "1", toPlatform: "16")

        let live = try await repo.walk(for: key)
        XCTAssertTrue(live.ok)

        await inner.goOffline()
        let cached = try await repo.walk(for: key)
        XCTAssertTrue(cached.ok, "a walk fetched with signal renders offline")
        XCTAssertEqual(cached.relationId, 5688517)
        XCTAssertEqual(cached.fromPlatform, "1")
        XCTAssertEqual(cached.toPlatform, "16")
    }

    /// Miss: an uncached walk rethrows offline (so `TripModel.walk` falls back to its
    /// schematic, exactly as before the cache existed).
    func testWalkMissRethrowsWhenOfflineWithNoCache() async throws {
        let inner = StubInner(); await inner.goOffline()
        let repo = CachingRepository(wrapping: inner, cache: cache)
        do {
            _ = try await repo.walk(for: WalkKey(relationId: 1, fromPlatform: "1", toPlatform: "2"))
            XCTFail("an uncached walk must rethrow when offline")
        } catch {
            XCTAssertTrue(error is URLError)
        }
    }

    /// A `step_free` variant is a distinct walk (a different route), so it caches
    /// apart from the default and doesn't answer for it offline.
    func testStepFreeWalkIsADistinctCacheEntry() async throws {
        let inner = StubInner()
        let repo = CachingRepository(wrapping: inner, cache: cache)
        _ = try await repo.walk(for: WalkKey(relationId: 42, fromPlatform: "1", toPlatform: "2", stepFree: false))
        await inner.goOffline()
        do {
            _ = try await repo.walk(for: WalkKey(relationId: 42, fromPlatform: "1", toPlatform: "2", stepFree: true))
            XCTFail("the step-free variant is a different walk with its own cache key")
        } catch {
            XCTAssertTrue(error is URLError)
        }
    }

    /// An `ok == false` walk (no geometry) must not overwrite a previously-cached
    /// good walk — a transient no-geometry reply shouldn't evict the offline copy.
    func testFailedWalkDoesNotClobberCachedGeometry() async throws {
        let inner = StubInner()
        let repo = CachingRepository(wrapping: inner, cache: cache)
        let key = WalkKey(relationId: 7, fromPlatform: "3", toPlatform: "5")

        _ = try await repo.walk(for: key)          // caches a good (ok == true) walk
        await inner.setWalkOK(false)
        let degraded = try await repo.walk(for: key)
        XCTAssertFalse(degraded.ok, "the live reply passes through unchanged")

        // The good copy is still what serves offline — the ok == false reply didn't
        // overwrite it.
        await inner.goOffline()
        let cached = try await repo.walk(for: key)
        XCTAssertTrue(cached.ok, "a no-geometry reply must not evict the cached good walk")
    }
}
