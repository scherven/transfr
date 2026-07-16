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

    // MARK: Progressive walk load
    //
    // `/journeys` returns the verdict spine fast; each transfer's drawable
    // geometry (`/walk`) is a separate, heavier fetch. So once a journey is
    // chosen and its timeline shows, we STREAM those walks in the background into
    // `walkCache` — the journey screen never blocks on them, and by the time the
    // user opens a transfer the drawing is usually already there. `walkPrefetch`
    // exposes the per-transfer progress the timeline strip and the transition
    // screen render live.

    /// One transfer's geometry-load state, in the selected journey's order.
    public enum WalkLoad: Sendable, Equatable {
        case pending       // not started
        case loading       // /walk in flight
        case ready         // geometry cached and drawable
        case unavailable   // resolved, but no geometry (sample tier / FR·IT·ES / data gap)
    }

    public struct WalkPrefetchState: Sendable, Equatable {
        /// One entry per transfer of the selected journey.
        public var statuses: [WalkLoad] = []

        public var total: Int { statuses.count }
        /// Transfers whose fetch has settled (ready OR knowably unavailable).
        public var settled: Int { statuses.filter { $0 == .ready || $0 == .unavailable }.count }
        public var readyCount: Int { statuses.filter { $0 == .ready }.count }
        /// Still actively streaming something.
        public var inFlight: Bool { statuses.contains(.loading) }
        /// Seeded and nothing left to wait on.
        public var isComplete: Bool { !statuses.isEmpty && !statuses.contains(where: { $0 == .loading || $0 == .pending }) }
    }

    public private(set) var walkPrefetch = WalkPrefetchState()

    /// Deterministic geometry cache, shared by the prefetch and every on-demand
    /// `walk(for:)`. Keyed by the exact `WalkKey` (so a step-free variant is
    /// distinct), it makes a prefetched walk open instantly.
    private var walkCache: [WalkKey: WalkResult] = [:]
    private var prefetchTask: Task<Void, Never>?
    private var prefetchIdentity: (journeyId: String, stepFree: Bool)?

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
    /// Expands a pasted short link (HTTP redirect) before parsing. Injectable so a
    /// test can supply a stub instead of hitting the network.
    private let linkExpander: LinkExpanding

    public init(repository: JourneyRepository,
                linkExpander: LinkExpanding = URLSessionLinkExpander()) {
        self.repo = repository
        self.linkExpander = linkExpander
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

    /// Plan a trip from a pasted maps / rail link (the "Paste link" door). Expands
    /// a short link if needed (HTTP redirect), parses the endpoints with the pure
    /// `RouteLinkParser`, and converges on the same `plan()` path as typed input.
    /// Fails soft — a bad or unsupported link surfaces a message on the CTA, never
    /// a crash.
    public func planFromLink(_ raw: String) async {
        load = .loading
        let parsed: RouteLinkParser.ParsedRouteLink
        do {
            parsed = try await resolveLink(raw)
        } catch {
            load = .failed(linkMessage(for: error)); return
        }

        // Names first; fall back to reverse-resolving a coordinate (a shared
        // pin-drop) the way "current location" does, so a link that carried only
        // points still plans.
        let from = await endpointName(parsed.from, coordinate: parsed.fromCoordinate)
        let to   = await endpointName(parsed.to, coordinate: parsed.toCoordinate)

        guard let from, let to else {
            load = .failed("That link didn't include both a start and destination.")
            return
        }

        origin = from
        destination = to
        usingCurrentLocation = false
        originUserEdited = true
        if let dep = parsed.departure { departure = dep }
        await plan()
    }

    /// Expand (only if a short link) then parse. Pure parsing lives in
    /// `RouteLinkParser`; the redirect is the sole network hop.
    private func resolveLink(_ raw: String) async throws -> RouteLinkParser.ParsedRouteLink {
        do {
            return try RouteLinkParser.parse(raw)
        } catch RouteLinkParser.ParseError.shortLinkNeedsExpansion(let url) {
            let expanded = try await linkExpander.expand(url)
            return try RouteLinkParser.parse(expanded.absoluteString)
        }
    }

    /// A queryable station name for one end: the parsed name if present, else the
    /// nearest station to the parsed coordinate (reusing the `/station-platforms`
    /// reverse lookup), else nil.
    private func endpointName(_ name: String?,
                              coordinate: RouteLinkParser.Coordinate?) async -> String? {
        if let name, !name.trimmingCharacters(in: .whitespaces).isEmpty { return name }
        guard let coordinate else { return nil }
        let resp = try? await repo.platforms(lat: coordinate.latitude, lon: coordinate.longitude)
        if let resp, resp.found, let station = resp.station, !station.isEmpty { return station }
        return nil
    }

    private func linkMessage(for error: Error) -> String {
        if let e = error as? RouteLinkParser.ParseError {
            switch e {
            case .notAURL:                 return "That doesn't look like a link. Paste a Maps or bahn.de link."
            case .unrecognizedProvider:    return "Unsupported link. Use Google Maps, Apple Maps, or bahn.de."
            case .noEndpoints:             return "Couldn't read a start or destination from that link."
            case .shortLinkNeedsExpansion: return "Couldn't open that short link. Check your connection."
            }
        }
        if error is URLError { return "Couldn't open that link. Check your connection." }
        return "Couldn't read that link."
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
        // Reset progressive-load state for the new journey; the timeline's
        // .task will seed a fresh prefetch (see prefetchWalks).
        prefetchTask?.cancel()
        prefetchTask = nil
        prefetchIdentity = nil
        walkPrefetch = WalkPrefetchState()
        path.append(.journey)
    }

    public func swapEndpoints() { swap(&origin, &destination) }

    /// Transfers of the selected journey (the carousel/walk source).
    public var transfers: [Transfer] { selected?.transfers ?? [] }

    /// Fetch one transfer's drawable geometry (the keystone `viz_export`). Serves
    /// the prefetch cache first, so a walk the timeline already streamed opens
    /// instantly; otherwise fetches and caches it. Returns nil when unavailable
    /// (e.g. the sample tier), so the walk screen keeps its schematic.
    /// Non-throwing on purpose — geometry is progressive enhancement.
    public func walk(for key: WalkKey) async -> WalkResult? {
        if let cached = walkCache[key] { return cached }
        let result = try? await repo.walk(for: key)
        if let result { walkCache[key] = result }
        return result
    }

    /// Start (or resume) streaming the selected journey's transfer walks into the
    /// cache, updating `walkPrefetch` as each settles. Idempotent per
    /// journey+variant, and detached from any view: the task lives on the model,
    /// so navigating deeper into the walks doesn't cancel the stream. Call it when
    /// the timeline appears (and when `stepFree` flips — a different route).
    public func prefetchWalks(stepFree: Bool) {
        guard let journey = selected, let jid = journey.id else { return }
        // Same journey+variant already seeded -> leave the in-flight/finished run.
        if let id = prefetchIdentity, id.journeyId == jid, id.stepFree == stepFree,
           !walkPrefetch.statuses.isEmpty { return }
        prefetchIdentity = (jid, stepFree)
        prefetchTask?.cancel()

        let transfers = journey.transfers
        var statuses = [WalkLoad](repeating: .unavailable, count: transfers.count)
        var todo: [(index: Int, key: WalkKey)] = []
        for (i, t) in transfers.enumerated() {
            guard let key = WalkKey(transfer: t, stepFree: stepFree) else { continue } // stays .unavailable
            if let cached = walkCache[key] {
                statuses[i] = cached.ok ? .ready : .unavailable
            } else {
                statuses[i] = .loading
                todo.append((i, key))
            }
        }
        walkPrefetch = WalkPrefetchState(statuses: statuses)
        guard !todo.isEmpty else { return }
        prefetchTask = Task { [weak self] in await self?.runPrefetch(todo) }
    }

    private func runPrefetch(_ todo: [(index: Int, key: WalkKey)]) async {
        let repo = self.repo   // capture the Sendable repo, not self, for the children
        await withTaskGroup(of: (Int, WalkKey, WalkResult?).self) { group in
            for (idx, key) in todo {
                group.addTask { (idx, key, try? await repo.walk(for: key)) }
            }
            for await (idx, key, result) in group {
                if Task.isCancelled { return }
                if let result { walkCache[key] = result }
                if idx < walkPrefetch.statuses.count {
                    walkPrefetch.statuses[idx] = (result?.ok == true) ? .ready : .unavailable
                }
            }
        }
    }

    /// The geometry-load status of the transfer at `index` (in the selected
    /// journey), for the timeline strip and the transition screen.
    public func walkStatus(at index: Int) -> WalkLoad {
        walkPrefetch.statuses[safe: index] ?? .pending
    }

    /// Open the transfers, starting at `startIndex`. Goes straight to the walk
    /// carousel when that transfer's geometry has settled (the common, fast case);
    /// otherwise routes through the transition screen so the wait is visible and
    /// the drawing is ready when it lands.
    public func openTransfers(startIndex: Int) {
        let waiting: Bool
        switch walkStatus(at: startIndex) {
        case .pending, .loading: waiting = startIndex < transfers.count  // real, still-streaming change
        case .ready, .unavailable: waiting = false
        }
        path.append(waiting ? .preparingWalks(startIndex: startIndex)
                            : .carousel(startIndex: startIndex))
    }

    /// Advance from the transition screen into the walk carousel, replacing the
    /// transition in the stack so Back returns to the timeline, not to it.
    public func proceedToWalks(startIndex: Int) {
        if case .preparingWalks = path.last { path.removeLast() }
        path.append(.carousel(startIndex: startIndex))
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
    case preparingWalks(startIndex: Int)
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
    case stationMap
    case stationWalk
    case nearestFacility
    case mapHealth
    case offlineRegions
}
