import SwiftUI
import TransfrCore

/// The vector "paper map" (agents/design/route-maps.html, signed off 2026-07-15): every
/// connection drawn *where it actually goes*, over a soft silhouette of Germany.
/// No MapKit — this is a `Canvas` that projects the real leg coordinates so it
/// matches the app's palette, works offline, and costs nothing per thumbnail.
///
/// Semantic rule (shared with the rest of the app): **`accent` is the path and its
/// endpoints; the verdict colours are for the transfer rings only.**
struct RouteMapView: View {
    let journey: Journey
    /// Label shown at the origin when `fromCurrent` (e.g. the resolved station).
    var originName: String? = nil
    /// Draw the origin as a live "current location" dot instead of a ring.
    var fromCurrent: Bool = false
    /// 0…1 position along the route for the live "you" dot; nil on planning maps.
    var youProgress: Double? = nil
    /// Thumbnail mode: no graticule / cities / labels, chunkier marks.
    var mini: Bool = false
    /// Draw station labels (off for thumbnails).
    var showLabels: Bool = true
    /// The journeys this map must stay **comparable** with; defaults to just this
    /// one. Pass the whole result set when several maps sit side by side — the
    /// frame is fitted to their union, so "via Göttingen" and "via Frankfurt" are
    /// drawn at the same scale and can be read against each other. Fitting each
    /// map to its own route would silently rescale them and destroy the comparison.
    var fitTo: [Journey]? = nil

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let proj = MapProjection(fitting: MapStop.stops(for: fitTo ?? [journey])
                                             .map { (lat: $0.lat, lon: $0.lon) })
        Group {
            if youProgress != nil && !reduceMotion {
                TimelineView(.animation) { tl in
                    Canvas { ctx, size in
                        RouteMapRenderer(journey: journey, originName: originName,
                                         fromCurrent: fromCurrent, youProgress: youProgress,
                                         mini: mini, showLabels: showLabels, proj: proj,
                                         pulse: Self.pulse(tl.date))
                            .draw(ctx, size)
                    }
                }
            } else {
                Canvas { ctx, size in
                    RouteMapRenderer(journey: journey, originName: originName,
                                     fromCurrent: fromCurrent, youProgress: youProgress,
                                     mini: mini, showLabels: showLabels, proj: proj, pulse: 0)
                        .draw(ctx, size)
                }
            }
        }
        .accessibilityElement()
        .accessibilityLabel(Self.a11y(journey))
    }

    private static func pulse(_ date: Date) -> Double {
        let period = 2.2
        return (date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: period)) / period
    }

    private static func a11y(_ j: Journey) -> String {
        let names = ([j.legs.first?.origin.name]
                     + j.transfers.map { $0.atStation }
                     + [j.legs.last?.destination.name]).compactMap { $0 }
        return "Route map: " + names.joined(separator: ", ")
    }
}

// MARK: - Renderer

/// The pure drawing pass, factored out so both the animated and static `Canvas`
/// paths share one code path.
private struct RouteMapRenderer {
    let journey: Journey
    let originName: String?
    let fromCurrent: Bool
    let youProgress: Double?
    let mini: Bool
    let showLabels: Bool
    let proj: MapProjection
    let pulse: Double

    func draw(_ context: GraphicsContext, _ size: CGSize) {
        // Sea fills the whole canvas (incl. any letterbox margin); the rest draws in
        // viewBox coordinates under a uniform fit-and-centre transform.
        context.fill(Path(CGRect(origin: .zero, size: size)), with: .color(Theme.mapSea))

        let s = min(size.width / proj.vbw, size.height / proj.vbh)
        guard s > 0 else { return }
        var ctx = context
        ctx.translateBy(x: (size.width - proj.vbw * s) / 2, y: (size.height - proj.vbh * s) / 2)
        ctx.scaleBy(x: s, y: s)

        let stops = MapStop.stops(for: journey)
        let pts = stops.map { proj.point($0.lat, $0.lon) }
        if !mini { drawGraticule(&ctx) }
        drawLand(&ctx)
        if !mini { drawCities(&ctx, stopPts: pts) }
        drawRoute(&ctx, stops: stops, pts: pts)
    }

