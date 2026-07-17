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

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let proj = MapProjection.germany
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

        if !mini { drawGraticule(&ctx) }
        drawLand(&ctx)
        drawRivers(&ctx)
        if !mini { drawCities(&ctx) }
        drawRoute(&ctx)
    }

    // MARK: base layers

    private func drawGraticule(_ ctx: inout GraphicsContext) {
        for lon in [7.0, 9, 11, 13, 15] {
            let x = proj.point(48, lon).x
            var p = Path(); p.move(to: CGPoint(x: x, y: 0)); p.addLine(to: CGPoint(x: x, y: proj.vbh))
            ctx.stroke(p, with: .color(Theme.mapGrat), lineWidth: 0.4)
        }
        for lat in [48.0, 50, 52, 54] {
            let y = proj.point(lat, 10).y
            var p = Path(); p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: proj.vbw, y: y))
            ctx.stroke(p, with: .color(Theme.mapGrat), lineWidth: 0.4)
        }
    }

    private func drawLand(_ ctx: inout GraphicsContext) {
        let path = proj.path(DEGeo.outline, closed: true)
        ctx.fill(path, with: .color(Theme.mapLand))
        ctx.stroke(path, with: .color(Theme.mapCoast),
                   style: StrokeStyle(lineWidth: 0.7, lineJoin: .round))
    }

    private func drawRivers(_ ctx: inout GraphicsContext) {
        for river in [DEGeo.rhine, DEGeo.elbe] {
            ctx.stroke(proj.path(river, closed: false), with: .color(Theme.mapRiver),
                       style: StrokeStyle(lineWidth: 0.9, lineCap: .round))
        }
    }

    private func drawCities(_ ctx: inout GraphicsContext) {
        let exclude = Set(MapStop.stops(for: journey).map { shortName($0.name) } + ["Hannover"])
        for city in DEGeo.cities where !exclude.contains(city.name) {
            let p = proj.point(city.lat, city.lon)
            ctx.fill(Path(ellipseIn: CGRect(x: p.x - 0.9, y: p.y - 0.9, width: 1.8, height: 1.8)),
                     with: .color(Theme.mapCity))
            let right = p.x <= proj.vbw * 0.62
            ctx.draw(Text(city.name).font(.system(size: 2.7)).foregroundColor(Theme.mapCity),
                     at: CGPoint(x: p.x + (right ? 2.2 : -2.2), y: p.y + 0.9),
                     anchor: right ? .leading : .trailing)
        }
    }

    // MARK: route overlay

    private func drawRoute(_ ctx: inout GraphicsContext) {
        let stops = MapStop.stops(for: journey)
        let pts = stops.map { proj.point($0.lat, $0.lon) }
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

/// Equirectangular projection scaled by cos(lat0), fit to Germany's bounds — the
/// same maths as the signed-off HTML prototype, so the Swift map matches it. `x`
/// depends only on longitude and `y` only on latitude, which is what makes the
/// graticule a plain grid of straight lines.
struct MapProjection {
    let vbw: Double
    let vbh: Double
    private let pad: Double
    private let rx0: Double
    private let ry0: Double
    private let scale: Double
    static let k = cos(51.0 * .pi / 180)

    var aspect: Double { vbw / vbh }

    init(outline: [(lat: Double, lon: Double)], vbw: Double = 100, pad: Double = 7) {
        self.vbw = vbw
        self.pad = pad
        var minx = Double.greatestFiniteMagnitude, maxx = -Double.greatestFiniteMagnitude
        var miny = Double.greatestFiniteMagnitude, maxy = -Double.greatestFiniteMagnitude
        for (la, lo) in outline {
            let x = lo * Self.k, y = -la
            minx = min(minx, x); maxx = max(maxx, x); miny = min(miny, y); maxy = max(maxy, y)
        }
        rx0 = minx; ry0 = miny
        scale = (vbw - 2 * pad) / (maxx - minx)
        vbh = (maxy - miny) * scale + 2 * pad
    }

    func point(_ lat: Double, _ lon: Double) -> CGPoint {
        CGPoint(x: pad + (lon * Self.k - rx0) * scale,
                y: pad + (-lat - ry0) * scale)
    }

    func path(_ coords: [(lat: Double, lon: Double)], closed: Bool) -> Path {
        var p = Path()
        p.addLines(coords.map { point($0.lat, $0.lon) })
        if closed { p.closeSubpath() }
        return p
    }

    static let germany = MapProjection(outline: DEGeo.outline)
}

// MARK: - Geography (schematic, [lat, lon])

/// A coarse but recognisable silhouette of Germany plus two rivers and reference
/// cities, purely to ground the route. Not survey-grade — the route itself is
/// exact (real leg coordinates); this is only the backdrop.
enum DEGeo {
    static let outline: [(lat: Double, lon: Double)] = [
        (54.90,8.30),(54.83,9.45),(54.45,10.00),(54.30,11.10),(54.15,12.10),(54.35,13.10),(53.93,14.20),
        (53.30,14.35),(52.80,14.14),(52.10,14.72),(51.55,14.75),(51.00,14.95),
        (50.95,14.30),(50.65,14.00),(50.40,12.95),(50.20,12.20),(49.75,12.55),(49.30,12.60),(48.95,13.40),(48.58,13.50),
        (48.35,13.00),(47.95,12.90),(47.70,12.20),(47.55,11.30),(47.45,10.90),(47.55,10.10),(47.55,9.75),
        (47.65,9.20),(47.80,8.65),(47.55,7.85),(47.59,7.59),
        (48.10,7.55),(48.75,8.00),(49.05,8.00),(49.10,6.85),(49.46,6.36),
        (50.00,6.13),(50.32,6.02),(50.75,6.02),(51.05,5.87),(51.60,6.05),(51.83,6.10),(52.10,6.68),(52.45,7.06),(52.65,7.06),(53.18,7.19),
        (53.68,7.15),(53.70,8.00),(53.90,8.13),(54.05,8.55),(54.45,8.60),(54.75,8.30),
    ]
    static let rhine: [(lat: Double, lon: Double)] = [
        (47.59,7.59),(48.0,7.75),(48.6,7.85),(49.0,8.3),(49.5,8.45),(50.0,8.3),(50.4,7.62),(50.9,6.96),(51.4,6.72),(51.83,6.1),
    ]
    static let elbe: [(lat: Double, lon: Double)] = [
        (53.55,10.0),(53.2,10.9),(52.9,11.6),(52.5,12.25),(51.85,12.9),(51.3,13.4),(51.05,13.74),
    ]
    static let cities: [(name: String, lat: Double, lon: Double)] = [
        ("Berlin",52.52,13.40),("München",48.14,11.58),("Köln",50.94,6.96),("Leipzig",51.34,12.37),
        ("Nürnberg",49.45,11.08),("Hannover",52.37,9.73),("Dresden",51.05,13.74),("Bremen",53.08,8.81),("Dortmund",51.51,7.47),
    ]
}
