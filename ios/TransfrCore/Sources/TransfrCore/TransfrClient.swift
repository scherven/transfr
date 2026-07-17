import Foundation

/// Thin async client over the FastAPI service (`api/main.py`). Near-term the app
/// is a thin client — `core/` stays server-side and the phone consumes these
/// contracts (DESIGN.md §13.2). Networking is abstracted behind `Transport` so
/// the client is unit-testable offline (inject a stub) and so an offline cache
/// can wrap it later.
public protocol Transport: Sendable {
    func data(for request: URLRequest) async throws -> (Data, URLResponse)
}

extension URLSession: Transport {}

public enum TransfrClientError: Error, Sendable {
    case badStatus(Int)
    case badURL
}

public struct TransfrClient: Sendable {
    public var baseURL: URL
    public var transport: Transport
    /// Shared secret sent as the `X-API-Key` header on every request. `nil` for an
    /// unsecured local dev server; set it for the deployed (tunnelled) service,
    /// which returns 401 without it (see `api/security.py`).
    public var apiKey: String?

    public init(baseURL: URL, transport: Transport = URLSession.shared, apiKey: String? = nil) {
        self.baseURL = baseURL
        self.transport = transport
        self.apiKey = apiKey
    }

    /// Wrap a URL in a request carrying the auth header (if configured).
    private func authorized(_ url: URL) -> URLRequest {
        var req = URLRequest(url: url)
        if let apiKey { req.setValue(apiKey, forHTTPHeaderField: "X-API-Key") }
        return req
    }

    /// GET /journeys?from=&to=&time=&max= — the product endpoint. `time` is an
    /// ISO-8601 departure time (the server calls the query param `time`, not
    /// `when`; see `api/main.py:get_journeys`).
    public func journeys(from: String, to: String, when: String? = nil,
                         max: Int? = nil) async throws -> JourneysResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("journeys"),
                                  resolvingAgainstBaseURL: false)
        var items = [URLQueryItem(name: "from", value: from), URLQueryItem(name: "to", value: to)]
        if let when { items.append(URLQueryItem(name: "time", value: when)) }
        if let max { items.append(URLQueryItem(name: "max", value: String(max))) }
        comps?.queryItems = items
        return try await get(comps?.url)
    }

    /// GET /stations?q= — autocomplete. (The app also ships `stations.csv` for
    /// offline suggestions; this is the online refresh path.)
    public func stations(query: String) async throws -> [StationSuggestion] {
        var comps = URLComponents(url: baseURL.appendingPathComponent("stations"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "q", value: query)]
        return try await get(comps?.url)
    }

    /// GET /transfer?lat=&lon=&from_platform=&to_platform= — the debug
    /// single-station platform walk. (Server param names are `from_platform` /
    /// `to_platform`; see `api/main.py:get_transfer`.)
    public func transfer(lat: Double, lon: Double,
                         from: String, to: String) async throws -> PlatformWalkResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("transfer"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
            URLQueryItem(name: "from_platform", value: from),
            URLQueryItem(name: "to_platform", value: to),
        ]
        return try await get(comps?.url)
    }

    /// GET /station-platforms?lat=&lon= — the platforms (and the relation_id a
    /// subsequent `walk(...)` uses) at the station nearest a coordinate. Powers
    /// the walk-only door: the platform pickers adapt to the entered station.
    public func stationPlatforms(lat: Double, lon: Double) async throws -> StationPlatformsResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("station-platforms"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
        ]
        return try await get(comps?.url)
    }

    /// GET /walk?relation_id=&from_platform=&to_platform=&step_free= — one
    /// transfer's drawable walk geometry (the `viz_export` document). Keyed by the
    /// triple a `Transfer` already carries, so callers forward them verbatim.
    public func walk(relationId: Int, from: String, to: String,
                     stepFree: Bool = false, allPlatforms: Bool = false) async throws -> WalkResult {
        var comps = URLComponents(url: baseURL.appendingPathComponent("walk"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [
            URLQueryItem(name: "relation_id", value: String(relationId)),
            URLQueryItem(name: "from_platform", value: from),
            URLQueryItem(name: "to_platform", value: to),
            URLQueryItem(name: "step_free", value: stepFree ? "true" : "false"),
            URLQueryItem(name: "all_platforms", value: allPlatforms ? "true" : "false"),
        ]
        return try await get(comps?.url)
    }

    /// POST /walks — batch prefetch of a planned journey's walks in one round
    /// trip so its transfers cache to the device together (DESIGN.md §13.9).
    public func walks(_ keys: [WalkKey]) async throws -> WalksResponse {
        let url = baseURL.appendingPathComponent("walks")
        var req = authorized(url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try TransfrJSON.encoder.encode(WalksRequest(keys: keys))
        let (data, response) = try await transport.data(for: req)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransfrClientError.badStatus(http.statusCode)
        }
        return try TransfrJSON.decode(WalksResponse.self, from: data)
    }

    private func get<T: Decodable>(_ url: URL?) async throws -> T {
        guard let url else { throw TransfrClientError.badURL }
        let (data, response) = try await transport.data(for: authorized(url))
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransfrClientError.badStatus(http.statusCode)
        }
        return try TransfrJSON.decode(T.self, from: data)
    }
}