    // MARK: base layers

    /// Derived from whatever the projection ended up framing, not hardcoded.
    private func drawGraticule(_ ctx: inout GraphicsContext) {
        let dLon = MapProjection.gratStep(proj.lonMax - proj.lonMin)
        var i = (proj.lonMin / dLon).rounded(.up)
        while i * dLon <= proj.lonMax {
            let x = proj.point(0, i * dLon).x
            var p = Path(); p.move(to: CGPoint(x: x, y: 0)); p.addLine(to: CGPoint(x: x, y: proj.vbh))
            ctx.stroke(p, with: .color(Theme.mapGrat), lineWidth: 0.4)
            i += 1
        }
        let dLat = MapProjection.gratStep(proj.latMax - proj.latMin)
        var j = (proj.latMin / dLat).rounded(.up)
        while j * dLat <= proj.latMax {
            let y = proj.point(j * dLat, 0).y
            var p = Path(); p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: proj.vbw, y: y))
            ctx.stroke(p, with: .color(Theme.mapGrat), lineWidth: 0.4)
            j += 1
        }
    }

    /// One filled landmass, then the shore and the borders stroked from arcs that
    /// each appear exactly once. Stroking every country ring instead would draw
    /// each border twice and render it darker than the coast.
    private func drawLand(_ ctx: inout GraphicsContext) {
        let t = proj.rawTransform
        ctx.fill(MapGeo.landRaw.applying(t), with: .color(Theme.mapLand))
        if !mini {
            ctx.stroke(MapGeo.borderRaw.applying(t), with: .color(Theme.mapBorder),
                       style: StrokeStyle(lineWidth: 0.5, lineCap: .round, dash: [1.6, 1.3]))
        }
        ctx.stroke(MapGeo.coastRaw.applying(t), with: .color(Theme.mapCoast),
                   style: StrokeStyle(lineWidth: 0.7, lineCap: .round, lineJoin: .round))
    }

    /// Reference cities, thinned to fit: in-frame, ranked, and never on top of the
    /// route. (This subsumes a hardcoded "hide Hannover" — the clash it worked
    /// around is just a distance test.)
    private func drawCities(_ ctx: inout GraphicsContext, stopPts: [CGPoint]) {
        let exclude = Set(MapStop.stops(for: journey).map { shortName($0.name) })
        var kept: [CGPoint] = []
        for city in MapGeo.shared.cities.sorted(by: { $0.rank < $1.rank }) {
            if kept.count >= 9 { break }
            if exclude.contains(city.name) { continue }
            let p = proj.point(city.lat, city.lon)
            guard p.x >= 3, p.x <= proj.vbw - 3, p.y >= 3, p.y <= proj.vbh - 3 else { continue }
            if stopPts.contains(where: { hypot($0.x - p.x, $0.y - p.y) < 8 }) { continue }
            if kept.contains(where: { hypot($0.x - p.x, $0.y - p.y) < 10 }) { continue }
            kept.append(p)
            ctx.fill(Path(ellipseIn: CGRect(x: p.x - 0.9, y: p.y - 0.9, width: 1.8, height: 1.8)),
                     with: .color(Theme.mapCity))
            let right = p.x <= proj.vbw * 0.62
            ctx.draw(Text(city.name).font(.system(size: 2.7)).foregroundColor(Theme.mapCity),
                     at: CGPoint(x: p.x + (right ? 2.2 : -2.2), y: p.y + 0.9),
                     anchor: right ? .leading : .trailing)
        }
    }

    // MARK: route overlay

    private func drawRoute(_ ctx: inout GraphicsContext, stops: [MapStop], pts: [CGPoint]) {
        guard pts.count >= 2 else { return }

        let line = path(pts)
        // glow
        ctx.stroke(line, with: .color(Theme.accent.opacity(0.16)),
                   style: StrokeStyle(lineWidth: mini ? 4.2 : 3.4, lineCap: .round, lineJoin: .round))

        if let t = youProgress {
            // remainder dashed + dim, done portion solid
            ctx.stroke(line, with: .color(Theme.accent.opacity(0.33)),
                       style: StrokeStyle(lineWidth: mini ? 1.7 : 1.9, lineCap: .round,
                                          lineJoin: .round, dash: [2.4, 2.4]))
            ctx.stroke(path(donePoints(pts, t)), with: .color(Theme.accent),
                       style: StrokeStyle(lineWidth: mini ? 1.9 : 2.1, lineCap: .round, lineJoin: .round))
        } else {
            ctx.stroke(line, with: .color(Theme.accent),
                       style: StrokeStyle(lineWidth: mini ? 1.9 : 2.1, lineCap: .round, lineJoin: .round))
        }

        for (stop, p) in zip(stops, pts) { drawMarker(&ctx, stop: stop, at: p) }
        if youProgress != nil { drawYou(&ctx, at: pointAt(pts, youProgress!)) }
        if showLabels && !mini { drawLabels(&ctx, stops: stops, pts: pts) }
    }

    private func drawMarker(_ ctx: inout GraphicsContext, stop: MapStop, at p: CGPoint) {
        func dot(_ r: CGFloat, _ fill: Color) {
            ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)), with: .color(fill))
        }
        func ring(_ r: CGFloat, _ fill: Color, _ stroke: Color, _ w: CGFloat) {
            let rect = CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)
            ctx.fill(Path(ellipseIn: rect), with: .color(fill))
            ctx.stroke(Path(ellipseIn: rect), with: .color(stroke), lineWidth: w)
        }
        switch stop.kind {
        case .origin:
            if fromCurrent { ring(mini ? 2.6 : 2.4, Theme.accent, .white, mini ? 1 : 1.1) }
            else { ring(mini ? 2.4 : 2.2, .white, Theme.accent, mini ? 1.6 : 1.5) }
        case .destination:
            ring(mini ? 2.6 : 2.5, Theme.accent, .white, 1.1)
            dot(mini ? 1 : 0.9, .white)
        case .transfer(let v):
            ring(mini ? 2.7 : 2.6, Theme.panel, v.color, mini ? 1.5 : 1.6)
            dot(mini ? 1.1 : 1.05, v.color)
        }
    }

    private func drawYou(_ ctx: inout GraphicsContext, at p: CGPoint) {
        let r = 2.6 + 2.4 * pulse
        ctx.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2*r, height: 2*r)),
                 with: .color(Theme.accent.opacity(0.5 * (1 - pulse))))
        ctx.fill(Path(ellipseIn: CGRect(x: p.x - 2.2, y: p.y - 2.2, width: 4.4, height: 4.4)), with: .color(Theme.accent))
        ctx.stroke(Path(ellipseIn: CGRect(x: p.x - 2.2, y: p.y - 2.2, width: 4.4, height: 4.4)), with: .color(.white), lineWidth: 1.1)
    }

    private func drawLabels(_ ctx: inout GraphicsContext, stops: [MapStop], pts: [CGPoint]) {
        let depTime = Fmt.time(journey.legs.first?.departure)
        let arrTime = Fmt.time(journey.legs.last?.arrival)
        for (stop, p) in zip(stops, pts) {
            let right = p.x <= proj.vbw * 0.58
            let x = p.x + (right ? 3.4 : -3.4)
            let anchor: UnitPoint = right ? .leading : .trailing
            let name: String, sub: String, color: Color
            switch stop.kind {
            case .origin:
                name = fromCurrent ? "You're here" : shortName(stop.name)
                sub = fromCurrent ? shortName(originName ?? stop.name) : "dep \(depTime)"
                color = Theme.ink
            case .destination:
                name = shortName(stop.name); sub = "arr \(arrTime)"; color = Theme.ink
            case .transfer(let v):
                name = shortName(stop.name)
                sub = (stop.plat.map { "Pl \($0)" } ?? "") + " · " + v.shortLabel
                color = v.color
            }
            ctx.draw(Text(name).font(.system(size: 3.1, weight: .semibold)).foregroundColor(color),
                     at: CGPoint(x: x, y: p.y - 1.3), anchor: anchor)
            ctx.draw(Text(sub).font(.system(size: 2.5)).foregroundColor(Theme.ink3),
                     at: CGPoint(x: x, y: p.y + 1.7), anchor: anchor)
        }
    }

    // MARK: geometry helpers

    private func path(_ pts: [CGPoint]) -> Path {
        var p = Path(); p.addLines(pts); return p
    }

    private func donePoints(_ pts: [CGPoint], _ t: Double) -> [CGPoint] {
        guard pts.count >= 2 else { return pts }
        let segs = zip(pts, pts.dropFirst()).map { hypot($1.x - $0.x, $1.y - $0.y) }
        let total = segs.reduce(0, +)
        var target = t * total, acc = 0.0, out = [pts[0]]
        for (i, len) in segs.enumerated() {
            if acc + len >= target { out.append(pointAt(pts, t)); break }
            acc += len; out.append(pts[i + 1]); target = t * total
        }
        return out
    }

    private func pointAt(_ pts: [CGPoint], _ t: Double) -> CGPoint {
        guard pts.count >= 2 else { return pts.first ?? .zero }
        let segs = zip(pts, pts.dropFirst()).map { hypot($1.x - $0.x, $1.y - $0.y) }
        let total = segs.reduce(0, +)
        var target = max(0, min(1, t)) * total, acc = 0.0
        for (i, len) in segs.enumerated() {
            if acc + len >= target {
                let f = len > 0 ? (target - acc) / len : 0
                return CGPoint(x: pts[i].x + (pts[i+1].x - pts[i].x) * f,
                               y: pts[i].y + (pts[i+1].y - pts[i].y) * f)
            }
            acc += len
        }
        return pts.last ?? .zero
    }
}

