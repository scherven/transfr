import Foundation
import Testing
@testable import TransfrCore

/// Unit tests for the pure recent-search history rules (`Array.recordingSearch`):
/// newest-first ordering, dedup by route, the bounded cap, and a `Codable`
/// roundtrip. No `UserDefaults`, no UI — the `RecentSearchStore` app-layer wrapper
/// just persists whatever these produce.
struct RecentSearchTests {
    private func s(_ from: String, _ to: String, _ t: TimeInterval) -> RecentSearch {
        RecentSearch(origin: from, destination: to, date: Date(timeIntervalSince1970: t))
    }

    // MARK: Ordering

    @Test func newestFirst() {
        var history: [RecentSearch] = []
        history = history.recordingSearch(s("Hamburg Hbf", "Stuttgart Hbf", 1))
        history = history.recordingSearch(s("Berlin Hbf", "Basel SBB", 2))
        #expect(history.map(\.origin) == ["Berlin Hbf", "Hamburg Hbf"])
        #expect(history[0].destination == "Basel SBB")
    }

    // MARK: Dedup

    @Test func reRunningARouteMovesItToFrontWithoutDuplicating() {
        var history: [RecentSearch] = []
        history = history.recordingSearch(s("A", "B", 1))
        history = history.recordingSearch(s("C", "D", 2))
        history = history.recordingSearch(s("A", "B", 3))   // re-run the first route
        #expect(history.count == 2)
        #expect(history.map(\.origin) == ["A", "C"])
        #expect(history[0].date == Date(timeIntervalSince1970: 3), "the timestamp refreshes on re-run")
    }

    @Test func dedupIgnoresCaseAndSurroundingWhitespace() {
        var history: [RecentSearch] = []
        history = history.recordingSearch(s("Köln Hbf", "München Hbf", 1))
        history = history.recordingSearch(s("  köln hbf ", "MÜNCHEN HBF", 2))
        #expect(history.count == 1)
        #expect(history[0].origin == "  köln hbf ", "the newest spelling wins")
    }

    @Test func reversedRouteIsADistinctSearch() {
        var history: [RecentSearch] = []
        history = history.recordingSearch(s("A", "B", 1))
        history = history.recordingSearch(s("B", "A", 2))
        #expect(history.count == 2, "A→B and B→A are different trips")
    }

    // MARK: Cap

    @Test func capDropsTheOldest() {
        var history: [RecentSearch] = []
        for i in 0..<5 { history = history.recordingSearch(s("O\(i)", "D\(i)", TimeInterval(i)), cap: 3) }
        #expect(history.count == 3)
        #expect(history.map(\.origin) == ["O4", "O3", "O2"], "oldest two fell off the end")
    }

    @Test func nonPositiveCapYieldsEmpty() {
        let history = [s("A", "B", 1)].recordingSearch(s("C", "D", 2), cap: 0)
        #expect(history.isEmpty)
    }

    // MARK: Codable roundtrip

    @Test func codableRoundtripPreservesEntries() throws {
        let original = [s("Hamburg Hbf", "Stuttgart Hbf", 100), s("Berlin Hbf", "Basel SBB", 200)]
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode([RecentSearch].self, from: data)
        #expect(decoded == original)
    }

    @Test func idIsStableAcrossEncodeDecode() throws {
        let original = s("A", "B", 1)
        let decoded = try JSONDecoder().decode(RecentSearch.self, from: try JSONEncoder().encode(original))
        #expect(decoded.id == original.id, "identity is derived from the route, so it survives a roundtrip")
    }
}
