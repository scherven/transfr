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

    public func journeys(from: String, to: String, when: Date?) async throws -> JourneysResponse {
        try? await Task.sleep(for: latency)
        let data = try Self.bundled("sample_journeys")
        return try TransfrJSON.decode(JourneysResponse.self, from: data)
    }

    public func stations(query: String) async throws -> [StationSuggestion] {
        try? await Task.sleep(for: .milliseconds(120))
        let q = query.lowercased()
        return Self.stationSeed.filter { $0.name.lowercased().contains(q) }
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