// MARK: - Stops

/// One point on the drawn route, with the meaning that decides its marker.
struct MapStop {
    enum Kind { case origin, transfer(Verdict), destination }
    let lat: Double
    let lon: Double
    let kind: Kind
    let name: String
    let plat: String?

    /// Build the drawable stops from a journey's legs + transfers. Transfer `i`
    /// sits at `legs[i].destination` (== `legs[i+1].origin`). Stops whose `Place`
    /// carries no coordinate are dropped (can't be placed); the route still draws
    /// through whatever remains.
    /// Every stop across a set of journeys — what a shared frame is fitted to.
    static func stops(for journeys: [Journey]) -> [MapStop] {
        journeys.flatMap { stops(for: $0) }
    }

    static func stops(for journey: Journey) -> [MapStop] {
        guard let first = journey.legs.first, let last = journey.legs.last else { return [] }
        var out: [MapStop] = []
        if let la = first.origin.latitude, let lo = first.origin.longitude {
            out.append(MapStop(lat: la, lon: lo, kind: .origin, name: first.origin.name ?? "", plat: nil))
        }
        for (i, t) in journey.transfers.enumerated() {
            guard let place = journey.legs[safe: i]?.destination,
                  let la = place.latitude, let lo = place.longitude else { continue }
            let plat = "\(t.arrivalPlatform ?? "?")→\(t.departurePlatform ?? "?")"
            out.append(MapStop(lat: la, lon: lo, kind: .transfer(t.verdictKind),
                               name: t.atStation ?? place.name ?? "", plat: plat))
        }
        if let la = last.destination.latitude, let lo = last.destination.longitude {
            out.append(MapStop(lat: la, lon: lo, kind: .destination, name: last.destination.name ?? "", plat: nil))
        }
        return out
    }
}

