import Foundation

/// Swift mirror of `api/schemas.py` — the typed HTTP contract for `/journeys`,
/// `/stations`, and `/transfer`. Field names are camelCased here; the shared
/// `TransfrJSON.decoder` applies `.convertFromSnakeCase`, so `train_name` on the
/// wire decodes into `trainName` etc. Keep this file and `schemas.py` in lockstep;
/// the fixture-decode tests fail the instant they drift.

public struct Place: Codable, Hashable, Sendable {
    public var id: String?
    public var name: String?
    public var latitude: Double?
    public var longitude: Double?

    public init(id: String? = nil, name: String? = nil, latitude: Double? = nil, longitude: Double? = nil) {
        self.id = id; self.name = name; self.latitude = latitude; self.longitude = longitude
    }
}

public struct Leg: Codable, Hashable, Sendable {
    public var mode: String
    public var trainName: String?
    public var origin: Place
    public var destination: Place
    public var departure: String?
    public var arrival: String?
    public var plannedDeparture: String?
    public var plannedArrival: String?
    public var departurePlatform: String?
    public var arrivalPlatform: String?
    public var departureDelayS: Int?
    public var arrivalDelayS: Int?
    public var cancelled: Bool
    public var distanceM: Int?
}

public struct Transfer: Codable, Hashable, Sendable {
    public var atStation: String?
    public var relationId: Int?
    public var arrivalPlatform: String?
    public var departurePlatform: String?
    public var layoverS: Double?
    public var walkTimeS: Double?
    public var walkDistanceM: Double?
    public var verdict: String
    public var reason: String?

    public init(atStation: String? = nil, relationId: Int? = nil,
                arrivalPlatform: String? = nil, departurePlatform: String? = nil,
                layoverS: Double? = nil, walkTimeS: Double? = nil, walkDistanceM: Double? = nil,
                verdict: String, reason: String? = nil) {
        self.atStation = atStation; self.relationId = relationId
        self.arrivalPlatform = arrivalPlatform; self.departurePlatform = departurePlatform
        self.layoverS = layoverS; self.walkTimeS = walkTimeS; self.walkDistanceM = walkDistanceM
        self.verdict = verdict; self.reason = reason
    }

    /// The typed verdict, combining the raw string with its reason.
    public var verdictKind: Verdict { Verdict(raw: verdict, reason: reason) }
}

public struct Journey: Codable, Hashable, Sendable, Identifiable {
    public var id: String?
    public var date: String?
    public var durationS: Int?
    public var numChanges: Int
    /// Rolled up server-side (worst-wins). Exposed typed via `verdictKind`.
    public var verdict: String
    public var legs: [Leg]
    public var transfers: [Transfer]

    public var verdictKind: Verdict { Verdict(raw: verdict, reason: nil) }

    /// Independent recomputation of the journey verdict from its transfers, so a
    /// client can re-verdict on cached/live data without the server. Should equal
    /// `verdictKind` for a freshly-fetched journey (asserted in tests).
    public var recomputedVerdict: Verdict { transfers.map(\.verdictKind).rolledUp() }
}

public struct JourneysResponse: Codable, Sendable {
    public var origin: Place
    public var destination: Place
    public var departureTime: String?
    public var journeys: [Journey]
}

// MARK: - Streaming assessment (/assess) — mirrors api/schemas.py

/// One change of train to assess, built from a journey's legs: the arrival end of
/// the incoming train and the departure end of the onward train. Sent to `/assess`
/// to fill in a `pending` transfer's real verdict behind a fast
/// `/journeys?assess=false`.
public struct AssessInterchange: Codable, Hashable, Sendable {
    public var atStation: String?
    public var arrLat: Double?
    public var arrLon: Double?
    public var arrPlatform: String?
    public var arrTime: String?
    public var depLat: Double?
    public var depLon: Double?
    public var depPlatform: String?
    public var depTime: String?

    public init(atStation: String? = nil,
                arrLat: Double? = nil, arrLon: Double? = nil, arrPlatform: String? = nil, arrTime: String? = nil,
                depLat: Double? = nil, depLon: Double? = nil, depPlatform: String? = nil, depTime: String? = nil) {
        self.atStation = atStation
        self.arrLat = arrLat; self.arrLon = arrLon; self.arrPlatform = arrPlatform; self.arrTime = arrTime
        self.depLat = depLat; self.depLon = depLon; self.depPlatform = depPlatform; self.depTime = depTime
    }
}

