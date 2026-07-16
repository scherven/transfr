import Foundation

/// Turns a pasted maps / rail link into the `{from, to, departure?}` the planner
/// needs. This is the **pure** half of the "Paste link" feature: string in, value
/// out, no network — so it unit-tests in seconds against fixed link strings.
///
/// The one runtime concern it deliberately does *not* do is expand a short link
/// (`maps.app.goo.gl/…`), which needs an HTTP redirect. `parse` throws
/// `.shortLinkNeedsExpansion` for those so the app layer can follow the redirect
/// (see `LinkExpander.swift`) and re-`parse` the expanded URL — which is again a
/// pure string this file handles.
///
/// Three link families are recognised (see `md/PASTE-LINK.md` for the full survey):
///   • **Google Maps** — `/maps/dir/<from>/<to>/…` path segments, plus the `data=`
///     block's `!1d<lng>!2d<lat>` endpoint coordinates and `!3e<n>` travel mode;
///     also the `?api=1&origin=&destination=` Maps-URLs API form.
///   • **Apple Maps** — `saddr`/`daddr` (+`dirflg`) directions, or a `q`/`ll`/
///     `address` place.
///   • **Deutsche Bahn / bahn.de** — `so`/`zo` names + `hd` departure in the URL
///     *fragment* (new site), or `S`/`Z`/`date`/`time` (old reiseauskunft). Also
///     mines `soid`/`zoid` for the `@O=` name when the plain name is absent.
public enum RouteLinkParser {

    // MARK: - Result

    /// A geographic point recovered from a link (endpoint coords live in the
    /// Google `data=` block or an Apple `ll=`/`saddr` coordinate).
    public struct Coordinate: Equatable, Sendable {
        public var latitude: Double
        public var longitude: Double
        public init(latitude: Double, longitude: Double) {
            self.latitude = latitude; self.longitude = longitude
        }
    }

    public enum TravelMode: String, Equatable, Sendable {
        case transit, driving, walking, cycling, flight, unknown
    }

    public enum Source: String, Equatable, Sendable {
        case googleMaps, appleMaps, deutscheBahn
    }

    /// The recovered trip. `from`/`to` are station-ish **names** (what `/journeys`
    /// queries today); `fromCoordinate`/`toCoordinate` are the raw endpoint points
    /// when the link carried them, so the app can reverse-resolve a name-less
    /// pin-drop through the same `/station-platforms` path "current location" uses.
    public struct ParsedRouteLink: Equatable, Sendable {
        public var source: Source
        public var from: String?
        public var to: String?
        public var fromCoordinate: Coordinate?
        public var toCoordinate: Coordinate?
        public var departure: Date?
        public var travelMode: TravelMode

        public init(source: Source, from: String? = nil, to: String? = nil,
                    fromCoordinate: Coordinate? = nil, toCoordinate: Coordinate? = nil,
                    departure: Date? = nil, travelMode: TravelMode = .unknown) {
            self.source = source; self.from = from; self.to = to
            self.fromCoordinate = fromCoordinate; self.toCoordinate = toCoordinate
            self.departure = departure; self.travelMode = travelMode
        }

        /// A name for each end that `/journeys` can query. When only a coordinate
        /// survived (a pin-drop), that end is `nil` here — the app then reverse-
        /// resolves the coordinate before it can plan.
        public var isPlannable: Bool {
            (from?.isBlank == false) && (to?.isBlank == false)
        }
    }

    public enum ParseError: Error, Equatable, Sendable {
        /// Not a URL we can make sense of at all.
        case notAURL
        /// A recognised short link — expand it (HTTP redirect) and parse the result.
        case shortLinkNeedsExpansion(URL)
        /// A host we don't have a parser for.
        case unrecognizedProvider
        /// A known provider, but neither a name nor a coordinate for either end.
        case noEndpoints
    }

    // MARK: - Entry points

    /// True when `raw` is a short link that must be HTTP-expanded before `parse`
    /// can see the real endpoints. Pure host check — no network.
    public static func isShortLink(_ raw: String) -> Bool {
        guard let url = firstURL(in: raw), let host = url.host?.lowercased() else { return false }
        return isGoogleShortHost(host)
    }

