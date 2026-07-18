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
        func facilities(lat: Double, lon: Double, category: String) async throws -> FacilitiesResponse {
            throw RepositoryError.notAvailable("facilities")
        }
        func stationHealth(lat: Double, lon: Double) async throws -> StationHealthResponse {
            throw RepositoryError.notAvailable("stationHealth")
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
        XCTAssertEqual(calls, 1, "one /assess batches a whole journey's changes (both transfers), " +
                       "not one request per transfer")
    }

    /// Returns the same pending journeys, but every `/assess` throws — the network
    /// blip / server error / timeout that used to strand a transfer on `pending`
    /// forever. Counts calls so the retry is observable.
    actor FailingAssessRepo: JourneyRepository {
        private(set) var assessCalls = 0
        func journeys(from: String, to: String, when: Date?, assess: Bool,
                      noElevators: Bool = false) async throws -> JourneysResponse {
            let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
            return try dec.decode(JourneysResponse.self, from: Data(TripModelStreamingTests.pendingJSON.utf8))
        }
        func assess(_ interchanges: [AssessInterchange], noElevators: Bool = false) async throws -> [Transfer] {
            assessCalls += 1
            throw URLError(.timedOut)
        }
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
        func walk(for key: WalkKey) async throws -> WalkResult {
            WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                       toPlatform: key.toPlatform, stepFree: key.stepFree, ok: false)
        }
    }

    /// THE resilience contract (the reported bug): when `/assess` keeps failing, the
    /// transfers must NOT stay `pending` forever — the old behaviour, where the error
    /// was swallowed by `try?`, nothing retried, and `pending` was the only loading
    /// state. They retry, then settle on a TERMINAL `unknown` carrying
    /// `assessFailedReason`, so the journey rolls up honestly and PreparingWalksView
    /// can reach `allSettled` instead of spinning indefinitely.
    @MainActor
    func testFailedAssessSettlesTerminalRatherThanStuckPending() async throws {
        let repo = FailingAssessRepo()
        let model = TripModel(repository: repo)
        await model.plan()

        // No transfer is left pending — the whole point of the fix.
        await waitUntil { model.journeys.first?.transfers.allSatisfy { !$0.verdictKind.isPending } ?? false }
        let j = try XCTUnwrap(model.journeys.first)
        XCTAssertTrue(j.transfers.allSatisfy { $0.verdictKind == .unknown(TripModel.assessFailedReason) },
                      "a transfer whose /assess failed settles on a terminal unknown, not pending")
        XCTAssertEqual(j.verdictKind, .unknown(nil), "and the journey rolls up to unknown, never a fake feasible")

        let calls = await repo.assessCalls
        XCTAssertGreaterThan(calls, 1, "the failing /assess is retried before giving up")
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

    // MARK: - Instant navigation (#17)

    /// A `/journeys` the test can hold open, so the window between the tap and the
    /// response — the one the results screen now has to draw — can be observed
    /// rather than raced against. `waitUntilCalled()` returns once the fetch is in
    /// flight; `open()` lets it return. Only the FIRST call is held; later ones
    /// return at once, so a test can land a second search ahead of the first. Each
    /// response is tagged with its query's origin, to tell the two apart.
    actor GatedRepo: JourneyRepository {
        private var gate: CheckedContinuation<Void, Never>?
        private var enteredWaiter: CheckedContinuation<Void, Never>?
        private var entered = false
        private var calls = 0

        func journeys(from: String, to: String, when: Date?, assess: Bool, noElevators: Bool = false) async throws -> JourneysResponse {
            calls += 1
            let held = calls == 1
            entered = true
            enteredWaiter?.resume(); enteredWaiter = nil
            if held { await withCheckedContinuation { gate = $0 } }
            let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
            return try dec.decode(JourneysResponse.self, from: Data(Self.json(origin: from).utf8))
        }

        /// Suspend until `journeys` has been entered.
        func waitUntilCalled() async {
            if entered { return }
            await withCheckedContinuation { enteredWaiter = $0 }
        }

        func open() { gate?.resume(); gate = nil }

        /// One direct journey (no transfers), so nothing streams and the test is
        /// only about the fetch itself.
        static func json(origin: String) -> String {
            """
            {"origin":{"name":"\(origin)"},"destination":{"name":"C"},
             "departure_time":"2026-07-13T09:00:00Z",
             "journeys":[{"id":"j1","num_changes":0,"verdict":"feasible","transfers":[],"legs":[
                {"mode":"train","train_name":"ICE","cancelled":false,
                 "origin":{"name":"\(origin)","latitude":53.5,"longitude":10.0},
                 "destination":{"name":"C","latitude":48.78,"longitude":9.18},
                 "departure":"2026-07-13T09:00:00Z","arrival":"2026-07-13T11:00:00Z"}]}]}
            """
        }

        func assess(_ interchanges: [AssessInterchange], noElevators: Bool = false) async throws -> [Transfer] { [] }
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
        func walk(for key: WalkKey) async throws -> WalkResult {
            WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                       toPlatform: key.toPlatform, stepFree: key.stepFree, ok: false)
        }
    }

    /// Every `/journeys` fails.
    struct FailingRepo: JourneyRepository {
        func journeys(from: String, to: String, when: Date?, assess: Bool, noElevators: Bool = false) async throws -> JourneysResponse {
            throw URLError(.notConnectedToInternet)
        }
        func assess(_ interchanges: [AssessInterchange], noElevators: Bool = false) async throws -> [Transfer] { [] }
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
        func walk(for key: WalkKey) async throws -> WalkResult {
            WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                       toPlatform: key.toPlatform, stepFree: key.stepFree, ok: false)
        }
    }

    /// THE #17 CONTRACT: the tap is answered before the search returns. `plan()`
    /// pushes `.results` on its synchronous prefix — while `/journeys` is still in
    /// flight — so the user never waits on a frozen input screen.
    @MainActor
    func testPlanNavigatesToResultsBeforeJourneysLand() async throws {
        let repo = GatedRepo()
        let model = TripModel(repository: repo)

        let planning = Task { await model.plan() }
        await repo.waitUntilCalled()          // the /journeys await is now in flight

        XCTAssertEqual(model.path, [.results], "nav must not wait on the fetch")
        XCTAssertEqual(model.load, .loading)
        XCTAssertTrue(model.journeys.isEmpty,
                      "the empty window shows a skeleton — never fabricated journeys")

        await repo.open()
        await planning.value

        XCTAssertEqual(model.path, [.results], "and it stays there once they land")
        XCTAssertEqual(model.load, .loaded)
        XCTAssertEqual(model.journeys.count, 1)
    }

    /// A search that fails AFTER the instant nav leaves the user on the results
    /// screen, holding the error — not bounced back, and not stranded on a blank
    /// list. Recovery (Try again / Change the search) is ResultsView's, and both
    /// need exactly this state.
    @MainActor
    func testFailedFetchSurfacesOnResultsRatherThanBouncingBack() async throws {
        let model = TripModel(repository: FailingRepo())
        await model.plan()

        XCTAssertEqual(model.path, [.results], "the error is shown where the user already is")
        XCTAssertEqual(model.load, .failed("No connection to the planning service."))
        XCTAssertTrue(model.journeys.isEmpty)
    }

    /// Instant nav means the user can be back on the input screen searching again
    /// while the first fetch is still out. The slower, older response must not land
    /// on top of the newer search's results.
    @MainActor
    func testStaleSearchDoesNotOverwriteANewerOne() async throws {
        let repo = GatedRepo()
        let model = TripModel(repository: repo)

        model.origin = "OLD"
        let first = Task { await model.plan() }
        await repo.waitUntilCalled()          // first search held open

        // The user goes back and searches again; this one is not held, so it lands.
        model.origin = "NEW"
        await model.plan()
        XCTAssertEqual(model.response?.origin.name, "NEW")

        // Now the original, superseded search finally returns.
        await repo.open()
        await first.value

        XCTAssertEqual(model.response?.origin.name, "NEW",
                       "a superseded search must not clobber the current results")
        XCTAssertEqual(model.load, .loaded)
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
