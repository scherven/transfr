import XCTest
import SwiftUI
import TransfrCore
@testable import TransfrUI

/// Headless render of the progressive-load transition screen in a mid-stream
/// state (one walk ready, one still resolving), so the layout can be eyeballed
/// without driving the Simulator UI — the same ImageRenderer path WalkSceneTests
/// uses for the walk canvases.
@MainActor
final class PreparingWalksSnapshotTests: XCTestCase {

    /// rel 1 resolves instantly; rel 2 hangs, so the screen sits at [ready, loading].
    actor MixedRepo: JourneyRepository {
        func walk(for key: WalkKey) async throws -> WalkResult {
            if key.relationId != 1 { try? await Task.sleep(nanoseconds: 60_000_000_000) }
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

    private static let json = """
    {"id":"j1","duration_s":3600,"num_changes":2,"verdict":"feasible","legs":[],
     "transfers":[
       {"at_station":"Frankfurt (Main) Hbf","relation_id":1,"arrival_platform":"6","departure_platform":"12",
        "layover_s":600,"walk_time_s":66,"walk_distance_m":92,"verdict":"feasible","reason":null},
       {"at_station":"Mannheim Hbf","relation_id":2,"arrival_platform":"3","departure_platform":"5",
        "layover_s":300,"walk_time_s":40,"walk_distance_m":50,"verdict":"feasible","reason":null}
     ]}
    """

    func testRenderTransitionScreenMidStream() async throws {
        let model = TripModel(repository: MixedRepo())
        let dec = JSONDecoder(); dec.keyDecodingStrategy = .convertFromSnakeCase
        model.select(try dec.decode(Journey.self, from: Data(Self.json.utf8)))
        model.prefetchWalks(stepFree: false)

        // Wait until the first walk is ready while the second is still streaming.
        let start = Date()
        while !(model.walkStatus(at: 0) == .ready && model.walkStatus(at: 1) == .loading) {
            if Date().timeIntervalSince(start) > 3 { break }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertEqual(model.walkStatus(at: 0), .ready)
        XCTAssertEqual(model.walkStatus(at: 1), .loading)

        let view = NavigationStack {
            PreparingWalksView(startIndex: 1)
                .environment(model)
                .environment(SettingsStore())
        }
        .frame(width: 390, height: 780)

        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try XCTUnwrap(renderer.uiImage, "ImageRenderer produced no image")
        let data = try XCTUnwrap(image.pngData(), "no PNG data")
        XCTAssertGreaterThan(data.count, 1000, "suspiciously small render")
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("preparing_walks.png")
        try data.write(to: url)
        print("RENDER_PNG: \(url.path)")
    }
}
