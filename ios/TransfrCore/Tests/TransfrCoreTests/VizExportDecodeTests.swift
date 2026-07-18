import Foundation
import Testing
@testable import TransfrCore

/// Decodes a **real** `core/viz_export.py` output (Berlin Hbf Pl 1→16, the
/// 9-level escalator+elevator reference case from DESIGN.md §7.6). This is the
/// contract every walk renderer reads, so a decode failure here means the
/// section / per-level / 3D / AR views would all break together.
struct VizExportDecodeTests {

    static func fixture(_ name: String) throws -> Data {
        let url = try #require(
            Bundle.module.url(forResource: "Fixtures/\(name)", withExtension: "json"),
            "missing fixture \(name).json"
        )
        return try Data(contentsOf: url)
    }

    @Test func decodesBerlinWalk() throws {
        let viz = try TransfrJSON.decode(VizExport.self, from: Self.fixture("viz_berlin_1_16"))

        #expect(viz.meta.stationName == "Berlin Hauptbahnhof")
        #expect(viz.meta.ref1 == "1")
        #expect(viz.meta.ref2 == "16")
        // Z is schematic level, not surveyed elevation — the honesty flag.
        #expect(viz.meta.zIsLevelNotElevation)
        // This case famously spans L−2 … L+2 (9 half-levels).
        #expect(viz.meta.levelsPresent.contains(-2))
        #expect(viz.meta.levelsPresent.contains(2))
        #expect(!viz.ways.isEmpty)

        let path = viz.path
        #expect(path.found)
        let time = try #require(path.walkingTimeSeconds)
        let dist = try #require(path.walkingDistanceMeters)
        #expect(time > 0)
        #expect(dist > 0)

        // Point3 decoded from [x,y,z] arrays.
        let pts = try #require(path.points)
        #expect(!pts.isEmpty)

        // Endpoints line up with the requested platforms.
        let ends = try #require(path.endpoints)
        #expect(ends.start.ref == "1")
        #expect(ends.end.ref == "16")

        // The reason this case matters: real vertical circulation on the route.
        let transitions = try #require(path.transitions)
        #expect(!transitions.isEmpty)
        #expect(transitions.allSatisfy { !$0.kind.isEmpty })
    }

    @Test func decodesDetailsLayer() throws {
        let viz = try TransfrJSON.decode(VizExport.self, from: Self.fixture("viz_berlin_1_16_details"))
        #expect(viz.meta.hasDetails)
        #expect(!viz.details.isEmpty)
        // Each detail is either a point POI (xyz) or an outlined building (points).
        for d in viz.details {
            #expect(d.xyz != nil || d.points != nil)
            #expect(!d.category.isEmpty)
        }
    }

    /// The `/facility-map` contract decodes: the browse export + the ranked list,
    /// aligned index-for-index (details[i] ↔ facilities[i]) so a tapped pin maps
    /// straight back to its facility. Every pin is a `focus` POI with a cheap
    /// nearest-platform anchor.
    @Test func decodesFacilityMap() throws {
        let map = try TransfrJSON.decode(FacilityMapResponse.self, from: Self.fixture("facility_map_berlin"))
        #expect(map.found)
        #expect(map.station == "Berlin Hauptbahnhof")
        #expect(map.category == "toilets")
        #expect(map.facilities.count == 4)

        let export = try #require(map.export)
        let pois = export.details.filter { $0.kind == "poi" }
        #expect(pois.count == map.facilities.count)
        #expect(pois.allSatisfy { $0.focus == true && $0.xyz != nil })
        // Order is preserved, so the pin/list correspondence holds.
        #expect(zip(pois, map.facilities).allSatisfy { $0.name == $1.name })
        // The cheap platform anchor came through for "Show walk".
        #expect(map.facilities.allSatisfy { $0.nearestPlatform != nil })
    }

    /// A `Point3` round-trips through our unkeyed [x,y,z] coding.
    @Test func point3RoundTrips() throws {
        let p = Point3(x: -59.9, y: 65.8, z: -8.0)
        let data = try TransfrJSON.encoder.encode(p)
        let back = try TransfrJSON.decoder.decode(Point3.self, from: data)
        #expect(back == p)
        // …and it really is a bare array on the wire.
        #expect(String(data: data, encoding: .utf8)?.hasPrefix("[") == true)
    }
}
