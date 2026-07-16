import Foundation
import Observation
import TransfrCore

/// The single source of truth for a planning session (DESIGN.md §13.1). Holds the
/// query, the fetched options, the selection, and load state. Screens observe it;
/// navigation is value-driven through `path` so `NavigationStack` gives the
/// prototype's forward/back slide for free.
@MainActor
@Observable
public final class TripModel {
    // Query
    public var origin: String = "Hamburg Hbf"
    public var destination: String = "Stuttgart Hbf"
    public var departure: Date = TripModel.defaultDeparture()

    // Current-location origin (design/route-maps.html §3). `usingCurrentLocation`
    // drives the "From" field's location treatment and the route map's live origin
    // dot; `originUserEdited` guards the first-launch default from clobbering a
    // station the user has typed.
    public var usingCurrentLocation = false
    public var originUserEdited = false
    /// The station the user's coordinate resolved to (shown under "Current location").
    public var locationName: String?

    // Results
    public var response: JourneysResponse?
    public var selected: Journey?

    // Load state
    public enum Load: Equatable { case idle, loading, loaded, failed(String) }
    public private(set) var load: Load = .idle

    // Navigation stack (value routes)
    public var path: [Route] = []

    /// The verdict-free walk-only lookup (§6.9): a resolved station + two of its
    /// platforms, set when "Show walk" is tapped and read by `WalkLookupView` to
    /// fetch that walk's geometry. `relationId == 0` marks the sample tier (no
    /// real geometry), so the lookup falls back to a schematic.
    public struct WalkLookup: Hashable, Sendable {
        public var station: String
        public var relationId: Int
        public var fromPlatform: String
        public var toPlatform: String

        public init(station: String, relationId: Int, fromPlatform: String, toPlatform: String) {
            self.station = station; self.relationId = relationId
            self.fromPlatform = fromPlatform; self.toPlatform = toPlatform
        }
    }
    public var walkLookup: WalkLookup?

    private let repo: JourneyRepository

    public init(repository: JourneyRepository) {
        self.repo = repository
    }

    public var journeys: [Journey] { response?.journeys ?? [] }

    /// Plan the current query and land on the results screen. Fails soft — the
    /// error is surfaced in `load` for the UI, never thrown to a crash.
    public func plan() async {
        load = .loading
        do {
            let resp = try await repo.journeys(from: origin, to: destination, when: departure)
            response = resp
            load = .loaded
            path = [.results]
        } catch {
            load = .failed(message(for: error))
        }
    }

    /// Resolve a coordinate to the nearest station and set it as the origin. Keeps
    /// `origin` a plain station name (what `/journeys` queries), but flags the trip
    /// as location-sourced so the field and the route map show the "you" treatment.
    /// Returns false (leaving state untouched) if nothing resolved near the point.
    @discardableResult
    public func useCurrentLocation(lat: Double, lon: Double) async -> Bool {
        guard let resp = try? await repo.platforms(lat: lat, lon: lon),
              resp.found, let name = resp.station, !name.isEmpty else { return false }
        origin = name
        locationName = name
        usingCurrentLocation = true
        return true
    }

    /// Station autocomplete for the input fields. Fails soft to an empty list —
    /// suggestions are progressive enhancement, never a blocking error.
    public func stations(matching query: String) async -> [StationSuggestion] {
        (try? await repo.stations(query: query)) ?? []
    }

    /// Resolve a picked station's coordinate to its platforms (+ relation id) so
    /// the walk-only door's pickers can adapt to it. Fails soft to nil — the UI
    /// then keeps its free-form platform fields.
    public func stationPlatforms(lat: Double, lon: Double) async -> StationPlatformsResponse? {
        try? await repo.platforms(lat: lat, lon: lon)
    }

    public func select(_ journey: Journey) {
        selected = journey
        path.append(.journey)
    }

    public func swapEndpoints() { swap(&origin, &destination) }

    /// Transfers of the selected journey (the carousel/walk source).
    public var transfers: [Transfer] { selected?.transfers ?? [] }

    /// Fetch one transfer's drawable geometry (the keystone `viz_export`). Returns
    /// nil when unavailable (e.g. the sample tier), so the walk screen keeps its
    /// schematic. Non-throwing on purpose — geometry is progressive enhancement.
    public func walk(for key: WalkKey) async -> WalkResult? {
        try? await repo.walk(for: key)
    }

    private func message(for error: Error) -> String {
        if let e = error as? TransfrClientError {
            switch e {
            case .badStatus(404): return "We couldn't find one of those stations."
            case .badStatus(let c): return "The service returned an error (\(c))."
            case .badURL: return "Bad request."
            }
        }
        if error is URLError { return "No connection to the planning service." }
        return error.localizedDescription
    }

    private static func defaultDeparture() -> Date {
        // The prototype opens at "Today · 08:34"; anchor to that wall-clock time.
        var c = Calendar.current.dateComponents([.year, .month, .day], from: Date())
        c.hour = 8; c.minute = 34
        return Calendar.current.date(from: c) ?? Date()
    }
}

/// Value routes for the `NavigationStack`. Adding a destination is a new case —
/// the §13.12 reason a navigation rethink stays cheap. Covers all 15 prototype
/// screens (DESIGN.md §3): the journey spine, the walk/AR pair, the verdict-free
/// walk-lookup door, and the Settings → Advanced/Attributions subtrees.
public enum Route: Hashable {
    // Journey spine
    case results
    case journey
    case carousel(startIndex: Int)
    case walk(transferIndex: Int)
    case ar(transferIndex: Int)
    case live
    // Second Plan door (§6.9)
    case walkLookup
    // Settings subtree
    case settings
    case attributions
    // Advanced hub (§6.10)
    case advanced
    case stationWalk
    case nearestFacility
    case mapHealth
    case offlineRegions
}
