import Foundation
import TransfrCore

/// Offline stand-in for the API. Serves the bundled `sample_journeys.json` (the
/// exact `api/schemas.py` shape — decoded through the same `TransfrJSON` coder the
/// live path uses, so it exercises the real contract) plus a small station list.
/// A short artificial delay makes loading states visible in the running app.
public struct SampleRepository: JourneyRepository {
    public var latency: Duration

    public init(latency: Duration = .milliseconds(450)) {
        self.latency = latency
    }

    public func journeys(from: String, to: String, when: Date?, assess: Bool) async throws -> JourneysResponse {
        try? await Task.sleep(for: latency)
        let data = try Self.bundled("sample_journeys")
        // `assess` is ignored: the offline tier is instant and its verdicts are
        // baked into the bundle, so there's nothing to defer and stream. The
        // journeys come back already assessed, so plan() finds no pending
        // transfers and skips the streaming path entirely.
        return try TransfrJSON.decode(JourneysResponse.self, from: data)
    }

    public func assess(_ interchanges: [AssessInterchange]) async throws -> [Transfer] {
        // The offline tier can't pathfind; echo each interchange as unknown. Not
        // reached in the normal flow (journeys() already returns assessed sample
        // data), but kept honest for the contract.
        interchanges.map {
            Transfer(atStation: $0.atStation, arrivalPlatform: $0.arrPlatform,
                     departurePlatform: $0.depPlatform, verdict: "unknown",
                     reason: "sample_no_assessment")
        }
    }

    public func stations(query: String) async throws -> [StationSuggestion] {
        try? await Task.sleep(for: .milliseconds(120))
        let q = query.lowercased()
        return Self.stationSeed.filter { $0.name.lowercased().contains(q) }
    }

    public func platforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse {
        try? await Task.sleep(for: .milliseconds(140))
        // Nearest seed station by rough coordinate distance → a plausible platform
        // set so the walk-only pickers populate offline. (No DB in the sample tier,
        // so relationId is 0 and walk(for:) returns ok == false; the lookup then
        // falls back to its schematic.)
        let nearest = Self.stationSeed.min {
            hypot(($0.latitude ?? 0) - lat, ($0.longitude ?? 0) - lon)
            < hypot(($1.latitude ?? 0) - lat, ($1.longitude ?? 0) - lon)
        }
        let name = nearest?.name ?? ""
        let refs = name.hasPrefix("Berlin")
            ? ["1", "2", "3", "4", "5", "6", "7", "8", "11", "12", "13", "14", "15", "16"]
            : ["1", "2", "3", "4", "5", "6", "7", "8"]
        return StationPlatformsResponse(lat: lat, lon: lon, relationId: 0, station: name,
                                        found: true, platforms: refs)
    }

    public func stationHealth(lat: Double, lon: Double) async throws -> StationHealthResponse {
        try? await Task.sleep(for: .milliseconds(160))
        // No DB offline, so synthesize a plausible, mostly-connected breakdown from
        // the nearest seed station's platform count — enough for the Map-health
        // query panel to render its bar + examples without the live service.
        let nearest = Self.stationSeed.min {
            hypot(($0.latitude ?? 0) - lat, ($0.longitude ?? 0) - lon)
            < hypot(($1.latitude ?? 0) - lat, ($1.longitude ?? 0) - lon)
        }
        let name = nearest?.name ?? ""
        let platforms = name.hasPrefix("Berlin") ? 14 : 8
        let total = platforms * (platforms - 1) / 2
        let stitchable = total >= 6 ? 2 : 0
        let island = total >= 10 ? 1 : 0
        let connected = total - stitchable - island
        func pct(_ n: Int) -> Double { total > 0 ? (Double(n) / Double(total) * 1000).rounded() / 10 : 0 }
        let examples = [
            StationHealthPair(fromPlatform: "1", toPlatform: "2", kind: "stitchable"),
            StationHealthPair(fromPlatform: "9", toPlatform: "12", kind: "island"),
        ]
        return StationHealthResponse(
            lat: lat, lon: lon, relationId: 0, station: name, found: true,
            platformCount: platforms, connected: connected, stitchable: stitchable, island: island,
            connectedPct: pct(connected), stitchablePct: pct(stitchable), islandPct: pct(island),
            sampled: false, examples: examples)
    }

