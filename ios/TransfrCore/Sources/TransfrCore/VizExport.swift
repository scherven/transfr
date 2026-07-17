import Foundation

/// Swift mirror of the `core/viz_export.py` JSON — the single geometry contract
/// that feeds all four walk renderers (section / per-level / SceneKit 3D / AR),
/// so they never drift (DESIGN.md §13.3, "the keystone"). Everything is in
/// local-ENU metres about `meta.originLat/Lon`; **Z is the OSM `level` tag × a
/// nominal floor height, not surveyed elevation** (`meta.zIsLevelNotElevation`).

/// A point in local-ENU metres, decoded from a JSON `[x, y, z]` array.
public struct Point3: Codable, Hashable, Sendable {
    public var x: Float
    public var y: Float
    public var z: Float

    public init(x: Float, y: Float, z: Float) { self.x = x; self.y = y; self.z = z }

    public init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        x = try c.decode(Float.self)
        y = try c.decode(Float.self)
        // Some exports (rare) may omit z; default it to ground rather than fail.
        z = c.isAtEnd ? 0 : try c.decode(Float.self)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.unkeyedContainer()
        try c.encode(x); try c.encode(y); try c.encode(z)
    }
}

public struct VizExport: Codable, Sendable {
    public var meta: Meta
    public var ways: [Way]
    public var path: Path
    public var details: [Detail]

    public struct Meta: Codable, Sendable {
        public var relationId: Int
        public var stationName: String?
        public var ref1: String
        public var ref2: String
        public var algorithm: String
        public var contextMode: String
        public var stitched: Bool
        public var nStitches: Int
        public var floorHeightM: Float
        public var zIsLevelNotElevation: Bool
        public var originLat: Double
        public var originLon: Double
        public var levelsPresent: [Float]
        public var bbox: BBox?
        public var nContextWays: Int
        public var hasDetails: Bool
        public var detailRadiusM: Float
        public var nDetails: Int
    }

    public struct BBox: Codable, Sendable {
        public var minX: Float
        public var maxX: Float
        public var minY: Float
        public var maxY: Float
    }

    /// A context way the search touched. `kind` drives the renderer legend
    /// (platform / walkway / stairs / escalator / elevator / ramp / …).
    ///
    /// Platform ways additionally carry `ref` (the human platform number, from
    /// OSM tags) and `level` (the floor it sits on, resolved from the graph and
    /// used to lift its geometry — `nil` when the data can't place it reliably).
    /// Both are `nil` on non-platform ways and on older exports.
    public struct Way: Codable, Sendable {
        public var id: Int
        public var kind: String
        public var isConnector: Bool
        public var levelRaw: String?
        public var points: [Point3]
        public var ref: String?
        public var level: Int?
        /// Set only on connector ways (stairs/escalator/elevator/ramp) of a found
        /// walk: `true` when the path passes through this connector. A walk view
        /// hides connectors where this is `false`; a full station map ignores it.
        /// `nil` on platforms/walkways and on exports without a resolved path.
        public var walkRelevant: Bool?
    }

    /// The resolved route. When `found` is false only `reason` is populated.
    public struct Path: Codable, Sendable {
        public var found: Bool
        public var reason: String?
        public var nodeIds: [Int]?
        public var wayIds: [Int]?
        public var points: [Point3]?
        public var transitions: [Transition]?
        public var stitchSegments: [StitchSegment]?
        public var walkingTimeSeconds: Double?
        public var walkingDistanceMeters: Double?
        public var endpoints: Endpoints?
    }

    /// A level change along the path. Classified as stairs / escalator /
    /// elevator / ramp / vertical — the single decision at each floor change.
    public struct Transition: Codable, Sendable {
        public var kind: String
        public var wayId: Int?
        public var nodeId: Int?
        public var from: Point3
        public var to: Point3
    }

    /// A synthetic bridge hop — a join the pathfinder *inferred* rather than read
    /// off a mapped footpath (`core/build_stitch_bridges.py`). Surfaced separately
    /// so the renderer can flag it as lower-confidence.
    public struct StitchSegment: Codable, Sendable {
        public var from: Point3
        public var to: Point3
        public var lengthM: Double
    }

    public struct Endpoints: Codable, Sendable {
        public var start: Endpoint
        public var end: Endpoint
        public struct Endpoint: Codable, Sendable {
            public var ref: String
            public var xyz: Point3
        }
    }

    /// A landmark/store/building around the station (optional details layer).
    /// A POI carries `xyz` (+ optional `outline` footprint); a building carries
    /// `points` (its outline).
    public struct Detail: Codable, Sendable {
        public var kind: String        // "poi" | "building"
        public var category: String
        public var subtype: String?
        public var name: String?
        public var dist: Double
        public var xyz: Point3?
        public var points: [Point3]?
        public var outline: [Point3]?
    }
}
