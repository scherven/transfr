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

    // Results
    public var response: JourneysResponse?
    public var selected: Journey?

    // Load state
    public enum Load: Equatable { case idle, loading, loaded, failed(String) }
    public private(set) var load: Load = .idle

    // Navigation stack (value routes)
    public var path: [Route] = []

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
/// the §13.12 reason a navigation rethink stays cheap.
public enum Route: Hashable {
    case results
    case journey
    case carousel(startIndex: Int)
    case walk(transferIndex: Int)
    case settings
}
