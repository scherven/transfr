import Foundation
import Testing
@testable import TransfrCore

/// Unit tests for the pure "Paste link" parser. Every string here is a fixed
/// fixture — **no network** — so short links are exercised via the
/// `shortLinkNeedsExpansion` throw and the *expanded* URL is tested directly (it's
/// the real string `maps.app.goo.gl/JWTvpehbneTcqad39` redirects to; see
/// `md/PASTE-LINK.md`). Covers the three link families plus graceful failure.
struct RouteLinkParserTests {
    typealias P = RouteLinkParser

    // Berlin timezone so the bahn.de wall-clock assertions are deterministic
    // regardless of the machine running the suite.
    private static let berlin = TimeZone(identifier: "Europe/Berlin")!
    private func components(_ date: Date) -> DateComponents {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = Self.berlin
        return cal.dateComponents([.year, .month, .day, .hour, .minute], from: date)
    }
    private func approx(_ a: Double, _ b: Double, _ eps: Double = 1e-4) -> Bool { abs(a - b) < eps }

    // MARK: - Short-link detection & expansion signalling

    @Test func detectsGoogleShortLink() {
        #expect(P.isShortLink("https://maps.app.goo.gl/JWTvpehbneTcqad39"))
        #expect(P.isShortLink("https://goo.gl/maps/abc123"))
        #expect(!P.isShortLink("https://www.google.com/maps/dir/A/B"))
        #expect(!P.isShortLink("https://maps.apple.com/?daddr=X"))
        #expect(!P.isShortLink("not a link at all"))
    }

    @Test func shortLinkThrowsForExpansion() throws {
        let raw = "https://maps.app.goo.gl/JWTvpehbneTcqad39"
        #expect(throws: P.ParseError.self) { try P.parse(raw) }
        do { _ = try P.parse(raw); Issue.record("expected throw") }
        catch P.ParseError.shortLinkNeedsExpansion(let url) {
            #expect(url.absoluteString == raw)
        }
    }

    @Test func extractsURLFromSurroundingText() {
        // A pasted sentence with the short link in the middle still resolves.
        let raw = "check this out https://maps.app.goo.gl/JWTvpehbneTcqad39 thanks!"
        #expect(P.isShortLink(raw))
    }

    // MARK: - Google Maps: the real expanded /maps/dir/ URL (field fixture)

    /// Exactly what `maps.app.goo.gl/JWTvpehbneTcqad39` (the app's default) expands
    /// to — captured once by following the 302. Brussels-South → München Hbf, transit.
    private static let expandedGoogleDir =
        "https://www.google.com/maps/dir/Brussel-Zuid,+Av.+Fonsny+47B,+1060+Bruxelles,+Belgium/" +
        "M%C3%BCnchen+Hauptbahnhof,+Bayerstra%C3%9Fe+10A,+80335+M%C3%BCnchen,+Germany/" +
        "@49.6523411,5.3147974,757692m/am=t/data=!3m1!1e3!4m15!4m14!1m5!1m1!" +
        "1s0x47c3c4e87f85c5b9:0xa3b206161a5f91a8!2m2!1d4.3370087!2d50.8364402!1m5!1m1!" +
        "1s0x479e75fec5b151b9:0x43ec2bf2c2451cc2!2m2!1d11.5582682!2d48.1404108!3e3!5i5" +
        "?entry=tts&g_ep=EgoyMDI2MDcwOC4wIPu8ASoASAFQAw%3D%3D&skid=740fc252"

    @Test func parsesRealExpandedGoogleDir() throws {
        let r = try P.parse(Self.expandedGoogleDir)
        #expect(r.source == .googleMaps)
        // First comma-component of each path segment is the station name.
        #expect(r.from == "Brussel-Zuid")
        #expect(r.to == "München Hauptbahnhof")
        #expect(r.travelMode == .transit)                 // !3e3
        #expect(r.isPlannable)
        // Endpoint coords from the data block (!1d = lng, !2d = lat), in order.
        let f = try #require(r.fromCoordinate); let t = try #require(r.toCoordinate)
        #expect(approx(f.latitude, 50.8364402) && approx(f.longitude, 4.3370087))
        #expect(approx(t.latitude, 48.1404108) && approx(t.longitude, 11.5582682))
    }

    // MARK: - Google Maps: other shapes

    @Test func parsesGoogleMapsURLsAPIForm() throws {
        let r = try P.parse("https://www.google.com/maps/dir/?api=1" +
                            "&origin=Hamburg%20Hbf&destination=Stuttgart%20Hbf&travelmode=transit")
        #expect(r.from == "Hamburg Hbf")
        #expect(r.to == "Stuttgart Hbf")
        #expect(r.travelMode == .transit)
        #expect(r.isPlannable)
    }

