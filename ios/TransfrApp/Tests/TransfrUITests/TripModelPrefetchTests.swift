import XCTest
import TransfrCore
@testable import TransfrUI

/// The progressive load: `/journeys?assess=false` returns the itineraries
/// instantly with `pending` transfers, then each real verdict streams in via
/// `/assess` and updates `response` in place. These drive `TripModel` with a stub
/// repository (pending journeys + a delayed, counted `assess`) and assert the
/// streaming, the live update of the selected journey, and the transition routing.
final class TripModelStreamingTests: XCTestCase {

    /// Returns a fixed journeys response with `pending` transfers, and an `assess`
    /// that (after an optional delay) marks each interchange `feasible`. Counts
    /// assess calls so we can prove one fires per transfer.
    actor StubRepo: JourneyRepository {
        let assessDelayNs: UInt64
        private(set) var assessCalls = 0
        init(assessDelayNs: UInt64 = 0) { self.assessDelayNs = assessDelayNs }

        func journeys(from: String, to: String, when: Date?, assess: Bool,
                      noElevators: Bool = false) async throws -> JourneysResponse {
            let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
            return try dec.decode(JourneysResponse.self, from: Data(TripModelStreamingTests.pendingJSON.utf8))
        }
        func assess(_ interchanges: [AssessInterchange], noElevators: Bool = false) async throws -> [Transfer] {
            assessCalls += 1
            if assessDelayNs > 0 { try? await Task.sleep(nanoseconds: assessDelayNs) }
            return interchanges.map {
                Transfer(atStation: $0.atStation, relationId: 42,
                         arrivalPlatform: $0.arrPlatform, departurePlatform: $0.depPlatform,
                         layoverS: 600, walkTimeS: 66, walkDistanceM: 92, verdict: "feasible")
            }
        }
        func stations(query: String) async throws -> [StationSuggestion] { [] }
        func platforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse {
            throw RepositoryError.notAvailable("platforms")
        }
        func stationWalk(lat: Double, lon: Double, fromPlatform: String, stepFree: Bool) async throws -> StationWalkResponse {
            throw RepositoryError.notAvailable("stationWalk")
        }
        func walk(for key: WalkKey) async throws -> WalkResult {
            WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                       toPlatform: key.toPlatform, stepFree: key.stepFree, ok: false)
        }
    }

    // One journey, 3 transit legs -> 2 changes of train, both pending.
    static let pendingJSON = """
    {"origin":{"name":"A"},"destination":{"name":"C"},"departure_time":"2026-07-13T09:00:00Z",
     "journeys":[{"id":"j1","num_changes":2,"verdict":"pending","transfers":[
        {"at_station":"Frankfurt","relation_id":null,"arrival_platform":"6","departure_platform":"12",
         "layover_s":600,"walk_time_s":null,"walk_distance_m":null,"verdict":"pending","reason":null},
        {"at_station":"Mannheim","relation_id":null,"arrival_platform":"3","departure_platform":"5",
         "layover_s":600,"walk_time_s":null,"walk_distance_m":null,"verdict":"pending","reason":null}
     ],"legs":[
        {"mode":"train","train_name":"ICE","cancelled":false,"origin":{"name":"A","latitude":53.5,"longitude":10.0},
         "destination":{"name":"Frankfurt","latitude":50.1,"longitude":8.66},
         "departure":"2026-07-13T09:00:00Z","arrival":"2026-07-13T09:20:00Z","arrival_platform":"6"},
        {"mode":"train","train_name":"ICE","cancelled":false,"origin":{"name":"Frankfurt","latitude":50.1,"longitude":8.66},
         "destination":{"name":"Mannheim","latitude":49.48,"longitude":8.47},
         "departure":"2026-07-13T09:30:00Z","arrival":"2026-07-13T09:50:00Z",
         "departure_platform":"12","arrival_platform":"3"},
        {"mode":"train","train_name":"ICE","cancelled":false,"origin":{"name":"Mannheim","latitude":49.48,"longitude":8.47},
         "destination":{"name":"C","latitude":48.78,"longitude":9.18},
         "departure":"2026-07-13T10:00:00Z","arrival":"2026-07-13T11:00:00Z","departure_platform":"5"}
     ]}]}
    """

    @MainActor
    private func waitUntil(_ timeout: TimeInterval = 3, _ cond: () -> Bool) async {
        let start = Date()
        while !cond() {
            if Date().timeIntervalSince(start) > timeout { XCTFail("condition not met within \(timeout)s"); return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    @MainActor
    func testPlanShowsPendingImmediatelyThenStreamsVerdicts() async throws {
        let repo = StubRepo(assessDelayNs: 30_000_000)
        let model = TripModel(repository: repo)
        await model.plan()

        // Landed on results at once, with pending transfers (no waiting on walks).
        XCTAssertEqual(model.path, [.results])
        XCTAssertEqual(model.load, .loaded)
        let j = try XCTUnwrap(model.journeys.first)
        XCTAssertEqual(j.transfers.count, 2)
        XCTAssertTrue(j.transfers.allSatisfy { $0.verdictKind.isPending })
        XCTAssertEqual(j.verdictKind, .pending)

        // Verdicts stream in and replace the pending transfers in place.
        await waitUntil { model.journeys.first?.transfers.allSatisfy { !$0.verdictKind.isPending } ?? false }
        let done = try XCTUnwrap(model.journeys.first)
        XCTAssertTrue(done.transfers.allSatisfy { $0.verdictKind == .feasible })
        XCTAssertTrue(done.transfers.allSatisfy { $0.walkTimeS == 66 })
        XCTAssertEqual(done.recomputedVerdict, .feasible)

        let calls = await repo.assessCalls
        XCTAssertEqual(calls, 2, "one /assess fires per still-pending transfer")
    }

    @MainActor
    func testSelectedJourneyUpdatesLiveAsVerdictsStream() async throws {
        let repo = StubRepo(assessDelayNs: 40_000_000)
        let model = TripModel(repository: repo)
        await model.plan()
        // Select while still pending; `selected` is an index, so streamed updates reach it.
        model.select(try XCTUnwrap(model.journeys.first))
        XCTAssertTrue(model.selectedHasPendingWalks)
        await waitUntil { !model.selectedHasPendingWalks }
        XCTAssertTrue(model.transfers.allSatisfy { $0.verdictKind == .feasible })
    }

    @MainActor
    func testSelectRoutesThroughTransitionOnlyWhilePending() async throws {
        let repo = StubRepo(assessDelayNs: 60_000_000)
        let model = TripModel(repository: repo)
        await model.plan()

        // Pending -> transition screen.
        model.select(try XCTUnwrap(model.journeys.first))
        XCTAssertEqual(model.path.last, Route.preparingWalks)
        model.path.removeLast()

        // Assessed -> straight to the timeline.
        await waitUntil { !model.selectedHasPendingWalks }
        model.select(try XCTUnwrap(model.journeys.first))
        XCTAssertEqual(model.path.last, Route.journey)
    }

    @MainActor
    func testProceedToTimelineReplacesTransition() async throws {
        let model = TripModel(repository: StubRepo())
        await model.plan()
        model.path.append(.preparingWalks)
        model.proceedToTimeline()
        XCTAssertEqual(model.path.last, Route.journey)
        XCTAssertFalse(model.path.contains(.preparingWalks),
                       "Back from the timeline returns to results, not the transition")
    }

    @MainActor
    func testInterchangesBuiltFromLegsAlignWithTransfers() throws {
        let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
        let resp = try dec.decode(JourneysResponse.self, from: Data(Self.pendingJSON.utf8))
        let ics = TripModel.interchanges(of: try XCTUnwrap(resp.journeys.first))
        XCTAssertEqual(ics.count, 2)
        XCTAssertEqual(ics[0].atStation, "Frankfurt")
        XCTAssertEqual(ics[0].arrPlatform, "6")     // arriving leg's arrival platform
        XCTAssertEqual(ics[0].depPlatform, "12")    // departing leg's departure platform
        XCTAssertEqual(ics[1].atStation, "Mannheim")
        XCTAssertEqual(ics[1].arrPlatform, "3")
        XCTAssertEqual(ics[1].depPlatform, "5")
    }
}