    public func walk(for key: WalkKey) async throws -> WalkResult {
        // No bundled geometry in the sample tier — the walk screen falls back to
        // its schematic rendering (see WalkView). ok == false is an honest "no
        // export", distinct from an export whose path.found == false.
        WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                   toPlatform: key.toPlatform, stepFree: key.stepFree,
                   ok: false, reason: "sample_no_geometry", export: nil)
    }

    public func facilities(lat: Double, lon: Double, category: String) async throws -> FacilitiesResponse {
        try? await Task.sleep(for: .milliseconds(200))
        let nearest = Self.stationSeed.min {
            hypot(($0.latitude ?? 0) - lat, ($0.longitude ?? 0) - lon)
            < hypot(($1.latitude ?? 0) - lat, ($1.longitude ?? 0) - lon)
        }
        let key = category.trimmingCharacters(in: .whitespaces).lowercased()
        // A small synthesized set so the offline view populates — the same
        // response shape the live `/facilities` returns, nearest-first. Categories
        // with no sample degrade to `none_mapped` (the honest "none here" state).
        guard let rows = Self.facilitySeed[key], !rows.isEmpty else {
            return FacilitiesResponse(lat: lat, lon: lon, relationId: 0, station: nearest?.name,
                                      category: category, found: false, reason: "none_mapped")
        }
        return FacilitiesResponse(lat: lat, lon: lon, relationId: 0, station: nearest?.name,
                                  category: category, found: true, facilities: rows)
    }

    /// Synthesized facilities per canonical category (nearest-first), so the
    /// offline Nearest-facility screen has real content to render. The first row
    /// carries a routed walk from a platform to mirror the "routed from a platform"
    /// story; the rest are straight-line only.
    static let facilitySeed: [String: [Facility]] = [
        "toilets": [
            Facility(name: "WC Center — main concourse", category: "amenity", subtype: "toilets",
                     level: "0", distanceM: 38, nearestPlatform: "1", walkTimeS: 33, walkDistanceM: 38),
            Facility(name: "rail&fresh — upper Stadtbahn", category: "amenity", subtype: "toilets",
                     level: "2", distanceM: 104),
            Facility(name: "Europaplatz entrance", category: "amenity", subtype: "toilets",
                     level: "0", distanceM: 131),
        ],
        "coffee": [
            Facility(name: "Einstein Kaffee", category: "shop", subtype: "coffee",
                     level: "0", distanceM: 22, nearestPlatform: "1", walkTimeS: 19, walkDistanceM: 22),
            Facility(name: "Starbucks — concourse", category: "amenity", subtype: "cafe",
                     level: "0", distanceM: 61),
        ],
        "atm": [
            Facility(name: "Reisebank", category: "amenity", subtype: "atm",
                     level: "0", distanceM: 27, nearestPlatform: "1", walkTimeS: 23, walkDistanceM: 27),
            Facility(name: "Commerzbank ATM", category: "amenity", subtype: "atm",
                     level: "-1", distanceM: 88),
        ],
        "food": [
            Facility(name: "Point — bakery", category: "amenity", subtype: "fast_food",
                     level: "0", distanceM: 30, nearestPlatform: "1", walkTimeS: 26, walkDistanceM: 30),
            Facility(name: "Vapiano", category: "amenity", subtype: "restaurant", level: "1", distanceM: 95),
        ],
        "tickets": [
            Facility(name: "DB Reisezentrum", category: "shop", subtype: "ticket",
                     level: "0", distanceM: 44, nearestPlatform: "1", walkTimeS: 38, walkDistanceM: 44),
        ],
        "shops": [
            Facility(name: "REWE To Go", category: "shop", subtype: "convenience",
                     level: "0", distanceM: 25, nearestPlatform: "1", walkTimeS: 21, walkDistanceM: 25),
            Facility(name: "Tabak- & Whiskyhaus", category: "shop", subtype: "alcohol", level: "0", distanceM: 40),
        ],
        "pharmacy": [
            Facility(name: "easyApotheke", category: "amenity", subtype: "pharmacy",
                     level: "0", distanceM: 52, nearestPlatform: "1", walkTimeS: 45, walkDistanceM: 52),
        ],
        "taxi": [
            Facility(name: "Taxi rank — Europaplatz", category: "amenity", subtype: "taxi",
                     level: "0", distanceM: 140, nearestPlatform: "1", walkTimeS: 120, walkDistanceM: 140),
        ],
    ]

    static func bundled(_ name: String) throws -> Data {
        guard let url = Bundle.module.url(forResource: name, withExtension: "json") else {
            throw RepositoryError.notAvailable("bundled \(name).json")
        }
        return try Data(contentsOf: url)
    }

    /// A handful of stations so autocomplete is real offline. Decoded so it goes
    /// through the same contract as `/stations`.
    static let stationSeed: [StationSuggestion] = {
        let json = """
        [
          {"name":"Hamburg Hbf","country":"DE","latitude":53.5528,"longitude":10.0067},
          {"name":"Stuttgart Hbf","country":"DE","latitude":48.7838,"longitude":9.1815},
          {"name":"Berlin Hbf","country":"DE","latitude":52.5251,"longitude":13.3694},
          {"name":"München Hbf","country":"DE","latitude":48.1402,"longitude":11.5586},
          {"name":"Frankfurt (Main) Hbf","country":"DE","latitude":50.1070,"longitude":8.6638},
          {"name":"Köln Hbf","country":"DE","latitude":50.9430,"longitude":6.9587},
          {"name":"Mannheim Hbf","country":"DE","latitude":49.4794,"longitude":8.4693},
          {"name":"Basel SBB","country":"CH","latitude":47.5474,"longitude":7.5896},
          {"name":"Göttingen","country":"DE","latitude":51.5366,"longitude":9.9266}
        ]
        """.data(using: .utf8)!
        return (try? TransfrJSON.decode([StationSuggestion].self, from: json)) ?? []
    }()
}
