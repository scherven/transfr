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

    /// A station-map export carries the GTFS-overlay track markers: one label per
    /// track, already projected to the export's ENU metres and lifted to its floor,
    /// so the client draws them without re-projecting. `level` is `nil` for a track
    /// the data can't place (then `z == 0`, the ground plane).
    @Test func decodesStationMapPlatformMarkers() throws {
        let json = """
        {
          "meta": {"relation_id": 1532513, "station_name": "Zürich HB", "ref_1": "3", "ref_2": "21",
            "algorithm": "astar", "context_mode": "touched", "stitched": false, "n_stitches": 0,
            "floor_height_m": 4.0, "z_is_level_not_elevation": true,
            "origin_lat": 47.3779, "origin_lon": 8.5403, "levels_present": [-2.0, 0.0],
            "bbox": {"min_x": -150.0, "max_x": 120.0, "min_y": -120.0, "max_y": 140.0},
            "n_context_ways": 1, "has_details": false, "detail_radius_m": 0.0, "n_details": 0,
            "all_platforms": true, "n_platform_markers": 3},
          "ways": [{"id": 1, "kind": "platform", "is_connector": false, "level_raw": "0",
            "points": [[-140.0, 10.0, 0.0], [-120.0, 30.0, 0.0]], "ref": "3", "level": 0}],
          "path": {"found": true, "points": [[-140.0, 10.0, 0.0], [108.0, -106.0, -8.0]],
            "walking_time_seconds": 120.0, "walking_distance_meters": 90.0},
          "details": [],
          "platform_markers": [
            {"track": "3", "x": -140.0, "y": 12.0, "z": 0.0, "level": 0},
            {"track": "21", "x": 108.0, "y": -106.0, "z": -8.0, "level": -2},
            {"track": "18", "x": -93.0, "y": 131.0, "z": 0.0, "level": null}
          ]
        }
        """
        let viz = try TransfrJSON.decode(VizExport.self, from: Data(json.utf8))
        #expect(viz.meta.allPlatforms == true)
        #expect(viz.meta.nPlatformMarkers == 3)

        let markers = try #require(viz.platformMarkers)
        #expect(markers.count == 3)
        #expect(Set(markers.map(\.track)) == ["3", "21", "18"])

        let t3 = try #require(markers.first { $0.track == "3" })
        #expect(t3.x == -140.0 && t3.y == 12.0 && t3.z == 0.0 && t3.level == 0)
        let t21 = try #require(markers.first { $0.track == "21" })
        #expect(t21.level == -2 && t21.z == -8.0)          // lifted to its floor
        // A deep/unplaceable track defaults to the ground plane with a nil level.
        let t18 = try #require(markers.first { $0.track == "18" })
        #expect(t18.level == nil && t18.z == 0.0)
    }

    /// A plain walk export has no station-map layer: `platformMarkers` and the two
    /// `meta` flags decode to `nil`, so older exports and walk views keep working.
    @Test func walkExportHasNoPlatformMarkers() throws {
        let viz = try TransfrJSON.decode(VizExport.self, from: Self.fixture("viz_berlin_1_16"))
        #expect(viz.platformMarkers == nil)
        #expect(viz.meta.allPlatforms == nil)
        #expect(viz.meta.nPlatformMarkers == nil)
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