public struct AssessRequest: Codable, Sendable {
    public var interchanges: [AssessInterchange]
    /// Route every walk without lifts (the server's `avoid_elevators`). Carries the
    /// same preference the journey was searched under, so the streamed verdicts
    /// match what an `assess: true` search would have returned. Encodes to
    /// `no_elevators` via `TransfrJSON`'s `.convertToSnakeCase`.
    public var noElevators: Bool
    public init(interchanges: [AssessInterchange], noElevators: Bool = false) {
        self.interchanges = interchanges
        self.noElevators = noElevators
    }
}

public struct AssessResponse: Codable, Sendable {
    public var transfers: [Transfer]
    public init(transfers: [Transfer]) { self.transfers = transfers }
}

public struct StationSuggestion: Codable, Hashable, Sendable, Identifiable {
    public var id: String?
    public var name: String
    public var latitude: Double?
    public var longitude: Double?
    public var country: String?
}

public struct PlatformWalkResponse: Codable, Sendable {
    public var lat: Double
    public var lon: Double
    public var relationId: Int?
    public var station: String?
    public var fromPlatform: String
    public var toPlatform: String
    public var found: Bool
    public var walkTimeS: Double?
    public var walkDistanceM: Double?
    public var reason: String?
}

/// The platforms at the station nearest a coordinate (from `/station-platforms`).
/// Powers the walk-only door: the platform pickers adapt to the entered station,
/// and `relationId` is the id a subsequent `/walk` between two of these refs uses
/// — so the two calls resolve the same station. `found == false` (with `reason`)
/// when no station sits near the coordinate.
public struct StationPlatformsResponse: Codable, Sendable {
    public var lat: Double
    public var lon: Double
    public var relationId: Int?
    public var station: String?
    public var found: Bool
    public var platforms: [String]
    public var reason: String?

    public init(lat: Double, lon: Double, relationId: Int? = nil, station: String? = nil,
                found: Bool, platforms: [String] = [], reason: String? = nil) {
        self.lat = lat; self.lon = lon; self.relationId = relationId; self.station = station
        self.found = found; self.platforms = platforms; self.reason = reason
    }
}

// MARK: - Full station walk (/station-walk) — mirrors api/schemas.py

/// One destination platform's walk FROM the chosen source platform — a row of the
/// "full station walk" tool. `found == false` (with `reason`, one of core/'s own:
/// `platform_not_found` / `disconnected` / `exceeded_plausibility_bound`) is an
/// honest "these two don't connect", rendered as an unreachable row — never an
/// error.
public struct StationWalkRow: Codable, Hashable, Sendable, Identifiable {
    public var toPlatform: String
    public var found: Bool
    public var walkTimeS: Double?
    public var walkDistanceM: Double?
    public var reason: String?

    /// The destination ref is unique within one response, so it is a stable id.
    public var id: String { toPlatform }

    public init(toPlatform: String, found: Bool, walkTimeS: Double? = nil,
                walkDistanceM: Double? = nil, reason: String? = nil) {
        self.toPlatform = toPlatform; self.found = found
        self.walkTimeS = walkTimeS; self.walkDistanceM = walkDistanceM; self.reason = reason
    }
}

/// Every OTHER platform's walk FROM one source platform at the station nearest a
/// coordinate — the "full station walk" advanced tool (mirrors
/// `api/schemas.StationWalkResponse`). One pathfind per platform, `results` sorted
/// nearest-first (reachable by ascending walk distance, then the unreachable ones).
/// `found == false` (with `reason`) at the top level when no station sits near the
/// coordinate; individual unreachable platforms are `found == false` *rows* inside
/// a `found == true` response. `relationId` is the id a subsequent `/walk` between
/// the source and a chosen row uses, so a tapped row resolves the same station.
public struct StationWalkResponse: Codable, Sendable {
    public var lat: Double
    public var lon: Double
    public var relationId: Int?
    public var station: String?
    public var fromPlatform: String
    public var stepFree: Bool
    public var found: Bool
    public var results: [StationWalkRow]
    public var reason: String?

