import Foundation
import Testing
@testable import TransfrCore

/// Unit tests for the pure offline-corpus rules (`StationCatalog`): CSV parsing of
/// the bundled `id;name;country;latitude;longitude` schema, and the ranked,
/// case/diacritic-insensitive autocomplete search. No bundle, no UI — the app-layer
/// `SampleRepository` just loads the bundled file and calls these.
struct StationCatalogTests {
    /// A tiny stand-in for the real bundled CSV: a header, a few real stations
    /// (`is_main` = `1` for the Hbf main stations), a row with no coordinates, a
    /// blank line, and a row with no name (both dropped).
    private let sampleCSV = """
    id;name;country;latitude;longitude;is_main
    1;Berlin Hbf;DE;52.5251;13.3694;1
    2;Berlin Südkreuz;DE;52.4752;13.3654;
    3;München;DE;48.1400;11.5500;
    4;München Hbf;DE;48.1402;11.5586;1
    5;Göttingen;DE;51.5366;9.9266;
    6;Paris Gare de Lyon;FR;;;
    7;;FR;1.0;2.0;

    """

    // MARK: Parsing

    @Test func parseSkipsHeaderBlankAndNamelessRows() {
        let cat = StationCatalog(csv: sampleCSV)
        // 7 data rows, but the nameless row (id 7) and the blank line are dropped.
        #expect(cat.stations.count == 6)
        #expect(cat.stations.map(\.name) == [
            "Berlin Hbf", "Berlin Südkreuz", "München", "München Hbf", "Göttingen", "Paris Gare de Lyon",
        ])
    }

    @Test func parsePopulatesEveryContractField() {
        let cat = StationCatalog(csv: sampleCSV)
        let berlin = cat.stations[0]
        #expect(berlin.id == "1")
        #expect(berlin.name == "Berlin Hbf")
        #expect(berlin.country == "DE")
        #expect(berlin.latitude == 52.5251)
        #expect(berlin.longitude == 13.3694)
    }

    @Test func parseLeavesMissingCoordinatesNil() {
        let cat = StationCatalog(csv: sampleCSV)
        let paris = cat.stations.first { $0.name == "Paris Gare de Lyon" }
        #expect(paris?.latitude == nil)
        #expect(paris?.longitude == nil)
        #expect(paris?.country == "FR")
    }

    @Test func emptyOrHeaderOnlyCSVYieldsNoStations() {
        #expect(StationCatalog(csv: "").stations.isEmpty)
        #expect(StationCatalog(csv: "id;name;country;latitude;longitude;is_main").stations.isEmpty)
    }

    @Test func parseToleratesMissingIsMainColumn() {
        // A 5-column row (no is_main) still parses; the station just ranks as
        // non-main, so ordering falls back to length. No crash on the short row.
        let cat = StationCatalog(csv: """
        id;name;country;latitude;longitude
        1;Aachen Hbf;DE;50.7678;6.0919
        2;Aachen West;DE;50.7803;6.0700
        """)
        #expect(cat.stations.count == 2)
        #expect(cat.search("aachen").map(\.name) == ["Aachen Hbf", "Aachen West"])
    }

    // MARK: Search

    @Test func searchRanksWholeNamePrefixBeforeSubstring() {
        let cat = StationCatalog(csv: sampleCSV)
        // "berlin" prefixes two names; "München"/"Paris" don't contain it → excluded.
        let hits = cat.search("berlin").map(\.name)
        #expect(hits == ["Berlin Hbf", "Berlin Südkreuz"], "shorter name breaks the tie first")
    }

    @Test func searchIsCaseInsensitive() {
        let cat = StationCatalog(csv: sampleCSV)
        #expect(cat.search("BERLIN HBF").map(\.name) == ["Berlin Hbf"])
    }

    @Test func searchIsDiacriticInsensitive() {
        let cat = StationCatalog(csv: sampleCSV)
        // ASCII query matches accented names both ways ("München"/"München Hbf").
        #expect(Set(cat.search("munchen").map(\.name)) == ["München", "München Hbf"])
        #expect(cat.search("gottingen").map(\.name) == ["Göttingen"])
    }

    @Test func searchFloatsMainStationAboveShorterNonMain() {
        let cat = StationCatalog(csv: sampleCSV)
        // "München" (7 chars, not main) is shorter than "München Hbf" (main), but the
        // main-station tiebreak floats the Hbf to the top — matching /stations.
        #expect(cat.search("munchen").map(\.name) == ["München Hbf", "München"])
    }

    @Test func searchMatchesSubstringNotJustPrefix() {
        let cat = StationCatalog(csv: sampleCSV)
        // "gare" only appears mid-name; a prefix search would miss it.
        #expect(cat.search("gare").map(\.name) == ["Paris Gare de Lyon"])
    }

    @Test func searchHonoursTheLimit() {
        let cat = StationCatalog(csv: sampleCSV)
        // Both "Berlin …" rows match; a limit of 1 keeps only the top-ranked one.
        #expect(cat.search("berlin", limit: 1).map(\.name) == ["Berlin Hbf"])
    }

    @Test func blankQueryYieldsNothing() {
        let cat = StationCatalog(csv: sampleCSV)
        #expect(cat.search("").isEmpty)
        #expect(cat.search("   ").isEmpty)
    }

    @Test func noMatchYieldsEmpty() {
        let cat = StationCatalog(csv: sampleCSV)
        #expect(cat.search("zzzznowhere").isEmpty)
    }
}