    /// A Google short-link host, matched by exact domain / dotted suffix rather than
    /// substring so an attacker host (`evilgoo.gl.example.com`, `goo.gl.evil.com`)
    /// can't be mistaken for one and expanded over the network.
    private static func isGoogleShortHost(_ host: String) -> Bool {
        host == "goo.gl" || host.hasSuffix(".goo.gl")
            || host == "g.co" || host.hasSuffix(".g.co")
    }

    /// Parse an already-expanded link. Throws rather than returning a half-empty
    /// result so the caller can surface a precise message. Never touches the
    /// network — a short link throws `.shortLinkNeedsExpansion`.
    public static func parse(_ raw: String, timeZone: TimeZone = .current) throws -> ParsedRouteLink {
        try parse(raw, timeZone: timeZone, depth: 0)
    }

    private static func parse(_ raw: String, timeZone: TimeZone, depth: Int) throws -> ParsedRouteLink {
        guard let url = firstURL(in: raw) else { throw ParseError.notAURL }
        guard let host = url.host?.lowercased() else { throw ParseError.notAURL }

        if isGoogleShortHost(host) {
            throw ParseError.shortLinkNeedsExpansion(url)
        }

        // Google consent / redirect interstitial (routine in the EU): the page we
        // actually want is wrapped in a `continue=` (consent) or `q=` (`/url`
        // bounce) param. Unwrap and re-parse it so a Maps link that lands on
        // `consent.google.com` still resolves as Google instead of failing as an
        // unrecognised host. `depth` guards against a pathological gateway loop.
        if depth < 3, let inner = googleGatewayTarget(url, host: host) {
            return try parse(inner, timeZone: timeZone, depth: depth + 1)
        }

        let path = url.path
        let isGoogle = host.contains("google.") && (path.contains("/maps") || path.contains("/dir")
                        || queryValue(url, "origin") != nil || queryValue(url, "destination") != nil
                        || queryValue(url, "saddr") != nil || queryValue(url, "daddr") != nil)
        let isApple = host.contains("maps.apple.com")
                        || (host.contains("apple.com") && path.contains("map"))
        let isDB = host.hasSuffix("bahn.de") || host.contains(".bahn.de")

        let result: ParsedRouteLink
        if isGoogle {
            result = parseGoogle(url)
        } else if isApple {
            result = parseApple(url)
        } else if isDB {
            result = parseDB(url, timeZone: timeZone)
        } else {
            throw ParseError.unrecognizedProvider
        }

        // A provider matched but yielded nothing usable at either end.
        if result.from == nil && result.to == nil
            && result.fromCoordinate == nil && result.toCoordinate == nil {
            throw ParseError.noEndpoints
        }
        return result
    }

    // MARK: - Google Maps