    public init(lat: Double, lon: Double, relationId: Int? = nil, station: String? = nil,
                fromPlatform: String, stepFree: Bool = false, found: Bool,
                results: [StationWalkRow] = [], reason: String? = nil) {
        self.lat = lat; self.lon = lon; self.relationId = relationId; self.station = station
        self.fromPlatform = fromPlatform; self.stepFree = stepFree; self.found = found
        self.results = results; self.reason = reason
    }
}

// MARK: - Nearest facility (/facilities) — mirrors api/schemas.py

/// One mapped facility (a POI) near a station, ranked by straight-line distance
/// from the station centroid. `category` is the OSM bucket (amenity/shop/…) and
/// `subtype` the specific tag ("toilets", "cafe"), mirroring the `viz_export`
/// details shape. `nearestPlatform`/`walk*` are filled only when a `from_platform`
/// anchor was supplied and a routed walk to the facility's nearest platform
/// resolved — otherwise `distanceM` (straight-line) is the only measure.
public struct Facility: Codable, Hashable, Sendable, Identifiable {
    public var name: String?
    public var category: String
    public var subtype: String?
    public var level: String?
    public var distanceM: Double
    public var lat: Double?
    public var lon: Double?
    public var nearestPlatform: String?
    public var walkTimeS: Double?
    public var walkDistanceM: Double?

    /// Stable identity for `ForEach`: no server id, so key off what's mapped.
    public var id: String { "\(category)/\(subtype ?? "")/\(name ?? "")/\(distanceM)" }

    public init(name: String? = nil, category: String, subtype: String? = nil,
                level: String? = nil, distanceM: Double, lat: Double? = nil, lon: Double? = nil,
                nearestPlatform: String? = nil, walkTimeS: Double? = nil, walkDistanceM: Double? = nil) {
        self.name = name; self.category = category; self.subtype = subtype
        self.level = level; self.distanceM = distanceM; self.lat = lat; self.lon = lon
        self.nearestPlatform = nearestPlatform; self.walkTimeS = walkTimeS; self.walkDistanceM = walkDistanceM
    }
}

/// Facilities of one `category` near the station nearest a coordinate, nearest
/// first (from `/facilities`). `found == true` iff at least one was returned;
/// otherwise `reason` says why: `station_unresolved`, `unsupported_category`,
/// `no_poi_layer` (the POI source isn't available on the host — the honest
/// degradation, mirroring boarding's `no_formation_feed`), or `none_mapped` (the
/// layer is present but this station tags none of that category).
public struct FacilitiesResponse: Codable, Sendable {
    public var lat: Double
    public var lon: Double
    public var relationId: Int?
    public var station: String?
    public var category: String
    public var found: Bool
    public var reason: String?
    public var facilities: [Facility]

    public init(lat: Double, lon: Double, relationId: Int? = nil, station: String? = nil,
                category: String, found: Bool, reason: String? = nil, facilities: [Facility] = []) {
        self.lat = lat; self.lon = lon; self.relationId = relationId; self.station = station
        self.category = category; self.found = found; self.reason = reason; self.facilities = facilities
    }
}

/// The whole station drawn in 3D with EVERY facility of a category pinned on it —
/// the map-first "walk to nearest" surface (from `/facility-map`). `export` is a
/// browse `viz_export` (all platforms, no single route) with each facility attached
/// as a focus POI; `facilities` is the ranked list in the SAME order as
/// `export.details`, so a tapped pin (`details[i]`) maps straight to its facility
/// (`facilities[i]`). `found == false` (with `reason`) degrades exactly like
/// `FacilitiesResponse` — `no_poi_layer`, `none_mapped`, `too_sparse`, etc. Mirrors
/// `api/schemas.py:FacilityMapResponse`.
public struct FacilityMapResponse: Codable, Sendable {
    public var lat: Double
    public var lon: Double
    public var relationId: Int?
    public var station: String?
    public var category: String
    public var found: Bool
    public var reason: String?
    public var export: VizExport?
    public var facilities: [Facility]

