import XCTest
import TransfrCore
@testable import TransfrUI

/// The app-layer persistence wrapper (#38): `RecentSearchStore` writes the history
/// through to `UserDefaults` (like `SettingsStore`) and reloads it on init. Each
/// test runs against an isolated `UserDefaults` suite so it never touches
/// `.standard`, and proves the roundtrip a fresh launch depends on. The pure
/// dedup / cap / ordering rules are unit-tested headlessly in `TransfrCoreTests`.
///
/// `@MainActor` sits on each test (not the class) — the store is main-actor
/// isolated, but `setUp`/`tearDown` override nonisolated superclass methods and so
/// can't touch isolated state. Mirrors `TripModelStreamingTests`.
final class RecentSearchStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "RecentSearchStoreTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil; suiteName = nil
        super.tearDown()
    }

    /// A fresh install starts with a genuinely empty history and no seeded
    /// examples. The "Recent" section renders `items` and nothing else, so this is
    /// the invariant that keeps fabricated rows out of the UI: no history means no
    /// rows, and the section shows its empty state instead.
    @MainActor
    func testFreshStoreHasNoSeededHistory() {
        XCTAssertTrue(RecentSearchStore(defaults: defaults).items.isEmpty,
                      "a fresh store must seed nothing, not example routes")
    }

    @MainActor
    func testRecordPersistsAcrossReload() {
        let store = RecentSearchStore(defaults: defaults)
        store.record(origin: "Hamburg Hbf", destination: "Stuttgart Hbf")
        store.record(origin: "Berlin Hbf", destination: "Basel SBB")

        // A brand-new store over the same suite = a fresh app launch reading disk.
        let reloaded = RecentSearchStore(defaults: defaults)
        XCTAssertEqual(reloaded.items.map(\.origin), ["Berlin Hbf", "Hamburg Hbf"],
                       "newest-first history survives a relaunch")
        XCTAssertEqual(reloaded.items.first?.destination, "Basel SBB")
    }

    @MainActor
    func testReRunDedupsAndBumpsToFront() {
        let store = RecentSearchStore(defaults: defaults)
        store.record(origin: "A", destination: "B")
        store.record(origin: "C", destination: "D")
        store.record(origin: "a", destination: " b ")   // same route, different spelling
        XCTAssertEqual(store.items.count, 2)
        XCTAssertEqual(store.items.map(\.origin), ["a", "C"])
    }

    @MainActor
    func testBlankEndpointsIgnored() {
        let store = RecentSearchStore(defaults: defaults)
        store.record(origin: "  ", destination: "Stuttgart Hbf")
        store.record(origin: "Hamburg Hbf", destination: "")
        XCTAssertTrue(store.items.isEmpty, "a search needs both ends to be reusable")
    }

    @MainActor
    func testCapIsReappliedOnReload() {
        let big = RecentSearchStore(cap: 12, defaults: defaults)
        for i in 0..<5 { big.record(origin: "O\(i)", destination: "D\(i)") }
        XCTAssertEqual(big.items.count, 5)

        // A smaller build reading the same stored blob trims it rather than
        // surfacing an over-long list.
        let small = RecentSearchStore(cap: 2, defaults: defaults)
        XCTAssertEqual(small.items.count, 2)
        XCTAssertEqual(small.items.map(\.origin), ["O4", "O3"])
    }

    /// Mirrors `nonPositiveCapYieldsEmpty` in TransfrCoreTests: the store must apply
    /// the same "keep nothing" rule on *read* that `recordingSearch` applies on
    /// write — and must not trap doing it (`prefix` requires a non-negative length).
    @MainActor
    func testNonPositiveCapReadsAsEmpty() {
        let store = RecentSearchStore(defaults: defaults)
        store.record(origin: "A", destination: "B")
        XCTAssertTrue(RecentSearchStore(cap: 0, defaults: defaults).items.isEmpty)
        XCTAssertTrue(RecentSearchStore(cap: -1, defaults: defaults).items.isEmpty)
    }

    @MainActor
    func testClearWipesPersistedHistory() {
        let store = RecentSearchStore(defaults: defaults)
        store.record(origin: "A", destination: "B")
        store.clear()
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertTrue(RecentSearchStore(defaults: defaults).items.isEmpty,
                      "clear() is persisted, not just in-memory")
    }

    // MARK: - The "when" label on a recent row (Fmt.relativeDay)

    /// The label each history row shows. Pins the weekday/date boundary: a weekday
    /// name is only unambiguous up to 6 days back — at 7 it would repeat today's.
    func testRelativeDayLabels() {
        let cal = Calendar.current
        let now = cal.date(from: DateComponents(year: 2026, month: 7, day: 17, hour: 12))!
        func ago(_ days: Int) -> Date { cal.date(byAdding: .day, value: -days, to: now)! }

        XCTAssertEqual(Fmt.relativeDay(now, now: now), "Today")
        XCTAssertEqual(Fmt.relativeDay(ago(1), now: now), "Yesterday")
        XCTAssertEqual(Fmt.relativeDay(ago(2), now: now), "Wed", "inside the week: weekday name")
        XCTAssertEqual(Fmt.relativeDay(ago(6), now: now), "Sat", "6 days back is still a weekday")
        XCTAssertEqual(Fmt.relativeDay(ago(7), now: now), "10 Jul", "7 days back falls back to a date")
        XCTAssertEqual(Fmt.relativeDay(ago(40), now: now), "7 Jun")
    }
}