    private static func parseGoogle(_ url: URL) -> ParsedRouteLink {
        var from: String?, to: String?
        var fromCoord: Coordinate?, toCoord: Coordinate?

        // 1) Maps-URLs API form: ?api=1&origin=…&destination=…&travelmode=…
        if let o = queryValue(url, "origin") { assign(o, &from, &fromCoord) }
        if let d = queryValue(url, "destination") { assign(d, &to, &toCoord) }

        // 1b) Classic Maps directions form: ?saddr=…&daddr=…&dirflg=… — what the
        //     Google Maps app's share sheet emits today (endpoints in the query,
        //     not `/dir/` path segments; `saddr`/`daddr` rather than origin/dest).
        if from == nil, fromCoord == nil, let s = queryValue(url, "saddr") { assign(s, &from, &fromCoord) }
        if to == nil, toCoord == nil, let d = queryValue(url, "daddr") { assign(d, &to, &toCoord) }

        // 2) /maps/dir/<from>/<to>[/<waypoints…>]/@…/data=… path form.
        let segments = url.path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        if let dirIdx = segments.firstIndex(of: "dir") {
            // Place segments are the ones after "dir" that aren't the @viewport or a
            // key=value control segment (data=…, am=t). A *leading* empty segment is
            // "here" (current location); a *trailing* one is just a trailing slash.
            var places: [String] = []
            for seg in segments[(dirIdx + 1)...] {
                if seg.hasPrefix("@") { break }
                if seg.contains("=") { continue }          // data=, am=, ...
                places.append(seg)
            }
            while places.count > 1, places.last?.isBlank == true { places.removeLast() }
            if let first = places.first, from == nil, fromCoord == nil {
                let decoded = decode(first)
                if !decoded.isBlank { assign(decoded, &from, &fromCoord) }
            }
            if places.count >= 2, to == nil, toCoord == nil {
                let decoded = decode(places[places.count - 1])
                if !decoded.isBlank { assign(decoded, &to, &toCoord) }
            }
        }

        // 3) Endpoint coordinates from the data block: each `!1d<lng>!2d<lat>` pair,
        //    in origin→destination order. The `@lat,lng` in the path is only the map
        //    viewport, never an endpoint, so we ignore it here.
        let coords = dataBlockCoordinates(url.absoluteString)
        if fromCoord == nil, let c = coords.first { fromCoord = c }
        if toCoord == nil, coords.count >= 2 { toCoord = coords.last }

        return ParsedRouteLink(source: .googleMaps, from: from, to: to,
                               fromCoordinate: fromCoord, toCoordinate: toCoord,
                               departure: nil, travelMode: googleTravelMode(url))
    }

    /// `!1d<lng>!2d<lat>` pairs from the opaque `data=` block, in order.
    private static func dataBlockCoordinates(_ absolute: String) -> [Coordinate] {
        let pattern = "!1d(-?\\d+\\.\\d+)!2d(-?\\d+\\.\\d+)"
        guard let re = try? NSRegularExpression(pattern: pattern) else { return [] }
        let ns = absolute as NSString
        return re.matches(in: absolute, range: NSRange(location: 0, length: ns.length)).compactMap { m in
            guard let lng = Double(ns.substring(with: m.range(at: 1))),
                  let lat = Double(ns.substring(with: m.range(at: 2))) else { return nil }
            return Coordinate(latitude: lat, longitude: lng)   // 1d = lng, 2d = lat
        }
    }

    private static func googleTravelMode(_ url: URL) -> TravelMode {
        if let tm = queryValue(url, "travelmode")?.lowercased() {
            switch tm {
            case "transit": return .transit
            case "driving": return .driving
            case "walking": return .walking
            case "bicycling": return .cycling
            case "flying":  return .flight
            default: break
            }
        }
        // data block `!3e<n>`: 0 drive · 1 cycle · 2 walk · 3 transit · 4 flight.
        if let n = firstMatch("!3e(\\d)", in: url.absoluteString), let code = Int(n) {
            switch code {
            case 0: return .driving
            case 1: return .cycling
            case 2: return .walking
            case 3: return .transit
            case 4: return .flight
            default: break
            }
        }
        // Classic `dirflg` mode letter (may carry a suffix, e.g. `rBSTR` = transit
        // with bus/subway/train sub-options), so key off the leading character.
        switch queryValue(url, "dirflg")?.lowercased().first {
        case "r": return .transit
        case "w": return .walking
        case "d": return .driving
        case "b": return .cycling
        default:  break
        }
        if url.absoluteString.contains("am=t") { return .transit }   // transit map layer
        return .unknown
    }

    // MARK: - Apple Maps

    private static func parseApple(_ url: URL) -> ParsedRouteLink {
        var from: String?, to: String?
        var fromCoord: Coordinate?, toCoord: Coordinate?

        if let s = queryValue(url, "saddr") { assign(s, &from, &fromCoord) }
        if fromCoord == nil, let sll = queryValue(url, "sll").flatMap(coordinate) { fromCoord = sll }

        // Destination: directions `daddr`, else a place `q`/`name`/`address`.
        for key in ["daddr", "destination", "q", "name", "address"] {
            guard let v = queryValue(url, key), !v.isBlank else { continue }
            assign(v, &to, &toCoord)
            if to != nil || toCoord != nil { break }
        }
        // Place-link coordinate (`ll`/`coordinate`) when the destination had no coord.
        if toCoord == nil {
            for key in ["ll", "coordinate"] {
                if let c = queryValue(url, key).flatMap(coordinate) { toCoord = c; break }
            }
        }

        let mode: TravelMode
        switch queryValue(url, "dirflg")?.lowercased() {
        case "r": mode = .transit
        case "w": mode = .walking
        case "d": mode = .driving
        default:  mode = .unknown
        }

        return ParsedRouteLink(source: .appleMaps, from: from, to: to,
                               fromCoordinate: fromCoord, toCoordinate: toCoord,
                               departure: nil, travelMode: mode)
    }