private func shortName(_ s: String) -> String {
    s.replacingOccurrences(of: " Hbf", with: "")
     .replacingOccurrences(of: " (Main)", with: "")
     .replacingOccurrences(of: "-Wilhelmshöhe", with: "")
}

private extension Verdict {
    /// Lowercase one-word tag for a map sublabel.
    var shortLabel: String {
        switch self {
        case .feasible: return "comfortable"
        case .tight: return "tight"
        case .infeasible: return "won't make it"
        case .unknown: return "unknown"
        case .pending: return "checking…"
        }
    }
}

// MARK: - Projection

/// Equirectangular projection scaled by cos(lat0), fit to the **route** — the same
/// maths as the signed-off HTML prototype, so the Swift map matches it. `x` depends
/// only on longitude and `y` only on latitude, which is what makes the graticule a
/// plain grid of straight lines.
struct MapProjection {
    let vbw: Double
    let vbh: Double
    private let pad: Double
    private let rx0: Double
    private let ry0: Double
    private let scale: Double
    static let k = cos(51.0 * .pi / 180)

    /// The smallest window we will ever show, in degrees of latitude (~122 km).
    /// Two jobs: a short hop (Köln→Düsseldorf) gets context instead of absurd
    /// magnification, and a route whose stops share a longitude — an ordinary
    /// two-stop case — cannot divide by a zero span.
    static let minSpan: Double = 1.1

