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

    public func stationWalk(lat: Double, lon: Double, fromPlatform: String, stepFree: Bool) async throws -> StationWalkResponse {
        try? await Task.sleep(for: .milliseconds(180))
        // Reuse the same nearest-seed platform set the walk-only door uses, then
        // synthesize a plausible nearest-first walk to each OTHER platform so the
        // tool populates offline. relationId 0 marks the sample tier (no real
        // geometry), so a tapped row's WalkLookup falls back to its schematic —
        // exactly like platforms(...) / walk(for:).
        let p = try await platforms(lat: lat, lon: lon)
        let others = p.platforms.filter { $0 != fromPlatform }
        let rows = others.enumerated().map { i, ref in
            StationWalkRow(toPlatform: ref, found: true,
                           walkTimeS: Double(20 + i * 14),
                           walkDistanceM: Double(15 + i * 18))
        }
        return StationWalkResponse(lat: lat, lon: lon, relationId: 0, station: p.station,
                                   fromPlatform: fromPlatform, stepFree: stepFree,
                                   found: p.found, results: rows)
    }

    public func walk(for key: WalkKey) async throws -> WalkResult {
        // No bundled geometry in the sample tier — the walk screen falls back to
        // its schematic rendering (see WalkView). ok == false is an honest "no
        // export", distinct from an export whose path.found == false.
        WalkResult(relationId: key.relationId, fromPlatform: key.fromPlatform,
                   toPlatform: key.toPlatform, stepFree: key.stepFree,
                   ok: false, reason: "sample_no_geometry", export: nil)
    }

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
