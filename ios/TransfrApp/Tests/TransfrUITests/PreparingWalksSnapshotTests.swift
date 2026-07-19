import XCTest
import SwiftUI
import TransfrCore
@testable import TransfrUI

/// Headless render of the verdict-streaming transition screen in a mid-stream
/// state (one transfer assessed, one still resolving), so the layout can be
/// eyeballed without driving the Simulator UI — the same ImageRenderer path
/// WalkSceneTests uses.
final class PreparingWalksSnapshotTests: XCTestCase {

    /// A repository stub used only to construct the model; the test sets the
    /// mid-stream [feasible, pending] state directly (see below), so `journeys` /
    /// `assess` here aren't what drives the render.
    actor MixedRepo: JourneyRepository {
        func journeys(from: String, to: String, when: Date?, assess: Bool,
                      noElevators: Bool = false) async throws -> JourneysResponse {
            let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
            return try dec.decode(JourneysResponse.self, from: Data(PreparingWalksSnapshotTests.json.utf8))
        }
        func assess(_ interchanges: [AssessInterchange], noElevators: Bool = false) async throws -> [Transfer] {
            var out: [Transfer] = []
            for ic in interchanges {
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
    func testRenderTransitionScreenMidStream() throws {
        // Construct the mid-stream state directly rather than racing the real verdict
        // stream. `streamVerdicts` batches per JOURNEY, so a single journey's transfers
        // resolve atomically (both pending -> both feasible): the transient
        // [feasible, pending] this screen renders is never observable for one journey by
        // waiting on the stream. What this test checks is the RENDER of that layout, so
        // we build the state and render it deterministically -- no timing, no race.
        let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
        var resp = try dec.decode(JourneysResponse.self, from: Data(Self.json.utf8))
        // First change assessed feasible; the second stays pending (as decoded).
        resp.journeys[0].transfers[0] = Transfer(
            atStation: "Frankfurt (Main) Hbf", relationId: 42,
            arrivalPlatform: "6", departurePlatform: "12",
            layoverS: 600, walkTimeS: 66, walkDistanceM: 92, verdict: "feasible")

        let model = TripModel(repository: MixedRepo())
        model.response = resp
        model.selectedIndex = 0

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