    var aspect: Double { vbw / vbh }

    /// Fit `stops` into the fixed viewBox.
    ///
    /// The viewBox is a design constant, not derived from the data. Fitting the
    /// *box* to the route instead would make a north-south route (Hamburg→
    /// Stuttgart, bbox aspect 0.20) render as a ~41-unit sliver in a landscape
    /// slot, and would change the map's shape from one journey to the next. So the
    /// route's bbox is grown to the box's aspect and centred — no distortion, one
    /// stable frame.
    ///
    /// Pass **every journey being compared**, not just the one being drawn: a list
    /// of alternatives only reads if they share a frame (see `RouteMapView.fitTo`).
    init(fitting stops: [(lat: Double, lon: Double)],
         vbw: Double = 100, vbh: Double = 126.12, pad: Double = 12) {
        self.vbw = vbw
        self.vbh = vbh
        self.pad = pad
        let innerW = vbw - 2 * pad, innerH = vbh - 2 * pad
        let aspect = innerW / innerH

        var minx = Double.greatestFiniteMagnitude, maxx = -Double.greatestFiniteMagnitude
        var miny = Double.greatestFiniteMagnitude, maxy = -Double.greatestFiniteMagnitude
        for (la, lo) in stops {
            let x = lo * Self.k, y = -la
            minx = min(minx, x); maxx = max(maxx, x); miny = min(miny, y); maxy = max(maxy, y)
        }
        if minx > maxx {   // nothing placeable — centre on Germany rather than NaN
            minx = 10 * Self.k; maxx = minx; miny = -51; maxy = -51
        }
        let cx = (minx + maxx) / 2, cy = (miny + maxy) / 2
        // Floor both spans *before* dividing: this is the zero-span guard.
        var sx = max(maxx - minx, Self.minSpan * aspect)
        var sy = max(maxy - miny, Self.minSpan)
        // Grow the deficient axis so the route fills the frame without distortion.
        if sx / sy < aspect { sx = sy * aspect } else { sy = sx / aspect }
        scale = innerW / sx
        rx0 = cx - sx / 2
        ry0 = cy - sy / 2
    }

    func point(_ lat: Double, _ lon: Double) -> CGPoint {
        CGPoint(x: pad + (lon * Self.k - rx0) * scale,
                y: pad + (-lat - ry0) * scale)
    }

    /// raw (`x = lon·k`, `y = −lat`) → viewBox. The projection is only a uniform
    /// scale plus a translate, which is what lets the 7k-point geography be drawn
    /// through one affine transform instead of being re-projected per frame.
    var rawTransform: CGAffineTransform {
        CGAffineTransform(translationX: pad - rx0 * scale, y: pad - ry0 * scale)
            .scaledBy(x: scale, y: scale)
    }

