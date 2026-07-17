import Foundation
import SwiftUI
import Testing
@testable import TransfrCore
@testable import TransfrUI

/// The route map's projection and its vendored geography (#18).
///
/// The map had no tests at all, and shipped a hand-typed silhouette of *Germany*
/// with the projection fitted to that outline — so a Paris route projected to
/// x = -26.11 on a 0…100 viewBox while `api/stations.py` autocompletes 43
/// countries. These pin the three things that fix has to keep true: non-German
/// routes land on canvas, German ones still look sane, and the maths cannot divide
/// by a zero span.
@MainActor
struct RouteMapTests {

    // MARK: - Fixtures

    /// Build a journey from `(name, lat, lon)` stops, via the real decode path
    /// (the contract has no public memberwise init, and this exercises it anyway).
    static func journey(_ stops: [(String, Double, Double)], id: String = "j") throws -> Journey {
        precondition(stops.count >= 2)
        func place(_ s: (String, Double, Double)) -> String {
            #"{"name":"\#(s.0)","latitude":\#(s.1),"longitude":\#(s.2)}"#
        }
        let legs = zip(stops, stops.dropFirst()).map { a, b in
            #"{"mode":"transit","origin":\#(place(a)),"destination":\#(place(b)),"cancelled":false}"#
        }.joined(separator: ",")
        let transfers = stops.dropFirst().dropLast().map {
            #"{"at_station":"\#($0.0)","verdict":"feasible"}"#
        }.joined(separator: ",")
        let json = #"""
        {"id":"\#(id)","num_changes":\#(max(0, stops.count - 2)),"verdict":"feasible",
         "legs":[\#(legs)],"transfers":[\#(transfers)]}
        """#
        let dec = JSONDecoder()
        dec.keyDecodingStrategy = .convertFromSnakeCase
        return try dec.decode(Journey.self, from: Data(json.utf8))
    }

    static let hamburgStuttgart: [(String, Double, Double)] = [
        ("Hamburg Hbf", 53.5528, 10.0067), ("Göttingen", 51.5366, 9.9266),
        ("Mannheim Hbf", 49.4794, 8.4693), ("Stuttgart Hbf", 48.7838, 9.1815),
    ]
    static let parisZurich: [(String, Double, Double)] = [
        ("Paris Gare de Lyon", 48.8443, 2.3743), ("Basel SBB", 47.5474, 7.5896),
        ("Zürich HB", 47.3779, 8.5403),
    ]

    static func proj(_ stops: [(String, Double, Double)]) -> MapProjection {
        MapProjection(fitting: stops.map { (lat: $0.1, lon: $0.2) })
    }

    /// Every stop lands inside the viewBox, with room for its marker.
    static func expectOnCanvas(_ p: MapProjection, _ stops: [(String, Double, Double)],
                               _ what: String, sourceLocation: SourceLocation = #_sourceLocation) {
        for (name, lat, lon) in stops {
            let pt = p.point(lat, lon)
            #expect(pt.x.isFinite && pt.y.isFinite, "\(what): \(name) is not finite (\(pt))",
                    sourceLocation: sourceLocation)
            #expect(pt.x >= 0 && pt.x <= p.vbw, "\(what): \(name) x=\(pt.x) outside 0…\(p.vbw)",
                    sourceLocation: sourceLocation)
            #expect(pt.y >= 0 && pt.y <= p.vbh, "\(what): \(name) y=\(pt.y) outside 0…\(p.vbh)",
                    sourceLocation: sourceLocation)
        }
    }

    // MARK: - #18: routes outside Germany

    /// The bug, as an assertion. Under the Germany-fitted outline Paris projected
    /// to x = -26.11 and Wien to x = 106.50 on a 0…100 box.
    @Test func nonGermanRouteProjectsOnCanvas() {
        Self.expectOnCanvas(Self.proj(Self.parisZurich), Self.parisZurich, "Paris→Zürich")
    }

    @Test(arguments: [
        ("Wien→Budapest", [("Wien Hbf", 48.185, 16.377), ("Budapest Keleti", 47.500, 19.084)]),
        ("Milano→Roma", [("Milano Centrale", 45.486, 9.204), ("Roma Termini", 41.901, 12.501)]),
        ("København→Malmö", [("København H", 55.673, 12.565), ("Malmö C", 55.609, 13.000)]),
        ("Madrid→Barcelona", [("Madrid Atocha", 40.407, -3.690), ("Barcelona Sants", 41.379, 2.140)]),
        ("London→Paris", [("London St Pancras", 51.532, -0.126), ("Paris Nord", 48.881, 2.355)]),
    ])
    func routesAcrossEuropeProjectOnCanvas(_ name: String, _ stops: [(String, Double, Double)]) {
        Self.expectOnCanvas(Self.proj(stops), stops, name)
    }

    /// …and the German case still behaves.
    @Test func germanRouteStillProjectsSanely() {
        let p = Self.proj(Self.hamburgStuttgart)
        Self.expectOnCanvas(p, Self.hamburgStuttgart, "Hamburg→Stuttgart")
        // North of the route must be *above* south, and east right of west.
        let hh = p.point(53.5528, 10.0067), str = p.point(48.7838, 9.1815)
        #expect(hh.y < str.y, "Hamburg should sit above Stuttgart")
        #expect(p.point(51, 14).x > p.point(51, 7).x, "east should sit right of west")
    }

    // MARK: - The divide-by-zero guard

    /// `scale = innerW / (maxx - minx)` → inf for a two-stop route whose endpoints
    /// share a longitude. Ordinary, not exotic: any due-north hop does it.
    @Test func twoStopsOnTheSameMeridianDoNotDivideByZero() {
        let stops = [("A", 50.0, 9.0), ("B", 51.0, 9.0)]
        let p = Self.proj(stops)
        Self.expectOnCanvas(p, stops, "same meridian")
        #expect(p.point(50, 9).x == p.point(51, 9).x, "same longitude ⇒ same x")
    }

    @Test func twoStopsOnTheSameParallelDoNotDivideByZero() {
        let stops = [("A", 50.0, 9.0), ("B", 50.0, 11.0)]
        let p = Self.proj(stops)
        Self.expectOnCanvas(p, stops, "same parallel")
        #expect(p.point(50, 9).y == p.point(50, 11).y, "same latitude ⇒ same y")
    }

    /// The pathological one: a single point, zero span on *both* axes.
    @Test func identicalStopsProduceAFiniteProjection() {
        let stops = [("A", 50.0, 9.0), ("A again", 50.0, 9.0)]
        let p = Self.proj(stops)
        Self.expectOnCanvas(p, stops, "identical stops")
    }

    /// A journey whose stops carry no coordinates yields no points to fit.
    @Test func emptyFitIsFiniteAndCentredOnGermany() {
        let p = MapProjection(fitting: [])
        let pt = p.point(51, 10)
        #expect(pt.x.isFinite && pt.y.isFinite)
        #expect(abs(pt.x - p.vbw / 2) < 0.001, "should centre the fallback")
    }

    // MARK: - Minimum extent

    /// A 35 km hop must not magnify to absurdity — it gets context instead.
    @Test func shortHopKeepsAMinimumWindow() {
        let stops = [("Köln Hbf", 50.9430, 6.9587), ("Düsseldorf Hbf", 51.2200, 6.7940)]
        let p = Self.proj(stops)
        Self.expectOnCanvas(p, stops, "Köln→Düsseldorf")
        #expect(p.latMax - p.latMin >= MapProjection.minSpan,
                "window \(p.latMax - p.latMin)° is under the \(MapProjection.minSpan)° floor")
        // A long route must NOT be clamped to that floor.
        let long = Self.proj(Self.hamburgStuttgart)
        #expect(long.latMax - long.latMin > 4, "Hamburg→Stuttgart spans ~4.8° of latitude")
    }

    // MARK: - One frame for a set of journeys

    /// Load-bearing: alternatives are drawn to be compared. Fitting each map to its
    /// own route would silently rescale them, so `fitTo` fits their union — the same
    /// projection for every map in the set.
    @Test func aSetOfJourneysSharesOneFrame() throws {
        let viaGoettingen = try Self.journey(Self.hamburgStuttgart)
        let viaFrankfurt = try Self.journey([
            ("Hamburg Hbf", 53.5528, 10.0067), ("Frankfurt (Main) Hbf", 50.1070, 8.6638),
            ("Stuttgart Hbf", 48.7838, 9.1815),
        ], id: "j2")
        let set = [viaGoettingen, viaFrankfurt]

        let union = MapProjection(fitting: MapStop.stops(for: set).map { (lat: $0.lat, lon: $0.lon) })
        // Both journeys' stops must fit the shared frame.
        for j in set {
            for s in MapStop.stops(for: j) {
                let pt = union.point(s.lat, s.lon)
                #expect(pt.x >= 0 && pt.x <= union.vbw && pt.y >= 0 && pt.y <= union.vbh,
                        "\(s.name) escapes the shared frame at \(pt)")
            }
        }
        // The union frame differs from a per-route fit — which is exactly why the
        // per-route fit would make the two maps incomparable.
        let solo = MapProjection(fitting: MapStop.stops(for: viaFrankfurt).map { (lat: $0.lat, lon: $0.lon) })
        #expect(abs(solo.lonMin - union.lonMin) > 0.001 || abs(solo.latMin - union.latMin) > 0.001,
                "expected the shared frame to differ from a solo fit")
        // A stop common to both lands in the same place under one projection.
        #expect(union.point(53.5528, 10.0067) == union.point(53.5528, 10.0067))
    }

    // MARK: - Graticule

    /// Derived from the fitted bbox. Hardcoding lon 7…15 / lat 48…54 drew lines
    /// through nothing the moment the frame wasn't Germany.
    @Test func graticuleStepScalesWithTheFrame() {
        #expect(MapProjection.gratStep(1.0) == 0.5)
        #expect(MapProjection.gratStep(4.0) == 1)
        #expect(MapProjection.gratStep(9.0) == 2)
        #expect(MapProjection.gratStep(24.0) == 5)
        #expect(MapProjection.gratStep(40.0) == 10)
        // Never zero or negative — it drives a `while` loop.
        for span in stride(from: 0.01, to: 90.0, by: 0.37) {
            #expect(MapProjection.gratStep(span) > 0)
        }
    }

    @Test func graticuleLinesFallInsideTheFrame() {
        let p = Self.proj(Self.parisZurich)
        let step = MapProjection.gratStep(p.lonMax - p.lonMin)
        var i = (p.lonMin / step).rounded(.up)
        var drawn = 0
        while i * step <= p.lonMax {
            let x = p.point(0, i * step).x
            #expect(x >= -0.001 && x <= p.vbw + 0.001, "meridian \(i*step)° at x=\(x)")
            drawn += 1
            i += 1
        }
        #expect(drawn >= 2, "expected at least a couple of meridians, drew \(drawn)")
    }

    // MARK: - Projection internals

    /// The geography is drawn by applying `rawTransform` to a prebuilt path rather
    /// than re-projecting 7k points per frame; it must agree with `point()`.
    @Test func rawTransformAgreesWithPoint() {
        let p = Self.proj(Self.hamburgStuttgart)
        for (_, lat, lon) in Self.hamburgStuttgart + Self.parisZurich {
            let raw = CGPoint(x: lon * MapProjection.k, y: -lat)
            let viaTransform = raw.applying(p.rawTransform)
            let direct = p.point(lat, lon)
            #expect(abs(viaTransform.x - direct.x) < 0.0001, "x \(viaTransform.x) vs \(direct.x)")
            #expect(abs(viaTransform.y - direct.y) < 0.0001, "y \(viaTransform.y) vs \(direct.y)")
        }
    }

    /// x must depend only on longitude and y only on latitude — that property is
    /// what lets the graticule be straight lines.
    @Test func axesAreIndependent() {
        let p = Self.proj(Self.hamburgStuttgart)
        #expect(p.point(48, 9).x == p.point(54, 9).x)
        #expect(p.point(48, 9).y == p.point(48, 15).y)
    }

    @Test func viewBoxIsAStableDesignConstant() {
        // Every map is the same shape, whatever the route — the page layout and the
        // app's fixed-height slots depend on it.
        for stops in [Self.hamburgStuttgart, Self.parisZurich,
                      [("A", 50.0, 9.0), ("B", 51.0, 9.0)]] {
            let p = Self.proj(stops)
            #expect(p.vbw == 100)
            #expect(abs(p.vbh - 126.12) < 0.001)
        }
    }

    // MARK: - Label de-clutter

    /// Every label that hangs off the same side must be at least `blockH` apart.
    /// Opposite sides can be level — their text runs away from each other.
    static func expectLabelsClear(_ pts: [CGPoint], _ place: [RouteLabelPlacement], _ what: String,
                                  sourceLocation: SourceLocation = #_sourceLocation) {
        for i in pts.indices {
            for j in pts.indices.dropFirst(i + 1) where place[i].right == place[j].right {
                let a = pts[i].y + place[i].dy, b = pts[j].y + place[j].dy
                #expect(abs(a - b) >= RouteLabelLayout.blockH - 0.001,
                        "\(what): labels \(i) and \(j) are \(abs(a - b)) apart on the same side",
                        sourceLocation: sourceLocation)
            }
        }
    }

    /// Every label's ink stays on the canvas. The de-clutter moves labels, so it can
    /// just as easily move them into the sea — which is no more readable than the
    /// overlap it set out to fix.
    static func expectLabelsOnCanvas(_ p: MapProjection, _ pts: [CGPoint],
                                     _ place: [RouteLabelPlacement], _ what: String,
                                     sourceLocation: SourceLocation = #_sourceLocation) {
        for (i, pl) in place.enumerated() {
            let y = Double(pts[i].y) + pl.dy
            #expect(y - RouteLabelLayout.inkAbove >= -0.001,
                    "\(what): label \(i) runs off the top at \(y)", sourceLocation: sourceLocation)
            #expect(y + RouteLabelLayout.inkBelow <= p.vbh + 0.001,
                    "\(what): label \(i) runs off the bottom at \(y) (vbh \(p.vbh))",
                    sourceLocation: sourceLocation)
        }
    }

    static func placements(_ stops: [(String, Double, Double)]) -> ([CGPoint], [RouteLabelPlacement]) {
        let p = proj(stops)
        let pts = stops.map { p.point($0.1, $0.2) }
        return (pts, RouteLabelLayout.place(pts, vbw: p.vbw, vbh: p.vbh))
    }

    /// The bug: fitting the frame to the route (#18) puts Basel at (76.3, 74.1) and
    /// Zürich at (88.0, 77.4) — 12 units apart, both past the 0.58 split, so both
    /// labels hang left and "Pl 7→8 · comfortable" printed on the same line as
    /// "Zürich HB" (baselines 0.08 apart in the prototype).
    @Test func closeStopsDoNotOverlapTheirLabels() {
        let (pts, place) = Self.placements(Self.parisZurich)

        // The case only bites because both labels hang the same way; a side flip is
        // no escape either — Zürich's name would run off the box from x=88.
        #expect(place[1].right == place[2].right, "fixture no longer puts both labels on one side")
        #expect(!place[2].right && pts[2].x > 80, "Zürich should be far right, hanging left")
        // Untreated they'd be ~3.3 apart — well inside one block.
        let natural = abs(pts[1].y - pts[2].y)
        #expect(natural < RouteLabelLayout.blockH,
                "fixture no longer reproduces the overlap: stops are \(natural) apart")

        Self.expectLabelsClear(pts, place, "Paris→Basel→Zürich")
        Self.expectLabelsOnCanvas(Self.proj(Self.parisZurich), pts, place, "Paris→Basel→Zürich")
        // Paris is far away, and on the other side — it must not be dragged along.
        #expect(place[0].dy == 0, "Paris moved \(place[0].dy) for no reason")
        // Only the lower of the pair gives way, and only by what it owed.
        #expect(place[1].dy == 0, "Basel should hold its place")
        #expect(abs(place[2].dy - (RouteLabelLayout.blockH - natural)) < 0.001,
                "Zürich should move exactly enough to clear, moved \(place[2].dy)")
    }

    /// The flip side: a well-spread route must be left exactly as it was. These are
    /// the positions the prototype draws today.
    @Test func wellSpacedStopsAreLeftAlone() {
        let (pts, place) = Self.placements(Self.hamburgStuttgart)
        Self.expectLabelsClear(pts, place, "Hamburg→Stuttgart")
        for (i, pl) in place.enumerated() {
            #expect(pl.dy == 0, "stop \(i) (\(Self.hamburgStuttgart[i].0)) moved \(pl.dy)")
        }
    }

    /// Three stops in a heap, not just two: each must clear the one above it, so the
    /// pass has to keep stacking rather than resolve one pair and stop.
    ///
    /// This is also the shape that breaks a push-downwards-only stagger: the far stop
    /// sets the zoom, the cluster lands hard against the bottom of the frame, and the
    /// last label is shoved off the map. It must spread around the cluster instead.
    @Test func aChainOfCloseStopsAllSeparates() {
        let stops = [("Start", 50.20, 9.00), ("A", 47.60, 8.10), ("B", 47.55, 8.20), ("C", 47.50, 8.30)]
        let p = Self.proj(stops)
        let (pts, place) = Self.placements(stops)
        #expect(pts.dropFirst().allSatisfy { $0.y > p.vbh - 20 },
                "fixture should pile the cluster against the bottom edge")
        Self.expectLabelsClear(pts, place, "chain")
        Self.expectLabelsOnCanvas(p, pts, place, "chain")
    }

    /// Stops level with each other but on opposite sides never interact.
    @Test func oppositeSidesDoNotPushEachOther() {
        // Same latitude, far apart in longitude → same y, x either side of the split.
        let stops = [("West", 48.0, 2.0), ("East", 48.0, 16.0)]
        let (pts, place) = Self.placements(stops)
        #expect(abs(pts[0].y - pts[1].y) < 0.001, "fixture should put both at the same height")
        #expect(place[0].right != place[1].right, "fixture should split them across the sides")
        for pl in place { #expect(pl.dy == 0, "opposite sides should not push, moved \(pl.dy)") }
    }

    /// The constant is load-bearing, not cosmetic. Two labels must end up further
    /// apart than the two lines *inside* one label, or the sub-label reads as
    /// belonging to the stop below — the wrong platform on the wrong station.
    @Test func labelSpacingOutranksLineSpacing() {
        let withinOneLabel = RouteLabelLayout.subDY - RouteLabelLayout.nameDY
        let betweenLabels = RouteLabelLayout.blockH - withinOneLabel
        #expect(betweenLabels > withinOneLabel,
                "blockH \(RouteLabelLayout.blockH) leaves \(betweenLabels) between labels vs \(withinOneLabel) inside one — the sub would group with the wrong stop")
    }

    /// Ordering must not depend on the stop order, or the two renderers could
    /// disagree: the HTML sorts the same way (`(y-y) || (i-i)`).
    @Test func placementIsIndependentOfStopOrder() {
        let p = Self.proj(Self.parisZurich)
        let (pts, place) = Self.placements(Self.parisZurich)
        let reversedPlace = RouteLabelLayout.place(Array(pts.reversed()), vbw: p.vbw, vbh: p.vbh)
        for (a, b) in zip(place, reversedPlace.reversed()) {
            #expect(a == b, "reversing the stops changed the layout: \(a) vs \(b)")
        }
    }

    /// Ties are broken by index, so a degenerate route still lays out deterministically.
    @Test func labelsAtIdenticalPointsStillSeparate() {
        let stops = [("A", 50.0, 9.0), ("A again", 50.0, 9.0)]
        let (pts, place) = Self.placements(stops)
        Self.expectLabelsClear(pts, place, "identical stops")
        #expect(place[0].dy == 0, "the first of the pair should hold its place")
        #expect(abs(place[1].dy - RouteLabelLayout.blockH) < 0.001,
                "the second should drop a full block, dropped \(place[1].dy)")
    }

    @Test func placeHandlesAnEmptyRoute() {
        #expect(RouteLabelLayout.place([], vbw: 100, vbh: 126.12).isEmpty)
    }

    // MARK: - The vendored asset

    @Test func geographyLoadsFromTheBundle() {
        let geo = MapGeo.shared
        #expect(!geo.land.isEmpty, "no land — is europe-geo.json in the bundle?")
        #expect(!geo.coast.isEmpty)
        #expect(!geo.borders.isEmpty)
        #expect(geo.cities.count >= 40)
        #expect(geo.bbox == [-26.0, 27.0, 46.0, 72.0])
        for ring in geo.land {
            #expect(ring.count >= 4 && ring.count % 2 == 0, "land rings are flat lon,lat pairs")
        }
    }

    /// The asset has to contain the places the app can be asked to draw.
    @Test func geographyCoversTheCitiesTheAppCanRouteTo() {
        let geo = MapGeo.shared
        let lons = geo.land.flatMap { ring in stride(from: 0, to: ring.count - 1, by: 2).map { ring[$0] } }
        let lats = geo.land.flatMap { ring in stride(from: 1, to: ring.count, by: 2).map { ring[$0] } }
        for (name, lon, lat) in [("Paris", 2.35, 48.86), ("Wien", 16.37, 48.21),
                                 ("Milano", 9.19, 45.46), ("København", 12.57, 55.68)] {
            #expect(lons.min()! <= lon && lon <= lons.max()!, "\(name) lon off the map")
            #expect(lats.min()! <= lat && lat <= lats.max()!, "\(name) lat off the map")
        }
    }

    @Test func geographyPathsAreNonEmpty() {
        #expect(!MapGeo.landRaw.isEmpty)
        #expect(!MapGeo.coastRaw.isEmpty)
        #expect(!MapGeo.borderRaw.isEmpty)
    }

    // MARK: - It actually draws

    /// Rasterise the real view for a German and a non-German route, so the whole
    /// projection + geography path runs (no NaN, no crash). PNGs land in the temp
    /// dir — grep `RENDER_PNG:` to eyeball them.
    @Test(arguments: [("de", true), ("fr", false)])
    func routeMapRenders(_ tag: String, _ german: Bool) throws {
        let j = try Self.journey(german ? Self.hamburgStuttgart : Self.parisZurich)
        let view = RouteMapView(journey: j, fromCurrent: true)
            .frame(width: 350, height: 200)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try #require(renderer.uiImage, "ImageRenderer produced no image")
        let data = try #require(image.pngData(), "no PNG data")
        #expect(data.count > 1000, "suspiciously small render (\(data.count) bytes)")
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("routemap_\(tag).png")
        try data.write(to: url)
        print("RENDER_PNG: \(url.path)")
    }

    @Test func miniMapRenders() throws {
        let view = RouteMapView(journey: try Self.journey(Self.parisZurich), mini: true, showLabels: false)
            .frame(width: 96, height: 120)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try #require(renderer.uiImage, "ImageRenderer produced no image")
        let data = try #require(image.pngData(), "no PNG data")
        #expect(data.count > 200)
    }
}
