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
        // Scheduled platforms decode too (planned_departure_platform → camelCase).
        #expect(j.legs[0].plannedDeparturePlatform == "7")   // live == planned (a guess)
        #expect(j.legs[2].plannedDeparturePlatform == "8")   // live "5" != planned "8" (a change)
        #expect(j.transfers[1].plannedDeparturePlatform == "8")
        #expect(j.transfers[1].plannedArrivalPlatform == "4")
    }

    /// The decoded scheduled platform drives the planned/live/changed rendering:
    /// the Mannheim onward train is a real platform change (live 5, planned 8).
    @Test func plannedPlatformDrivesChangeDisplay() throws {
        let data = try Self.fixture("journeys_hamburg_stuttgart")
        let j = try #require(try TransfrJSON.decode(JourneysResponse.self, from: data).journeys.first)
        let t = j.transfers[1]
        let dep = PlatformDisplay.make(live: t.departurePlatform, planned: t.plannedDeparturePlatform,
                                       actual: t.departurePlatformActual)
        #expect(dep == .changed(current: "5", from: "8"))
        let arr = PlatformDisplay.make(live: t.arrivalPlatform, planned: t.plannedArrivalPlatform,
                                       actual: t.arrivalPlatformActual)
        #expect(arr == .planned("4"))   // arrival unchanged ⇒ still the schedule's guess
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

    // MARK: - /facilities contract (nearest facility; honest degradation)

    @Test func decodesFacilitiesFound() throws {
        let data = try Self.fixture("facilities_berlin_toilets")
        let r = try TransfrJSON.decode(FacilitiesResponse.self, from: data)
        #expect(r.found)
        #expect(r.station == "Berlin, S Hauptbahnhof")
        #expect(r.relationId == 5688520)
        #expect(r.facilities.count == 2)
        // snake_case → camelCase across the row's fields, nearest first.
        let nearest = try #require(r.facilities.first)
        #expect(nearest.subtype == "toilets")
        #expect(nearest.distanceM == 7.2)
        #expect(nearest.nearestPlatform == "1")
        #expect(nearest.walkTimeS == 33.0)
        #expect(nearest.walkDistanceM == 38.0)
        // A facility without a routed anchor keeps distance only.
        #expect(r.facilities[1].nearestPlatform == nil)
        #expect(r.facilities[1].walkTimeS == nil)
        #expect(r.facilities[1].level == "2")
    }

    @Test func decodesFacilitiesDegraded() throws {
        // The honest-degradation state: layer absent → found=false + typed reason.
        let data = try Self.fixture("facilities_no_layer")
        let r = try TransfrJSON.decode(FacilitiesResponse.self, from: data)
        #expect(!r.found)
        #expect(r.reason == "no_poi_layer")
        #expect(r.facilities.isEmpty)
        // The station still resolved even though its POIs aren't available.
        #expect(r.station == "Berlin, S Hauptbahnhof")
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

    // MARK: - /station-walk contract (the "full station walk" Advanced tool)

    @Test func decodesStationWalk() throws {
        let data = try Self.fixture("station_walk_berlin")
        let sw = try TransfrJSON.decode(StationWalkResponse.self, from: data)
        #expect(sw.found)
        #expect(sw.relationId == 5688520)
        #expect(sw.station == "Berlin, S Hauptbahnhof")
        #expect(sw.fromPlatform == "1")
        #expect(sw.stepFree == false)
        #expect(sw.results.count == 4)
        // snake_case to_platform / walk_time_s / walk_distance_m → camelCase.
        #expect(sw.results.first?.toPlatform == "2")
        #expect(sw.results.first?.walkTimeS == 5.3)
        #expect(sw.results.first?.walkDistanceM == 7.4)
        // Reachable rows are nearest-first (ascending walk distance)…
        let reachable = sw.results.filter(\.found)
        #expect(reachable.map(\.walkDistanceM) == reachable.map(\.walkDistanceM).sorted { ($0 ?? 0) < ($1 ?? 0) })
        // …and an unreachable platform decodes as a found=false row with a reason.
        let last = try #require(sw.results.last)
        #expect(last.found == false)
        #expect(last.reason == "platform_not_found")
        #expect(last.walkTimeS == nil)
    }

    // MARK: - /station-health contract (the Map-health per-station query)

    @Test func decodesStationHealth() throws {
        let data = try Self.fixture("station_health_berlin")
        let h = try TransfrJSON.decode(StationHealthResponse.self, from: data)
        #expect(h.found)
        #expect(h.station == "Berlin, S Hauptbahnhof")
        #expect(h.platformCount == 14)
        // snake_case platform_count / connected_pct → camelCase; the three buckets
        // sum to the evaluated pairs.
        #expect(h.connected == 89 && h.stitchable == 1 && h.island == 1)
        #expect(h.pairCount == 91)
        #expect(h.connectedPct == 97.8)
        #expect(!h.sampled)
        // The example pairs carry their kind, stitchable first.
        #expect(h.examples.count == 2)
        #expect(h.examples[0].kind == "stitchable")
        #expect(h.examples[0].fromPlatform == "9" && h.examples[0].toPlatform == "12")
        #expect(h.examples[1].kind == "island")
    }
}
