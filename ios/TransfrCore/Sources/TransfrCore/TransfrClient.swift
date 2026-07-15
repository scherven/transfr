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

    public init(baseURL: URL, transport: Transport = URLSession.shared) {
        self.baseURL = baseURL
        self.transport = transport
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

    /// GET /transfer?lat=&lon=&from=&to= — the debug single-station platform walk.
    public func transfer(lat: Double, lon: Double,
                         from: String, to: String) async throws -> PlatformWalkResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("transfer"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
            URLQueryItem(name: "from", value: from),
            URLQueryItem(name: "to", value: to),
        ]
        return try await get(comps?.url)
    }

    private func get<T: Decodable>(_ url: URL?) async throws -> T {
        guard let url else { throw TransfrClientError.badURL }
        let (data, response) = try await transport.data(for: URLRequest(url: url))
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransfrClientError.badStatus(http.statusCode)
        }
        return try TransfrJSON.decode(T.self, from: data)
    }
}