    public init(lat: Double, lon: Double, relationId: Int? = nil, station: String? = nil,
                category: String, found: Bool, reason: String? = nil,
                export: VizExport? = nil, facilities: [Facility] = []) {
        self.lat = lat; self.lon = lon; self.relationId = relationId; self.station = station
        self.category = category; self.found = found; self.reason = reason
        self.export = export; self.facilities = facilities
    }
}

// MARK: - Station connectivity health (/station-health) — mirrors api/schemas.py

/// One platform pair that doesn't plainly connect — `kind` is "stitchable" (a
/// route exists only once synthetic stitch bridges are enabled) or "island" (no
/// route found either way). Surfaced as a few worked examples of a station's
/// disconnects (mirrors `api/schemas.py:StationHealthPair`).
public struct StationHealthPair: Codable, Hashable, Sendable {
    public var fromPlatform: String
    public var toPlatform: String
    public var kind: String

    public init(fromPlatform: String, toPlatform: String, kind: String) {
        self.fromPlatform = fromPlatform; self.toPlatform = toPlatform; self.kind = kind
    }
}

/// A single station's platform-connectivity breakdown (from `/station-health`) —
/// the Map-health tool's per-station query. Every unordered platform pair is
/// bucketed connected / stitchable / island by two pathfinder passes; the counts
/// are pair counts and the `*Pct` are their share of the pairs evaluated.
/// `sampled` is true when a very large station was down-sampled to bound the work
/// (`platformCount` still reports the true total). `found == false` (with
/// `reason`) when no station sits near the coordinate. Mirrors
/// `api/schemas.py:StationHealthResponse`.
public struct StationHealthResponse: Codable, Sendable {
    public var lat: Double
    public var lon: Double
    public var relationId: Int?
    public var station: String?
    public var found: Bool
    public var platformCount: Int
    public var connected: Int
    public var stitchable: Int
    public var island: Int
    public var connectedPct: Double
    public var stitchablePct: Double
    public var islandPct: Double
    public var sampled: Bool
    public var examples: [StationHealthPair]
    public var reason: String?

    public init(lat: Double, lon: Double, relationId: Int? = nil, station: String? = nil,
                found: Bool, platformCount: Int = 0, connected: Int = 0, stitchable: Int = 0,
                island: Int = 0, connectedPct: Double = 0, stitchablePct: Double = 0,
                islandPct: Double = 0, sampled: Bool = false,
                examples: [StationHealthPair] = [], reason: String? = nil) {
        self.lat = lat; self.lon = lon; self.relationId = relationId; self.station = station
        self.found = found; self.platformCount = platformCount
        self.connected = connected; self.stitchable = stitchable; self.island = island
        self.connectedPct = connectedPct; self.stitchablePct = stitchablePct; self.islandPct = islandPct
        self.sampled = sampled; self.examples = examples; self.reason = reason
    }

    /// Total platform pairs actually evaluated (0 when fewer than two platforms).
    public var pairCount: Int { connected + stitchable + island }
}

// MARK: - Walk geometry delivery (/walk, /walks) — mirrors api/schemas.py

/// Identifies one platform-to-platform walk. These three fields are exactly what
/// a `Transfer` already carries, so a client forwards them verbatim. `stepFree`
/// requests the elevator-free variant (a different route, hence a different walk
/// time than the verdict's).
/// A facility to draw in a walk's geometry — the "walk to nearest" focus. Its
/// coordinate is already known to the client (it came from `/facilities`), so the
/// server projects it into the export's local frame and appends it to the
/// `details` layer flagged `focus`, with no planet extract needed. `level` is the
/// OSM `level` tag as a string ("0", "−1") when known, so the POI is lifted to the
/// right floor in the 3D model. Mirrors `api/schemas.py:WalkPOI`.
public struct WalkPOI: Codable, Hashable, Sendable {
    public var lat: Double
    public var lon: Double
    public var name: String?
    public var category: String
    public var subtype: String?
    public var level: String?

    public init(lat: Double, lon: Double, name: String? = nil, category: String,
                subtype: String? = nil, level: String? = nil) {
        self.lat = lat; self.lon = lon; self.name = name
        self.category = category; self.subtype = subtype; self.level = level
    }
}

