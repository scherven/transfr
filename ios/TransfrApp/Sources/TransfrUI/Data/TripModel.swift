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

    // Results. `response` is the live source of truth: `/journeys?assess=false`
    // fills it with `pending` transfers, then `streamVerdicts` replaces each with
    // its real assessment in place, so every screen reading it updates as the
    // verdicts land. `selected` is an INDEX into it (not a copy) for the same reason
    // — a streamed verdict must reach the open timeline.
    public var response: JourneysResponse?
    public var selectedIndex: Int?
    public var selected: Journey? { selectedIndex.flatMap { response?.journeys[safe: $0] } }

    // Load state
    public enum Load: Equatable { case idle, loading, loaded, failed(String) }
    public private(set) var load: Load = .idle

    // MARK: Progressive walk load
    //
    // The heavy part of `/journeys` is `enrich`: pathfinding every change of
    // train for its walk time + verdict. We skip it (`assess=false`) so the
    // itinerary list shows instantly, then STREAM each transfer's real verdict in
    // via `/assess` — updating `response` in place. A transfer's own
    // `verdict == pending` IS its loading state; no parallel bookkeeping needed.
    private var verdictTask: Task<Void, Never>?

    /// On-demand geometry cache for the walk detail screens (a separate, later
    /// layer than the verdicts). Keyed by the exact `WalkKey`, so a walk opened
    /// once — or step-free vs not — is served instantly next time.
    private var walkCache: [WalkKey: WalkResult] = [:]

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
    ///
    /// Progressive: fetches the itineraries with `assess=false` so the list shows
    /// the instant the search returns (no waiting on the transfer pathfinding),
    /// then streams the real verdicts in behind it.
    public func plan() async {
        load = .loading
        verdictTask?.cancel()
        selectedIndex = nil
        do {
            let resp = try await repo.journeys(from: origin, to: destination, when: departure, assess: false)
            response = resp
            load = .loaded
            path = [.results]
            streamVerdicts()
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

    /// The "full station walk" from one source platform: the real walk to every
    /// other platform at the resolved station, nearest-first (the §6.10 Advanced
    /// tool). Fails soft to nil — the tool then shows its degraded/empty state
    /// rather than throwing.
    public func stationWalk(lat: Double, lon: Double, fromPlatform: String, stepFree: Bool) async -> StationWalkResponse? {
        try? await repo.stationWalk(lat: lat, lon: lon, fromPlatform: fromPlatform, stepFree: stepFree)
    }

    /// Facilities of a category near a coordinate (the Nearest-facility tool).
    /// Fails soft to nil only on a network/transport error; a resolved-but-empty
    /// or POI-layer-absent result comes back as a `FacilitiesResponse` with
    /// `found == false` and a typed `reason` so the view can say so honestly.
    public func facilities(lat: Double, lon: Double, category: String) async -> FacilitiesResponse? {
        try? await repo.facilities(lat: lat, lon: lon, category: category)
    }

    /// Resolve a coordinate to its station's platform-connectivity health (the
    /// Map-health per-station query). Fails soft to nil — the diagnostic panel then
    /// shows nothing rather than surfacing an error.
    public func stationHealth(lat: Double, lon: Double) async -> StationHealthResponse? {
        try? await repo.stationHealth(lat: lat, lon: lon)
    }

    /// Pick a journey. Lands on its timeline straight away when its verdicts are
    /// already in; otherwise routes through the transition screen so the still-
    /// streaming verdicts are shown filling in, then advances to the timeline.
    public func select(_ journey: Journey) {
        selectedIndex = response?.journeys.firstIndex(where: { $0.id == journey.id })
        path.append(selectedHasPendingWalks ? .preparingWalks : .journey)
    }

    /// The selected journey still has at least one transfer whose verdict is
    /// streaming in.
    public var selectedHasPendingWalks: Bool {
        selected?.transfers.contains { $0.verdictKind.isPending } ?? false
    }

    /// Advance from the transition screen to the timeline, replacing the
    /// transition in the stack so Back returns to the results list, not to it.
    public func proceedToTimeline() {
        if case .preparingWalks = path.last { path.removeLast() }
        path.append(.journey)
    }

    public func swapEndpoints() { swap(&origin, &destination) }

    /// Transfers of the selected journey (the carousel/walk source).
    public var transfers: [Transfer] { selected?.transfers ?? [] }

    // MARK: Verdict streaming

    /// Stream every still-`pending` transfer's real verdict in, across all
    /// journeys, updating `response` in place as each lands. Fired right after a
    /// fast `assess=false` search, so the itinerary cards and the timeline fill in
    /// their verdicts behind the list the user is already looking at. Lives on the
    /// model (not a view), so it keeps running as the user navigates.
    public func streamVerdicts() {
        guard let resp = response else { return }
        verdictTask?.cancel()
        var work: [(j: Int, t: Int, ic: AssessInterchange)] = []
        for (j, journey) in resp.journeys.enumerated() {
            let ics = Self.interchanges(of: journey)
            for (t, transfer) in journey.transfers.enumerated() where transfer.verdictKind.isPending {
                if t < ics.count { work.append((j, t, ics[t])) }
            }
        }
        guard !work.isEmpty else { return }
        verdictTask = Task { [weak self] in await self?.runVerdictStream(work) }
    }

    private func runVerdictStream(_ work: [(j: Int, t: Int, ic: AssessInterchange)]) async {
        let repo = self.repo
        await withTaskGroup(of: (Int, Int, Transfer?).self) { group in
            for item in work {
                let (j, t, ic) = (item.j, item.t, item.ic)
                group.addTask { (j, t, (try? await repo.assess([ic]))?.first) }
            }
            for await (j, t, transfer) in group {
                if Task.isCancelled { return }
                guard let transfer, var resp = response,
                      j < resp.journeys.count, t < resp.journeys[j].transfers.count else { continue }
                resp.journeys[j].transfers[t] = transfer
                // Re-roll the journey verdict now one transfer is known.
                resp.journeys[j].verdict = resp.journeys[j].transfers.map(\.verdictKind).rolledUp().raw
                response = resp
            }
        }
    }

    /// The changes of train of a journey, as the interchange requests `/assess`
    /// takes — mirrors the server's `transitous.interchanges`: consecutive transit
    /// legs (walking legs dropped), each adjacent pair one change. Aligned with
    /// `journey.transfers` by index.
    static func interchanges(of journey: Journey) -> [AssessInterchange] {
        let transit = journey.legs.filter { $0.mode != "walking" }
        return zip(transit, transit.dropFirst()).map { arriving, departing in
            AssessInterchange(
                atStation: arriving.destination.name,
                arrLat: arriving.destination.latitude, arrLon: arriving.destination.longitude,
                arrPlatform: arriving.arrivalPlatform, arrTime: arriving.arrival,
                depLat: departing.origin.latitude, depLon: departing.origin.longitude,
                depPlatform: departing.departurePlatform, depTime: departing.departure
            )
        }
    }

    /// Fetch one transfer's drawable geometry (the keystone `viz_export`). Serves
    /// the on-demand cache first, so a walk opened once (or already prefetched)
    /// returns instantly; otherwise fetches and caches it. Returns nil when
    /// unavailable (e.g. the sample tier), so the walk screen keeps its schematic.
    /// Non-throwing on purpose — geometry is progressive enhancement.
    public func walk(for key: WalkKey) async -> WalkResult? {
        if let cached = walkCache[key] { return cached }
        let result = try? await repo.walk(for: key)
        if let result { walkCache[key] = result }
        return result
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
    case preparingWalks
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
