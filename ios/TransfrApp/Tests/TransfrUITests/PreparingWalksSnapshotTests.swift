import XCTest
import SwiftUI
import TransfrCore
@testable import TransfrUI

/// Headless render of the verdict-streaming transition screen in a mid-stream
/// state (one transfer assessed, one still resolving), so the layout can be
/// eyeballed without driving the Simulator UI — the same ImageRenderer path
/// WalkSceneTests uses.
final class PreparingWalksSnapshotTests: XCTestCase {

    /// Frankfurt assesses instantly; Mannheim hangs, so the screen sits at
    /// [feasible, pending].
    actor MixedRepo: JourneyRepository {
        func journeys(from: String, to: String, when: Date?, assess: Bool) async throws -> JourneysResponse {
            let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
            return try dec.decode(JourneysResponse.self, from: Data(PreparingWalksSnapshotTests.json.utf8))
        }
        func assess(_ interchanges: [AssessInterchange]) async throws -> [Transfer] {
            var out: [Transfer] = []
            for ic in interchanges {
                if ic.atStation == "Mannheim Hbf" { try? await Task.sleep(nanoseconds: 60_000_000_000) }
                out.append(Transfer(atStation: ic.atStation, relationId: 42,
                                    arrivalPlatform: ic.arrPlatform, departurePlatform: ic.depPlatform,
                                    layoverS: 600, walkTimeS: 66, walkDistanceM: 92, verdict: "feasible"))
            }
            return out
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

    static let json = """
    {"origin":{"name":"A"},"destination":{"name":"C"},"departure_time":"2026-07-13T09:00:00Z",
     "journeys":[{"id":"j1","num_changes":2,"verdict":"pending","transfers":[
        {"at_station":"Frankfurt (Main) Hbf","relation_id":null,"arrival_platform":"6","departure_platform":"12",
         "layover_s":600,"walk_time_s":null,"walk_distance_m":null,"verdict":"pending","reason":null},
        {"at_station":"Mannheim Hbf","relation_id":null,"arrival_platform":"3","departure_platform":"5",
         "layover_s":600,"walk_time_s":null,"walk_distance_m":null,"verdict":"pending","reason":null}
     ],"legs":[
        {"mode":"train","train_name":"ICE","cancelled":false,"origin":{"name":"A","latitude":53.5,"longitude":10.0},
         "destination":{"name":"Frankfurt (Main) Hbf","latitude":50.1,"longitude":8.66},
         "departure":"2026-07-13T09:00:00Z","arrival":"2026-07-13T09:20:00Z","arrival_platform":"6"},
        {"mode":"train","train_name":"ICE","cancelled":false,"origin":{"name":"Frankfurt (Main) Hbf","latitude":50.1,"longitude":8.66},
         "destination":{"name":"Mannheim Hbf","latitude":49.48,"longitude":8.47},
         "departure":"2026-07-13T09:30:00Z","arrival":"2026-07-13T09:50:00Z",
         "departure_platform":"12","arrival_platform":"3"},
        {"mode":"train","train_name":"ICE","cancelled":false,"origin":{"name":"Mannheim Hbf","latitude":49.48,"longitude":8.47},
         "destination":{"name":"C","latitude":48.78,"longitude":9.18},
         "departure":"2026-07-13T10:00:00Z","arrival":"2026-07-13T11:00:00Z","departure_platform":"5"}
     ]}]}
    """

    @MainActor
    func testRenderTransitionScreenMidStream() async throws {
        let model = TripModel(repository: MixedRepo())
        await model.plan()

        // Wait until the first transfer is assessed while the second still streams.
        let start = Date()
        while !(model.journeys.first?.transfers.first?.verdictKind == .feasible
                && model.journeys.first?.transfers.last?.verdictKind.isPending == true) {
            if Date().timeIntervalSince(start) > 3 { break }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        model.select(try XCTUnwrap(model.journeys.first))
        XCTAssertEqual(model.transfers.first?.verdictKind, .feasible)
        XCTAssertEqual(model.transfers.last?.verdictKind, .pending)

        // Render the screen directly (no NavigationStack — ImageRenderer has no
        // UIKit interface idiom headless; the nav title is a no-op offscreen).
        let view = PreparingWalksView()
            .environment(model)
            .environment(SettingsStore())
            .frame(width: 390, height: 780)

        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try XCTUnwrap(renderer.uiImage, "ImageRenderer produced no image")
        let data = try XCTUnwrap(image.pngData(), "no PNG data")
        XCTAssertGreaterThan(data.count, 1000, "suspiciously small render")
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("preparing_walks.png")
        try data.write(to: url)
let attachment = XCTAttachment(data: data, uniformTypeIdentifier: "public.png")
attachment.name = "preparing_walks"
attachment.lifetime = .keepAlways
add(attachment)
    }
}