public struct WalkKey: Codable, Hashable, Sendable {
    public var relationId: Int
    public var fromPlatform: String
    public var toPlatform: String
    public var stepFree: Bool
    /// Station-map (browse) mode: include every platform at the station, not just
    /// the walked corridor's. A distinct cache key from the plain walk.
    public var allPlatforms: Bool
    /// A facility to draw beside the walk (the "walk to nearest" door). When set,
    /// the fetched geometry carries this POI in its `details` layer as the focus.
    /// Part of the key, so a walk-with-facility caches apart from the plain walk.
    public var poi: WalkPOI?

    public init(relationId: Int, fromPlatform: String, toPlatform: String,
                stepFree: Bool = false, allPlatforms: Bool = false, poi: WalkPOI? = nil) {
        self.relationId = relationId
        self.fromPlatform = fromPlatform
        self.toPlatform = toPlatform
        self.stepFree = stepFree
        self.allPlatforms = allPlatforms
        self.poi = poi
    }

    /// Build the key straight from a `Transfer` (nil if it never resolved a
    /// relation/platforms, in which case there is no walk to fetch).
    public init?(transfer: Transfer, stepFree: Bool = false) {
        guard let rel = transfer.relationId,
              let from = transfer.arrivalPlatform,
              let to = transfer.departurePlatform else { return nil }
        self.init(relationId: rel, fromPlatform: from, toPlatform: to, stepFree: stepFree)
    }
}

/// Where to be on the arriving train so you step off nearest the onward walk —
/// mirrors `api/boarding.py` / `schemas.BoardingGuidance`. Derived from the same
/// resolved path as the geometry: the multi-source search already picks the point
/// on the arrival platform closest (in walk time) to the departure platform, so
/// that point *is* the optimal step-off.
///
/// `stepoffFraction` is oriented so **0 is the platform end farthest from the
/// departure side and 1 the nearest** — a larger fraction means "board further
/// toward your connection". `timeSavedS` is the extra platform-walking the far end
/// would cost (an upper bound, hence "up to"). `coach` is filled only when a live
/// formation feed resolves it; from a generic host that feed is geo-blocked, so
/// `reason` is usually `no_formation_feed` — position is known, the coach isn't.
public struct BoardingGuidance: Codable, Hashable, Sendable {
    public var arrivalPlatform: String
    public var departurePlatform: String
    public var platformLengthM: Double
    public var stepoffOffsetM: Double
    public var stepoffFraction: Double
    public var timeSavedS: Double
    public var significance: String       // "high" | "some" | "low"
    public var coach: String?
    public var formationSource: String?
    public var reason: String?

    /// The typed significance, defaulting to `.low` for an unknown string.
    public var band: Significance { Significance(rawValue: significance) ?? .low }

    /// True once a real along-platform position was measured (a clean edge).
    public var hasPosition: Bool { platformLengthM > 0 }

    public enum Significance: String, Sendable {
        case high, some, low
    }
}

/// One walk's geometry, or a reason it couldn't be built. `export` is the full
/// `core/viz_export.py` document (typed as `VizExport`). Two failure levels:
/// `ok == false` means no export could be produced at all; `ok == true` with
/// `export.path.found == false` means the export exists but the platforms don't
/// connect (a real, drawable "no route" state). `boarding` is the step-off
/// guidance for a found walk (nil on the sample tier / a coarse platform).
public struct WalkResult: Codable, Sendable {
    public var relationId: Int
    public var fromPlatform: String
    public var toPlatform: String
    public var stepFree: Bool
    public var ok: Bool
    public var reason: String?
    public var export: VizExport?
    public var boarding: BoardingGuidance?

    public init(relationId: Int, fromPlatform: String, toPlatform: String, stepFree: Bool,
                ok: Bool, reason: String? = nil, export: VizExport? = nil,
                boarding: BoardingGuidance? = nil) {
        self.relationId = relationId
        self.fromPlatform = fromPlatform
        self.toPlatform = toPlatform
        self.stepFree = stepFree
        self.ok = ok
        self.reason = reason
        self.export = export
        self.boarding = boarding
    }
}

public struct WalksRequest: Codable, Sendable {
    public var keys: [WalkKey]
    public init(keys: [WalkKey]) { self.keys = keys }
}

public struct WalksResponse: Codable, Sendable {
    public var walks: [WalkResult]
}