    @Test func googleDirEmptyOriginMeansCurrentLocation() throws {
        // "/dir//Dest/" — leading empty segment = "here", trailing slash ignored.
        let r = try P.parse("https://www.google.com/maps/dir//Stuttgart+Hbf/")
        #expect(r.from == nil)
        #expect(r.to == "Stuttgart Hbf")
        #expect(!r.isPlannable)               // no origin → app must supply one
    }

    @Test func googleNonTransitStillParsesNames() throws {
        let r = try P.parse("https://www.google.com/maps/dir/Berlin+Hbf/Leipzig+Hbf/" +
                            "data=!4m2!4m1!3e0")     // !3e0 = driving
        #expect(r.from == "Berlin Hbf")
        #expect(r.to == "Leipzig Hbf")
        #expect(r.travelMode == .driving)     // parsed, not rejected — planning still works
        #expect(r.isPlannable)
    }

    // MARK: - Apple Maps

    @Test func parsesAppleDirections() throws {
        let r = try P.parse("http://maps.apple.com/?saddr=Berlin+Hbf&daddr=Basel+SBB&dirflg=r")
        #expect(r.source == .appleMaps)
        #expect(r.from == "Berlin Hbf")
        #expect(r.to == "Basel SBB")
        #expect(r.travelMode == .transit)     // dirflg=r
        #expect(r.isPlannable)
    }

    @Test func parsesApplePlaceWithNameAndCoordinate() throws {
        let r = try P.parse("https://maps.apple.com/?q=Stuttgart+Hbf&ll=48.7838,9.1817")
        #expect(r.from == nil)                // a place link has no origin
        #expect(r.to == "Stuttgart Hbf")
        let c = try #require(r.toCoordinate)
        #expect(approx(c.latitude, 48.7838) && approx(c.longitude, 9.1817))
        #expect(!r.isPlannable)
    }

    @Test func appleCoordinateDestinationKeepsCoordNotName() throws {
        let r = try P.parse("https://maps.apple.com/?daddr=48.1404,11.5583&dirflg=r")
        #expect(r.to == nil)                  // "48.1404,11.5583" is a pin, not a name
        let c = try #require(r.toCoordinate)
        #expect(approx(c.latitude, 48.1404) && approx(c.longitude, 11.5583))
    }

    // MARK: - Deutsche Bahn / bahn.de

    @Test func parsesBahnNewFragment() throws {
        let r = try P.parse("https://www.bahn.de/buchung/fahrplan/suche#sts=true" +
                            "&so=Hamburg%20Hbf&zo=Stuttgart%20Hbf&hd=2026-07-15T08:34:00&kl=2",
                            timeZone: Self.berlin)
        #expect(r.source == .deutscheBahn)
        #expect(r.from == "Hamburg Hbf")
        #expect(r.to == "Stuttgart Hbf")
        #expect(r.travelMode == .transit)
        let dep = try #require(r.departure)
        let c = components(dep)
        #expect(c.year == 2026 && c.month == 7 && c.day == 15 && c.hour == 8 && c.minute == 34)
    }

    @Test func bahnFallsBackToStopIdName() throws {
        // No so/zo — names must come from the @O= inside soid/zoid.
        let r = try P.parse("https://www.bahn.de/buchung/fahrplan/suche#" +
            "soid=A%3D1%40O%3DHamburg%20Hbf%40X%3D10006905%40L%3D8002549%40" +
            "&zoid=A%3D1%40O%3DStuttgart%20Hbf%40X%3D9181635%40L%3D8098096%40")
        #expect(r.from == "Hamburg Hbf")
        #expect(r.to == "Stuttgart Hbf")
    }

    @Test func parsesOldReiseauskunft() throws {
        let r = try P.parse("https://reiseauskunft.bahn.de/bin/query.exe/dn?" +
                            "S=Hamburg+Hbf&Z=Stuttgart+Hbf&date=15.07.26&time=08:34",
                            timeZone: Self.berlin)
        #expect(r.from == "Hamburg Hbf")
        #expect(r.to == "Stuttgart Hbf")
        let c = components(try #require(r.departure))
        #expect(c.year == 2026 && c.month == 7 && c.day == 15 && c.hour == 8 && c.minute == 34)
    }

    // MARK: - Graceful failure

    @Test func emptyStringIsNotAURL() {
        #expect(throws: P.ParseError.notAURL) { try P.parse("   ") }
    }

    @Test func unknownProviderThrows() {
        #expect(throws: P.ParseError.unrecognizedProvider) {
            try P.parse("https://www.openstreetmap.org/directions?from=A&to=B")
        }
    }

    @Test func garbageTextThrowsNotCrashes() {
        // Whatever it does, it must throw (never crash / never return a junk plan).
        #expect(throws: P.ParseError.self) { try P.parse("just some random words") }
    }

    @Test func googleWithNoEndpointsThrows() {
        // A Google maps URL with neither dir segments nor origin/destination.
        #expect(throws: P.ParseError.noEndpoints) {
            try P.parse("https://www.google.com/maps/@52.5,13.4,12z")
        }
    }
}