    // Inverse, for deriving the graticule from whatever we ended up framing.
    func lon(atX x: Double) -> Double { (rx0 + (x - pad) / scale) / Self.k }
    func lat(atY y: Double) -> Double { -(ry0 + (y - pad) / scale) }
    var lonMin: Double { lon(atX: 0) }
    var lonMax: Double { lon(atX: vbw) }
    var latMax: Double { lat(atY: 0) }
    var latMin: Double { lat(atY: vbh) }

    /// A "nice" graticule step for a span, in degrees. The old map hardcoded
    /// lon 7…15 / lat 48…54, which draws lines through nothing the moment the
    /// frame isn't Germany.
    static func gratStep(_ span: Double) -> Double {
        [0.5, 1, 2, 5, 10, 20].first { span / $0 <= 5 } ?? 30
    }
}

// MARK: - Geography

/// The vendored Europe outline: `design/europe-geo.json`, generated by
/// `scripts/build_map_geo.py` from Natural Earth (public domain) and copied into
/// this target's `Resources/` by the same script, so the two cannot drift.
///
/// Replaces a hand-typed silhouette of Germany *only*. The projection was fitted
/// to that outline, so anything outside it landed off-canvas — Paris at x = −26.11
/// on a 0…100 viewBox — while `api/stations.py` happily autocompletes 70,837
/// stations across 43 countries (#18).
///
/// Bundled, not fetched: DESIGN.md §7 requires no run-time tiles, an installed
/// region has to be self-contained. Hence a `Canvas`, not MapKit.
///
/// No rivers: a hand-drawn Elbe ran Hamburg → Dresden in `accent` at the *same*
/// 16% alpha as the route's own glow, so it read as a second leg of the journey
/// (#18 — "why is there a line to dresden").
enum MapGeo {
    struct Payload: Decodable {
        let bbox: [Double]
        /// Closed rings, filled as one path — every country is the same colour, so
        /// the borders and enclaves inside the landmass are invisible.
        let land: [[Double]]
        /// Arcs owned by exactly one ring: the shore.
        let coast: [[Double]]
        /// Arcs owned by two rings: an internal border. Emitted once, so stroking
        /// them can't double-darken (which is what stroking every country would do).
        let borders: [[Double]]
        let cities: [City]
    }

    /// `["Berlin", lon, lat, rank]` — a positional array, so it decodes unkeyed.
    struct City: Decodable {
        let name: String
        let lon: Double
        let lat: Double
        /// 1 = recognisable from the country's shape alone, 2 = major hub. Lets a
        /// crowded frame drop the second tier first.
        let rank: Int

        init(from decoder: Decoder) throws {
            var c = try decoder.unkeyedContainer()
            name = try c.decode(String.self)
            lon = try c.decode(Double.self)
            lat = try c.decode(Double.self)
            rank = try c.decode(Int.self)
        }
    }

    static let shared: Payload = {
        guard let url = Bundle.module.url(forResource: "europe-geo", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let geo = try? JSONDecoder().decode(Payload.self, from: data)
        else {
            assertionFailure("europe-geo.json missing/!decodable — run scripts/build_map_geo.py")
            return Payload(bbox: [], land: [], coast: [], borders: [], cities: [])
        }
        return geo
    }()

    // Built once, in *raw* projection space. Each map applies `rawTransform`
    // rather than re-projecting every point — `LiveView` redraws these at 60fps.
    static let landRaw   = rawPath(shared.land, closed: true)
    static let coastRaw  = rawPath(shared.coast, closed: false)
    static let borderRaw = rawPath(shared.borders, closed: false)

    /// `arcs` are flat `lon,lat` pairs (GeoJSON axis order).
    private static func rawPath(_ arcs: [[Double]], closed: Bool) -> Path {
        var p = Path()
        for flat in arcs where flat.count >= 4 {
            var pts: [CGPoint] = []
            pts.reserveCapacity(flat.count / 2)
            for i in stride(from: 0, to: flat.count - 1, by: 2) {
                pts.append(CGPoint(x: flat[i] * MapProjection.k, y: -flat[i + 1]))
            }
            p.addLines(pts)
            if closed { p.closeSubpath() }
        }
        return p
    }
}
