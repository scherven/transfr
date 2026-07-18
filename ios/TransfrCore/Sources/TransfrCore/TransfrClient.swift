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
    /// `assess: false` returns the itineraries instantly with `pending` transfers
    /// (no server-side pathfinding), to be filled in via `assess(_:)` — the
    /// progressive load. Defaults true (the full product path).
    public func journeys(from: String, to: String, when: String? = nil,
                         max: Int? = nil, assess: Bool = true,
                         noElevators: Bool = false) async throws -> JourneysResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("journeys"),
                                  resolvingAgainstBaseURL: false)
        var items = [URLQueryItem(name: "from", value: from), URLQueryItem(name: "to", value: to)]
        if let when { items.append(URLQueryItem(name: "time", value: when)) }
        if let max { items.append(URLQueryItem(name: "max", value: String(max))) }
        if !assess { items.append(URLQueryItem(name: "assess", value: "false")) }
        // The routing profile: routes every transfer's VERDICT without lifts, not
        // just the drawn walk. Omitted when false, so an ordinary search is
        // byte-for-byte the request it always was.
        if noElevators { items.append(URLQueryItem(name: "no_elevators", value: "true")) }
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

    /// GET /station-platform-markers?lat=&lon= — the feed's platform-number labels
    /// (the ones OSM lacks) for the station nearest a coordinate, each at its real
    /// coordinate. Mirrors the `stationPlatforms(...)` fetch. `found == false` (with
    /// `reason`: `no_platform_labels` when the harvested overlay isn't on this host,
    /// `station_unresolved` when no harvested station is near) — never an error.
    public func stationPlatformMarkers(lat: Double, lon: Double) async throws -> StationPlatformMarkersResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("station-platform-markers"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
        ]
        return try await get(comps?.url)
    }

    /// GET /station-walk?lat=&lon=&from_platform=&step_free= — the "full station
    /// walk": from one source platform, the real walk to every other platform at
    /// the station nearest a coordinate, one pathfind each, sorted nearest-first.
    /// Powers the Advanced tool of the same name. (Server param is `from_platform`;
    /// see `api/main.py:get_station_walk`.)
    public func stationWalk(lat: Double, lon: Double, fromPlatform: String,
                            stepFree: Bool = false) async throws -> StationWalkResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("station-walk"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
            URLQueryItem(name: "from_platform", value: fromPlatform),
            URLQueryItem(name: "step_free", value: stepFree ? "true" : "false"),
        ]
        return try await get(comps?.url)
    }

    /// GET /facilities?lat=&lon=&category=&from_platform= — facilities (POIs) of a
    /// category near the station nearest a coordinate, nearest first. Degrades to
    /// `found == false` with a typed `reason` (e.g. `no_poi_layer`) when the POI
    /// source isn't available on the host. `from` optionally anchors a routed walk
    /// to each facility's nearest platform.
    public func facilities(lat: Double, lon: Double, category: String,
                           from: String? = nil) async throws -> FacilitiesResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("facilities"),
                                  resolvingAgainstBaseURL: false)
        var items = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
            URLQueryItem(name: "category", value: category),
        ]
        if let from { items.append(URLQueryItem(name: "from_platform", value: from)) }
        comps?.queryItems = items
        return try await get(comps?.url)
    }

    /// GET /facility-map?lat=&lon=&category= — the whole station in 3D with every
    /// facility of a category pinned (a browse `viz_export` + the ranked list, in one
    /// round trip). Degrades to `found == false` with a typed `reason` like
    /// `facilities(...)` when the POI layer isn't available on the host.
    public func facilityMap(lat: Double, lon: Double, category: String) async throws -> FacilityMapResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("facility-map"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
            URLQueryItem(name: "category", value: category),
        ]
        return try await get(comps?.url)
    }

    /// GET /station-health?lat=&lon= — one station's platform-connectivity
    /// breakdown (connected / stitchable / island over every platform pair), for
    /// the Map-health tool's per-station query. Resolves the station nearest the
    /// coordinate the same way `stationPlatforms(...)` does.
    public func stationHealth(lat: Double, lon: Double) async throws -> StationHealthResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("station-health"),
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
    /// `poi` (the "walk to nearest" facility) rides along as `poi_*` params so the
    /// server draws it into the geometry's details layer as the focus.
    public func walk(relationId: Int, from: String, to: String,
                     stepFree: Bool = false, allPlatforms: Bool = false,
                     poi: WalkPOI? = nil) async throws -> WalkResult {
        var comps = URLComponents(url: baseURL.appendingPathComponent("walk"),
                                  resolvingAgainstBaseURL: false)
        var items = [
            URLQueryItem(name: "relation_id", value: String(relationId)),
            URLQueryItem(name: "from_platform", value: from),
            URLQueryItem(name: "to_platform", value: to),
            URLQueryItem(name: "step_free", value: stepFree ? "true" : "false"),
            URLQueryItem(name: "all_platforms", value: allPlatforms ? "true" : "false"),
        ]
        if let poi {
            items.append(URLQueryItem(name: "poi_lat", value: String(poi.lat)))
            items.append(URLQueryItem(name: "poi_lon", value: String(poi.lon)))
            items.append(URLQueryItem(name: "poi_category", value: poi.category))
            if let s = poi.subtype { items.append(URLQueryItem(name: "poi_subtype", value: s)) }
            if let n = poi.name { items.append(URLQueryItem(name: "poi_name", value: n)) }
            if let l = poi.level { items.append(URLQueryItem(name: "poi_level", value: l)) }
        }
        comps?.queryItems = items
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

    /// POST /assess — assess a batch of changes of train (the streamed verdicts a
    /// fast `/journeys?assess=false` deferred). Called with one interchange per
    /// request, fired concurrently, it fills a journey's verdicts in as each
    /// pathfind returns.
    public func assess(_ interchanges: [AssessInterchange],
                       noElevators: Bool = false) async throws -> AssessResponse {
        let url = baseURL.appendingPathComponent("assess")
        var req = authorized(url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try TransfrJSON.encoder.encode(
            AssessRequest(interchanges: interchanges, noElevators: noElevators))
        let (data, response) = try await transport.data(for: req)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw TransfrClientError.badStatus(http.statusCode)
        }
        return try TransfrJSON.decode(AssessResponse.self, from: data)
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