    // MARK: - Deutsche Bahn / bahn.de

    private static func parseDB(_ url: URL, timeZone: TimeZone) -> ParsedRouteLink {
        // The new site carries everything in the fragment; the old reiseauskunft in
        // the query. Merge both so either shape resolves. Use the *raw* (still
        // percent-encoded) strings so we decode values ourselves and structured
        // `soid`/`zoid` ids survive intact.
        let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
        var params = queryPairs(comps?.percentEncodedQuery)
        for (k, v) in queryPairs(comps?.percentEncodedFragment) { params[k] = v }

        // Names: prefer the plain `so`/`zo` (new) or `S`/`Z` (old reiseauskunft);
        // fall back to the `@O=<name>` inside the structured `soid`/`zoid` stop ids.
        let from = params["so"] ?? params["S"] ?? params["s"] ?? nameFromStopId(params["soid"])
        let to   = params["zo"] ?? params["Z"] ?? params["z"] ?? nameFromStopId(params["zoid"])

        var departure: Date?
        if let hd = params["hd"] { departure = parseISOish(hd, timeZone: timeZone) }
        else if let date = params["date"] {           // old reiseauskunft: dd.MM.yy + HH:mm
            departure = parseGermanDate(date, time: params["time"], timeZone: timeZone)
        }

        return ParsedRouteLink(source: .deutscheBahn,
                               from: from?.trimmed.nonBlank, to: to?.trimmed.nonBlank,
                               fromCoordinate: nil, toCoordinate: nil,
                               departure: departure, travelMode: .transit)
    }

    /// `A=1@O=Hamburg Hbf@X=…@L=8002549@…` → "Hamburg Hbf".
    private static func nameFromStopId(_ raw: String?) -> String? {
        guard let raw else { return nil }
        return firstMatch("@O=([^@]+)", in: raw)?.trimmed.nonBlank
    }

    // MARK: - Shared parsing helpers

    /// Interpret a place token as a coordinate if it *is* one, otherwise as a name.
    private static func assign(_ token: String, _ name: inout String?, _ coord: inout Coordinate?) {
        let decoded = decode(token)
        if let c = coordinate(decoded) { coord = c }       // a bare "lat,lng" pin
        else if let n = stationName(from: decoded) { name = n }
    }

    /// The station-ish name from a place string: the first comma-separated
    /// component ("München Hauptbahnhof, Bayerstraße 10A, …" → "München
    /// Hauptbahnhof"), which for a shared station is the station itself.
    private static func stationName(from place: String) -> String? {
        let head = place.split(separator: ",", maxSplits: 1).first.map(String.init) ?? place
        return head.trimmed.nonBlank
    }

    /// Parse "lat,lng" (both plain decimals, plausible ranges) — else nil.
    private static func coordinate(_ s: String) -> Coordinate? {
        let parts = s.split(separator: ",")
        guard parts.count == 2,
              let lat = Double(parts[0].trimmed), let lng = Double(parts[1].trimmed),
              abs(lat) <= 90, abs(lng) <= 180 else { return nil }
        // Reject "10A 30" style — Double() already rejects those; also reject when
        // there is any non-numeric residue by construction (split gave exactly 2).
        return Coordinate(latitude: lat, longitude: lng)
    }

    private static func decode(_ s: String) -> String {
        s.replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? s
    }

