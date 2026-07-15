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

// MARK: - Walk geometry delivery (/walk, /walks) — mirrors api/schemas.py

/// Identifies one platform-to-platform walk. These three fields are exactly what
/// a `Transfer` already carries, so a client forwards them verbatim. `stepFree`
/// requests the elevator-free variant (a different route, hence a different walk
/// time than the verdict's).
public struct WalkKey: Codable, Hashable, Sendable {
    public var relationId: Int
    public var fromPlatform: String
    public var toPlatform: String
    public var stepFree: Bool

    public init(relationId: Int, fromPlatform: String, toPlatform: String, stepFree: Bool = false) {
        self.relationId = relationId
        self.fromPlatform = fromPlatform
        self.toPlatform = toPlatform
        self.stepFree = stepFree
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

/// One walk's geometry, or a reason it couldn't be built. `export` is the full
/// `core/viz_export.py` document (typed as `VizExport`). Two failure levels:
/// `ok == false` means no export could be produced at all; `ok == true` with
/// `export.path.found == false` means the export exists but the platforms don't
/// connect (a real, drawable "no route" state).
public struct WalkResult: Codable, Sendable {
    public var relationId: Int
    public var fromPlatform: String
    public var toPlatform: String
    public var stepFree: Bool
    public var ok: Bool
    public var reason: String?
    public var export: VizExport?

    public init(relationId: Int, fromPlatform: String, toPlatform: String, stepFree: Bool,
                ok: Bool, reason: String? = nil, export: VizExport? = nil) {
        self.relationId = relationId
        self.fromPlatform = fromPlatform
        self.toPlatform = toPlatform
        self.stepFree = stepFree
        self.ok = ok
        self.reason = reason
        self.export = export
    }
}

public struct WalksRequest: Codable, Sendable {
    public var keys: [WalkKey]
    public init(keys: [WalkKey]) { self.keys = keys }
}

public struct WalksResponse: Codable, Sendable {
    public var walks: [WalkResult]
}
