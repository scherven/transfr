import XCTest
import TransfrCore
@testable import TransfrUI

/// The display rules screens share (`Formatting.swift`): the relative-day labels,
/// the "did we actually measure this walk" guard behind every spare-time figure, and
/// the worst-wins one-liner two screens summarise a journey with. Each of these
/// shipped wrong in a way no renderer test would catch — the copy was well-formed,
/// it just wasn't true.
final class FormattingTests: XCTestCase {

    // MARK: - Relative day, forwards (Fmt.relativeFutureDay)

    /// A departure reads forwards. `relativeDay` reads *backwards* (it labels when a
    /// past search was run), so pointing it at a departure three days out named the
    /// weekday of a day that has already gone. Same 6-day weekday window as its
    /// mirror, for the same reason: at 7 the name repeats today's.
    func testRelativeFutureDayLabels() {
        let cal = Calendar.current
        let now = cal.date(from: DateComponents(year: 2026, month: 7, day: 17, hour: 12))!   // a Friday
        func ahead(_ days: Int) -> Date { cal.date(byAdding: .day, value: days, to: now)! }

        XCTAssertEqual(Fmt.relativeFutureDay(now, now: now), "Today")
        XCTAssertEqual(Fmt.relativeFutureDay(ahead(1), now: now), "Tomorrow")
        XCTAssertEqual(Fmt.relativeFutureDay(ahead(2), now: now), "Sun", "inside the week: weekday name")
        XCTAssertEqual(Fmt.relativeFutureDay(ahead(6), now: now), "Thu", "6 days out is still a weekday")
        XCTAssertEqual(Fmt.relativeFutureDay(ahead(7), now: now), "24 Jul", "7 days out falls back to a date")
        XCTAssertEqual(Fmt.relativeFutureDay(ahead(40), now: now), "26 Aug")
    }

    /// Later the same day is still "Today" — the label is about the day, not the hour.
    func testRelativeFutureDayIsDayGranular() {
        let cal = Calendar.current
        let now = cal.date(from: DateComponents(year: 2026, month: 7, day: 17, hour: 8, minute: 34))!
        let tonight = cal.date(from: DateComponents(year: 2026, month: 7, day: 17, hour: 23, minute: 55))!
        XCTAssertEqual(Fmt.relativeFutureDay(tonight, now: now), "Today")
    }

    /// The picker allows a departure in the past, so the formatter has to survive one:
    /// it defers to `relativeDay` rather than calling yesterday a coming weekday.
    func testRelativeFutureDayFallsBackForPastDates() {
        let cal = Calendar.current
        let now = cal.date(from: DateComponents(year: 2026, month: 7, day: 17, hour: 12))!
        func ago(_ days: Int) -> Date { cal.date(byAdding: .day, value: -days, to: now)! }
        XCTAssertEqual(Fmt.relativeFutureDay(ago(1), now: now), "Yesterday")
        XCTAssertEqual(Fmt.relativeFutureDay(ago(2), now: now), "Wed")
    }

    // MARK: - Spare time (Transfer.spareSeconds)

    /// Spare time is only shown for a walk we actually measured.
    ///
    /// The server returns `walk_time_s: 0.0` for a walk it couldn't measure (verified
    /// at Utrecht Centraal 5→7, platforms ~13 m apart). Guarding on nil alone
    /// computed `layover − 0` and printed "Spare 2m · Comfortable" beside a walk time
    /// the same card was rendering as "—".
    func testSpareSecondsNeedsAMeasuredWalk() {
        func transfer(walk: Double?, layover: Double? = 300) -> Transfer {
            Transfer(layoverS: layover, walkTimeS: walk, verdict: "feasible")
        }
        XCTAssertNil(transfer(walk: nil).spareSeconds, "no walk time at all")
        XCTAssertNil(transfer(walk: 0).spareSeconds, "0.0 is the server's unmeasured walk, not an instant one")
        XCTAssertEqual(transfer(walk: 120).spareSeconds, 180, "a real walk still yields real slack")
        XCTAssertNil(transfer(walk: 120, layover: nil).spareSeconds, "no layover to spend")

        // The same 0-is-unknown rule the formatters apply — the two must not disagree,
        // since disagreeing is precisely what put a spare figure under a "—".
        XCTAssertEqual(Fmt.walkTime(0), "—")
        XCTAssertEqual(Fmt.distance(0), "—")
    }

    // MARK: - The shared journey summary (Collection<Transfer>.verdictSummary)

    /// Worst-wins, and an `infeasible` is the worst. It used to be counted by nobody:
    /// the results card counted pending / unknown / tight and fell through to "all
    /// clear", so a journey with a missed change announced itself as fine (in red).
    func testVerdictSummaryIsWorstWins() {
        func t(_ verdict: String, reason: String? = nil) -> Transfer {
            Transfer(layoverS: 300, walkTimeS: 60, verdict: verdict, reason: reason)
        }
        XCTAssertEqual([t("feasible"), t("feasible")].verdictSummary, "all clear")
        XCTAssertEqual([t("feasible"), t("tight")].verdictSummary, "1 tight")
        XCTAssertEqual([t("unknown", reason: "no_platform_data"), t("tight")].verdictSummary, "1 unknown",
                       "an unassessable change outranks a tight one")
        XCTAssertEqual([t("infeasible"), t("tight")].verdictSummary, "1 won't make it",
                       "a missed change outranks everything settled")
        XCTAssertEqual([t("infeasible"), t("unknown")].verdictSummary, "1 won't make it")
        XCTAssertEqual([t("infeasible"), t("infeasible")].verdictSummary, "2 won't make it")
        XCTAssertEqual([t("infeasible"), t("pending")].verdictSummary, "checking…",
                       "nothing is claimed while a verdict is still streaming")
    }
}
