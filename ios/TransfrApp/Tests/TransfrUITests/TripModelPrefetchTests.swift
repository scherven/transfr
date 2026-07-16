import XCTest
import TransfrCore
@testable import TransfrUI

/// The progressive walk load: once a journey is chosen, its transfers' geometry
/// streams into the cache behind the timeline, and the transition screen / walk
/// screens read it back instantly. These tests drive `TripModel` with a stub
/// repository (a controllable delay + a call counter) and assert the streaming,
/// the cache reuse, and the transition-screen routing — no server, no UI.
final class TripModelPrefetchTests: XCTestCase {

    /// A repository whose `walk` resolves after a set delay and counts its calls,
    /// so we can prove the prefetch fetches each walk once and `walk(for:)` then
    /// serves the cache. An actor so the counter is safe across concurrent fetches.
    actor StubRepo: JourneyRepository {
        let delayNs: UInt64
        private(set) var walkCalls = 0
        init(delayNs: UInt64) { self.delayNs = delayNs }

        func walk(for key: WalkKey) async throws -> WalkResult {
            walkCalls += 1
            if delayNs > 0 { try? await Task.sleep(nanoseconds: delayNs) }
            return WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                              toPlatform: key.toPlatform, stepFree: key.stepFree, ok: true)
        }
        func journeys(from: String, to: String, when: Date?) async throws -> JourneysResponse {
            throw RepositoryError.notAvailable("journeys")
        }
        func stations(query: String) async throws -> [StationSuggestion] { [] }
        func platforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse {
            throw RepositoryError.notAvailable("platforms")
        }
    }

    private static let twoTransferJourney = """
    {"id":"j1","date":null,"duration_s":3600,"num_changes":2,"verdict":"feasible","legs":[],
     "transfers":[
       {"at_station":"Frankfurt","relation_id":1,"arrival_platform":"6","departure_platform":"12",
        "layover_s":600,"walk_time_s":66,"walk_distance_m":92,"verdict":"feasible","reason":null},
       {"at_station":"Mannheim","relation_id":2,"arrival_platform":"3","departure_platform":"5",
        "layover_s":300,"walk_time_s":40,"walk_distance_m":50,"verdict":"feasible","reason":null}
     ]}
    """

    private func makeJourney(_ json: String) throws -> Journey {
        let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
        return try dec.decode(Journey.self, from: Data(json.utf8))
    }

    @MainActor
    private func waitUntil(_ timeout: TimeInterval = 3, _ cond: () -> Bool) async {
        let start = Date()
        while !cond() {
            if Date().timeIntervalSince(start) > timeout { XCTFail("condition not met within \(timeout)s"); return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    @MainActor
    func testPrefetchStreamsInThenServesFromCache() async throws {
        let repo = StubRepo(delayNs: 40_000_000)   // 40 ms per walk
        let model = TripModel(repository: repo)
        let j = try makeJourney(Self.twoTransferJourney)

        model.select(j)
        XCTAssertEqual(model.path, [.journey])
        XCTAssertTrue(model.walkPrefetch.statuses.isEmpty, "select resets prefetch state")

        model.prefetchWalks(stepFree: false)
        XCTAssertEqual(model.walkPrefetch.total, 2)
        XCTAssertTrue(model.walkPrefetch.inFlight, "both walks should be streaming")
        XCTAssertFalse(model.walkPrefetch.isComplete)

        await waitUntil { model.walkPrefetch.isComplete }
        XCTAssertEqual(model.walkPrefetch.statuses, [.ready, .ready])
        XCTAssertEqual(model.walkPrefetch.readyCount, 2)

        let calls = await repo.walkCalls
        XCTAssertEqual(calls, 2, "each transfer's walk is fetched exactly once")

        // A subsequent walk(for:) is served from the prefetch cache — no new fetch.
        let key = WalkKey(relationId: 1, fromPlatform: "6", toPlatform: "12", stepFree: false)
        let cached = await model.walk(for: key)
        XCTAssertNotNil(cached)
        XCTAssertTrue(cached?.ok == true)
        let callsAfter = await repo.walkCalls
        XCTAssertEqual(callsAfter, 2, "cache hit must not trigger another fetch")
    }

    @MainActor
    func testPrefetchIsIdempotentPerVariant() async throws {
        let repo = StubRepo(delayNs: 0)
        let model = TripModel(repository: repo)
        model.select(try makeJourney(Self.twoTransferJourney))

        model.prefetchWalks(stepFree: false)
        await waitUntil { model.walkPrefetch.isComplete }
        model.prefetchWalks(stepFree: false)   // same journey+variant -> no-op
        model.prefetchWalks(stepFree: false)

        let calls = await repo.walkCalls
        XCTAssertEqual(calls, 2, "re-seeding the same journey+variant must not refetch")
    }

    @MainActor
    func testOpenTransfersRoutesThroughTransitionUntilReady() async throws {
        let repo = StubRepo(delayNs: 80_000_000)
        let model = TripModel(repository: repo)
        model.select(try makeJourney(Self.twoTransferJourney))
        model.prefetchWalks(stepFree: false)

        // Not ready yet -> the transition screen.
        model.openTransfers(startIndex: 0)
        XCTAssertEqual(model.path.last, Route.preparingWalks(startIndex: 0))
        model.path.removeLast()

        // Once ready -> straight to the walk carousel.
        await waitUntil { model.walkPrefetch.isComplete }
        model.openTransfers(startIndex: 1)
        XCTAssertEqual(model.path.last, Route.carousel(startIndex: 1))
    }

    @MainActor
    func testProceedToWalksReplacesTheTransitionInTheStack() async throws {
        let repo = StubRepo(delayNs: 0)
        let model = TripModel(repository: repo)
        model.select(try makeJourney(Self.twoTransferJourney))
        model.path.append(.preparingWalks(startIndex: 1))

        model.proceedToWalks(startIndex: 1)
        XCTAssertEqual(model.path.last, Route.carousel(startIndex: 1))
        XCTAssertFalse(model.path.contains(.preparingWalks(startIndex: 1)),
                       "Back from the walks should return to the timeline, not the transition")
    }

    @MainActor
    func testUnresolvableTransferIsUnavailableNotStuck() async throws {
        // A transfer with no relation/platforms yields no WalkKey -> unavailable,
        // never a perpetual spinner.
        let json = """
        {"id":"j2","duration_s":1800,"num_changes":1,"verdict":"unknown","legs":[],
         "transfers":[{"at_station":"Paris","relation_id":null,"arrival_platform":null,
           "departure_platform":null,"layover_s":300,"walk_time_s":null,"walk_distance_m":null,
           "verdict":"unknown","reason":"no_platform_data"}]}
        """
        let repo = StubRepo(delayNs: 0)
        let model = TripModel(repository: repo)
        model.select(try makeJourney(json))
        model.prefetchWalks(stepFree: false)

        await waitUntil { model.walkPrefetch.isComplete }
        XCTAssertEqual(model.walkPrefetch.statuses, [.unavailable])
        let calls = await repo.walkCalls
        XCTAssertEqual(calls, 0, "a transfer with no walk key is never fetched")
        // openTransfers goes straight to the carousel (nothing to wait for).
        model.openTransfers(startIndex: 0)
        XCTAssertEqual(model.path.last, Route.carousel(startIndex: 0))
    }
}