    /// First URL in a paste (tolerant of surrounding text / missing scheme).
    private static func firstURL(in raw: String) -> URL? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        var candidate = trimmed
        if let r = trimmed.range(of: "https://") ?? trimmed.range(of: "http://") {
            candidate = String(trimmed[r.lowerBound...])
        }
        // Cut at the first whitespace (a pasted sentence after the URL).
        if let ws = candidate.rangeOfCharacter(from: .whitespacesAndNewlines) {
            candidate = String(candidate[..<ws.lowerBound])
        }
        if !candidate.lowercased().hasPrefix("http") { candidate = "https://" + candidate }
        guard let url = URL(string: candidate), url.host != nil else { return nil }
        return url
    }

    private static func queryValue(_ url: URL, _ name: String) -> String? {
        URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first { $0.name == name }?.value?.nonBlank
    }

    /// The wrapped destination of a Google consent / redirect gateway
    /// (`consent.google.com/…?continue=<real url>`, or a `google.com/url?q=<real
    /// url>` bounce), or nil when `url` isn't one. Only unwraps to a host we already
    /// parse, so it can't be used to smuggle in an arbitrary provider.
    private static func googleGatewayTarget(_ url: URL, host: String) -> String? {
        let isConsent = host.hasPrefix("consent.") && host.contains("google")
        let isRedirect = host.contains("google.") && url.path == "/url"
        guard isConsent || isRedirect else { return nil }
        for key in ["continue", "url", "q", "dest"] {
            guard let raw = queryValue(url, key), let inner = firstURL(in: raw),
                  let innerHost = inner.host?.lowercased() else { continue }
            if innerHost.contains("google.") || isGoogleShortHost(innerHost)
                || innerHost == "bahn.de" || innerHost.hasSuffix(".bahn.de")
                || innerHost == "apple.com" || innerHost.hasSuffix(".apple.com") {
                return inner.absoluteString
            }
        }
        return nil
    }

    /// Split a raw query/fragment string into a dict, percent-decoding values.
    private static func queryPairs(_ raw: String?) -> [String: String] {
        guard let raw, !raw.isEmpty else { return [:] }
        var out: [String: String] = [:]
        for pair in raw.split(separator: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1).map(String.init)
            guard let key = kv.first, !key.isEmpty else { continue }
            let value = kv.count > 1 ? decode(kv[1]) : ""
            out[key] = value
        }
        return out
    }

    private static func firstMatch(_ pattern: String, in text: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
        let ns = text as NSString
        guard let m = re.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)),
              m.numberOfRanges > 1 else { return nil }
        return ns.substring(with: m.range(at: 1))
    }

    // MARK: - Dates

    private static func parseISOish(_ s: String, timeZone: TimeZone) -> Date? {
        let value = s.trimmed
        for fmt in ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd HH:mm"] {
            if let d = dateFormatter(fmt, timeZone).date(from: value) { return d }
        }
        // Trailing zone (…Z / +02:00) → let ISO8601 handle it.
        let iso = ISO8601DateFormatter(); iso.formatOptions = [.withInternetDateTime]
        return iso.date(from: value)
    }

    private static func parseGermanDate(_ date: String, time: String?, timeZone: TimeZone) -> Date? {
        // Pick the year pattern from the actual digit count — a variable-width
        // `yyyy` would otherwise read a 2-digit "26" as the year 0026.
        let d = date.trimmed
        let yearDigits = d.split(separator: ".").last?.count ?? 0
        let dateFmt = "dd.MM." + (yearDigits >= 4 ? "yyyy" : "yy")
        if let time = time?.trimmed, !time.isEmpty,
           let parsed = dateFormatter("\(dateFmt) HH:mm", timeZone).date(from: "\(d) \(time)") {
            return parsed
        }
        return dateFormatter(dateFmt, timeZone).date(from: d)
    }

    private static func dateFormatter(_ format: String, _ timeZone: TimeZone) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = timeZone
        f.dateFormat = format
        f.isLenient = false          // else a 4-digit `yyyy` pattern eats a 2-digit year
        return f
    }
}

// MARK: - Small string conveniences (file-private)

private extension String {
    var isBlank: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
    var nonBlank: String? { isBlank ? nil : self }
}

private extension Substring {
    var trimmed: String { String(self).trimmingCharacters(in: .whitespacesAndNewlines) }
}
