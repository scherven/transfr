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
    // Query. Both ends start genuinely empty — the input fields carry real
    // placeholders showing what an entry looks like, rather than shipping an
    // example query that reads as something the user chose.
    public var origin: String = ""
    public var destination: String = ""
    public var departure: Date = TripModel.defaultDeparture()

    // Current-location origin (agents/design/route-maps.html §3). `usingCurrentLocation`
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

    /// Supersedes an in-flight `/journeys`. Nav is instant (#17), so the user can
    /// be back on the input screen searching again while the first fetch is still
    /// out; without this the slower, older response could land last and overwrite
    /// the newer search's results. This is NOT walk bookkeeping — verdict loading
    /// is still carried by `verdict == pending` alone (see above).
    private var planGeneration = 0

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
        /// The facility this walk leads to (the "walk to nearest" door), drawn into
        /// the geometry beside the destination platform. `nil` for a plain
        /// platform-to-platform lookup.
        public var poi: WalkPOI?
        /// Browse the whole station (every platform, no single route) rather than a
        /// two-platform walk — how a tapped facility is shown, so it reads on the
        /// full station map with the POI pinned regardless of which platform it's by.
        public var browse: Bool

        public init(station: String, relationId: Int, fromPlatform: String, toPlatform: String,
                    poi: WalkPOI? = nil, browse: Bool = false) {
            self.station = station; self.relationId = relationId
            self.fromPlatform = fromPlatform; self.toPlatform = toPlatform
            self.poi = poi; self.browse = browse
        }
    }
    public var walkLookup: WalkLookup?

    private let repo: JourneyRepository
    /// The routing profile the current `response` was searched under, captured at
    /// `plan()` time. The streamed verdicts must use the SAME profile as the
    /// search that produced the itineraries — reading the live setting per
    /// `/assess` call instead would let a mid-stream toggle flip leave one
    /// itinerary holding a mix of with-lift and lift-free verdicts.
    private var plannedAvoidElevators = false
    /// Expands a pasted short link (HTTP redirect) before parsing. Injectable so a
    /// test can supply a stub instead of hitting the network.
    private let linkExpander: LinkExpanding
    /// Past searches, recorded on every successful plan for one-tap reuse (#38).
    /// Optional so a test (or a headless model) can skip persistence — the recording
    /// is a no-op when absent.
    private let recents: RecentSearchStore?

    public init(repository: JourneyRepository,
                linkExpander: LinkExpanding = URLSessionLinkExpander(),
                recents: RecentSearchStore? = nil) {
        self.repo = repository
        self.linkExpander = linkExpander
        self.recents = recents
    }

    public var journeys: [Journey] { response?.journeys ?? [] }

    /// Plan the current query and land on the results screen. Fails soft — the
    /// error is surfaced in `load` for the UI, never thrown to a crash.
    ///
    /// Progressive, in two steps (#17). The nav happens FIRST — before the
    /// `/journeys` await — so the tap is answered instantly and the search is
    /// shown happening on the results screen instead of freezing the CTA. Then:
    ///
    ///   A  `load == .loading`, `response == nil`  — ResultsView shows skeletons.
    ///   B  the itineraries land (`assess=false`, so transfers are `pending`).
    ///   C  `streamVerdicts` fills each real verdict in behind the list.
    ///
    /// `response` is cleared up front on purpose: once nav is instant, a previous
    /// search's journeys would otherwise sit on screen — under the new query's
    /// title — reading as results for a search that hasn't happened yet.
    public func plan(avoidElevators: Bool = false) async {
        planGeneration += 1
        let generation = planGeneration
        load = .loading
        verdictTask?.cancel()
        selectedIndex = nil
        response = nil
        path = [.results]
        do {
            let resp = try await repo.journeys(from: origin, to: destination, when: departure, assess: false, noElevators: avoidElevators)
            guard generation == planGeneration else { return }
            response = resp
            load = .loaded
            streamVerdicts()
        } catch {
            guard generation == planGeneration else { return }
            load = .failed(message(for: error))
        }
    }

    /// Plan a trip from a pasted maps / rail link (the "Paste link" door). Expands
    /// a short link if needed (HTTP redirect), parses the endpoints with the pure
    /// `RouteLinkParser`, and converges on the same `plan()` path as typed input.
    /// Fails soft — a bad or unsupported link surfaces a message on the CTA, never
    /// a crash.
    public func planFromLink(_ raw: String, avoidElevators: Bool = false) async {
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
        await plan(avoidElevators: avoidElevators)
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

    /// The whole station in 3D with every facility of a category pinned (the
    /// map-first Nearest-facility surface). Fails soft to nil on a transport error;
    /// a resolved-but-empty or POI-layer-absent result comes back as a
    /// `FacilityMapResponse` with `found == false` and a typed `reason`.
    public func facilityMap(lat: Double, lon: Double, category: String) async -> FacilityMapResponse? {
        try? await repo.facilityMap(lat: lat, lon: lon, category: category)
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
        // Batch per journey, NOT per transfer. The heavy part of `/assess` is
        // building the station SearchContext, and the server shares one resolve
        // cache across a request's interchanges — so sending a whole itinerary's
        // changes in ONE request both fires far fewer requests (≤ one per journey
        // instead of one per transfer) and lets that cache actually hit. The old
        // per-transfer fan-out was what stranded walks on big journey sets (e.g.
        // Hamburg→Salzburg, 12+ transfers): a dozen concurrent requests hit
        // URLSession's per-host cap, each rebuilt the context cold, and any that
        // timed out left their transfer `pending` forever. Each item keeps its
        // transfer index so the batched reply splices back into the right row.
        var work: [(j: Int, items: [(t: Int, ic: AssessInterchange)])] = []
        for (j, journey) in resp.journeys.enumerated() {
            let ics = Self.interchanges(of: journey)
            let items = journey.transfers.enumerated().compactMap {
                (t, transfer) -> (t: Int, ic: AssessInterchange)? in
                guard transfer.verdictKind.isPending, t < ics.count else { return nil }
                return (t, ics[t])
            }
            if !items.isEmpty { work.append((j, items)) }
        }
        guard !work.isEmpty else { return }
        verdictTask = Task { [weak self] in await self?.runVerdictStream(work) }
    }

    private func runVerdictStream(_ work: [(j: Int, items: [(t: Int, ic: AssessInterchange)])]) async {
        let repo = self.repo
        let noElevators = plannedAvoidElevators   // the search's profile, not the live setting
        await withTaskGroup(of: (j: Int, indices: [Int], transfers: [Transfer]?).self) { group in
            for (j, items) in work {
                group.addTask {
                    let assessed = await Self.assessWithRetry(
                        items.map(\.ic), repo: repo, noElevators: noElevators)
                    return (j, items.map(\.t), assessed)
                }
            }
            for await (j, indices, assessed) in group {
                if Task.isCancelled { return }
                guard var resp = response, j < resp.journeys.count else { continue }
                for (k, t) in indices.enumerated() where t < resp.journeys[j].transfers.count {
                    if let assessed {
                        resp.journeys[j].transfers[t] = assessed[k]
                    } else {
                        // Every `/assess` attempt for this journey failed. Give each
                        // still-pending row a TERMINAL verdict in place — `unknown`,
                        // never a fabricated feasible — so it stops loading, the
                        // journey rolls up honestly, and PreparingWalksView can reach
                        // `allSettled`. `pending` is purely transient (Verdict.swift):
                        // nothing else would ever clear it, so without this a single
                        // failed request strands that walk forever.
                        resp.journeys[j].transfers[t].verdict = "unknown"
                        resp.journeys[j].transfers[t].reason = Self.assessFailedReason
                    }
                }
                // Re-roll the journey verdict now this itinerary's transfers are known.
                resp.journeys[j].verdict = resp.journeys[j].transfers.map(\.verdictKind).rolledUp().raw
                response = resp
            }
        }
    }

    /// Reason attached to a transfer whose verdict couldn't be fetched after
    /// retries — distinct from the server's own `unknown` reasons (e.g.
    /// `no_platform_data`) so the UI can say "couldn't check" rather than falsely
    /// "no platform data here" (see `JourneyView`).
    static let assessFailedReason = "assessment_unavailable"

    /// Assess one journey's interchanges in a single request, retrying briefly
    /// before giving up. Returns one transfer per interchange (in request order) on
    /// success, or `nil` if every attempt failed — the caller then writes a terminal
    /// verdict so no transfer is left `pending` forever. `nonisolated static`, with
    /// `repo` passed in, so it runs on the task-group child off the main actor (like
    /// the original per-transfer call did) rather than hopping back for each request.
    private nonisolated static func assessWithRetry(_ ics: [AssessInterchange], repo: JourneyRepository,
                                                    noElevators: Bool) async -> [Transfer]? {
        let maxAttempts = 3
        for attempt in 0..<maxAttempts {
            if Task.isCancelled { return nil }
            // Require one transfer back per interchange: a short/empty reply is as
            // useless as a thrown error (it would splice a nil), so treat it as a
            // failed attempt and retry rather than stranding the row.
            if let transfers = try? await repo.assess(ics, noElevators: noElevators),
               transfers.count == ics.count {
                return transfers
            }
            if attempt < maxAttempts - 1 {
                // 0.4s, 0.8s — brief backoff to ride out a transient blip or a
                // moment of server contention without hammering.
                try? await Task.sleep(nanoseconds: 400_000_000 << UInt64(attempt))
            }
        }
        return nil
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
    case stationMap
    case stationWalk
    case nearestFacility
    case mapHealth
    case offlineRegions
}
