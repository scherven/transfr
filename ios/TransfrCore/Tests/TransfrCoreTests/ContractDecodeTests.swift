import Foundation
import Testing
@testable import TransfrCore

/// These decode the **Python engine's own outputs** (goldens generated from
/// `api/schemas.py` via pydantic, and a real `core/viz_export.py` file). If the
/// server contract shifts shape, a decode here fails immediately — that's the
/// whole point of anchoring the Swift types to the engine's fixtures.
struct ContractDecodeTests {

    /// Load a fixture bundled as a test resource.
    static func fixture(_ name: String) throws -> Data {
        let url = try #require(
            Bundle.module.url(forResource: "Fixtures/\(name)", withExtension: "json"),
            "missing fixture \(name).json"
        )
        return try Data(contentsOf: url)
    }

    // MARK: - /journeys contract

    @Test func decodesJourneysResponse() throws {
        let data = try Self.fixture("journeys_hamburg_stuttgart")
        let resp = try TransfrJSON.decode(JourneysResponse.self, from: data)

        #expect(resp.origin.name == "Hamburg Hbf")
        #expect(resp.destination.name == "Stuttgart Hbf")
        #expect(resp.journeys.count == 1)

        let j = try #require(resp.journeys.first)
        #expect(j.numChanges == 2)
        #expect(j.legs.count == 3)
        #expect(j.transfers.count == 2)
        // snake_case → camelCase mapping actually landed:
        #expect(j.legs[0].trainName == "ICE 571")
        #expect(j.legs[1].arrivalDelayS == 120)
        #expect(j.transfers[1].departurePlatform == "5")
        #expect(j.transfers[1].walkTimeS == 181.0)
    }

    // MARK: - worst-wins verdict (port of api/pipeline.py:rollup_verdict)

    @Test func journeyVerdictIsWorstTransfer() throws {
        let data = try Self.fixture("journeys_hamburg_stuttgart")
        let j = try #require(try TransfrJSON.decode(JourneysResponse.self, from: data).journeys.first)

        // Göttingen feasible + Mannheim tight ⇒ journey is tight.
        #expect(j.transfers[0].verdictKind == .feasible)
        #expect(j.transfers[1].verdictKind == .tight)
        #expect(j.verdictKind == .tight)
        // Independent client-side recomputation must agree with the server roll-up.
        #expect(j.recomputedVerdict == .tight)
    }

    @Test func verdictRankingMatchesServer() {
        // _VERDICT_RANK = {infeasible:0, unknown:1, tight:2, feasible:3}; min = worst.
        #expect([Verdict.feasible, .feasible].rolledUp() == .feasible)
        #expect([Verdict.feasible, .tight].rolledUp() == .tight)
        #expect([Verdict.tight, .unknown("no_platform_data")].rolledUp() == .unknown("no_platform_data"))
        #expect([Verdict.unknown(nil), .infeasible].rolledUp() == .infeasible)
        // Direct journey (no transfers) is feasible.
        #expect([Verdict]().rolledUp() == .feasible)
    }

    @Test func unknownVerdictCarriesReason() {
        let t = Transfer(atStation: "X", relationId: nil, arrivalPlatform: nil,
                         departurePlatform: nil, layoverS: nil, walkTimeS: nil,
                         walkDistanceM: nil, verdict: "unknown", reason: "no_platform_data")
        #expect(t.verdictKind == .unknown("no_platform_data"))
        #expect(t.verdictKind.raw == "unknown")
    }

    // MARK: - /stations and /transfer contracts

    @Test func decodesStationSuggestions() throws {
        let data = try Self.fixture("stations_suggest")
        let suggestions = try TransfrJSON.decode([StationSuggestion].self, from: data)
        #expect(suggestions.count == 2)
        #expect(suggestions[0].name == "Hamburg Hbf")
        #expect(suggestions[0].country == "DE")
    }

    @Test func decodesPlatformWalk() throws {
        let data = try Self.fixture("platform_walk_berlin")
        let pw = try TransfrJSON.decode(PlatformWalkResponse.self, from: data)
        #expect(pw.station == "Berlin Hbf")
        #expect(pw.found)
        #expect(pw.fromPlatform == "1")
        #expect(pw.toPlatform == "16")
        #expect(pw.walkDistanceM == 107.0)
    }

    // MARK: - /station-platforms contract (the walk-only door)

    @Test func decodesStationPlatforms() throws {
        let data = try Self.fixture("station_platforms_berlin")
        let sp = try TransfrJSON.decode(StationPlatformsResponse.self, from: data)
        #expect(sp.found)
        // snake_case relation_id → camelCase relationId; the id a subsequent
        // /walk uses so both calls resolve the same station.
        #expect(sp.relationId == 5688520)
        #expect(sp.station == "Berlin, S Hauptbahnhof")
        #expect(sp.platforms.count == 14)
        #expect(sp.platforms.first == "1" && sp.platforms.last == "16")
    }
}
