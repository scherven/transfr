import Testing
@testable import TransfrCore

/// The planned-vs-live rendering decision — the Swift mirror of
/// `api/transfers.py:platform_display`. Same cases the pytest suite pins, so the
/// two implementations can't drift.
struct PlatformDisplayTests {

    @Test func neitherPresentRendersNothing() {
        // The FR/IT/ES honest empty state — no number is invented.
        #expect(PlatformDisplay.make(live: nil, planned: nil) == .none)
        #expect(PlatformDisplay.make(live: "", planned: "") == .none)   // empty == absent
        #expect(PlatformDisplay.make(live: nil, planned: nil).shownNumber == nil)
    }

    @Test func plannedOnlyIsTheSchedulesGuess() {
        let d = PlatformDisplay.make(live: nil, planned: "5")
        #expect(d == .planned("5"))
        #expect(d.isPlannedGuess && !d.isChange)
        #expect(d.shownNumber == "5")
    }

    @Test func liveEqualsPlannedIsStillAGuess() {
        // Realtime hasn't moved it, so it's still only the schedule's assignment.
        let d = PlatformDisplay.make(live: "5", planned: "5")
        #expect(d == .planned("5"))
        #expect(d.isPlannedGuess)
    }

    @Test func liveDiffersFromPlannedIsAChange() {
        let d = PlatformDisplay.make(live: "8", planned: "5")
        #expect(d == .changed(current: "8", from: "5"))
        #expect(d.isChange && !d.isPlannedGuess)
        #expect(d.shownNumber == "8")
    }

    @Test func liveOnlyIsConfirmed() {
        let d = PlatformDisplay.make(live: "5", planned: nil)
        #expect(d == .confirmed("5"))
        #expect(!d.isChange && !d.isPlannedGuess)
    }

    // `actual` (the renumber correction, an orthogonal axis) relabels the LIVE
    // platform for display but must never be read as a change on its own.

    @Test func actualRelabelsButIsNotAChange() {
        // Köln: feed "89" == planned "89", real sign "7" ⇒ planned "7", no change.
        #expect(PlatformDisplay.make(live: "89", planned: "89", actual: "7") == .planned("7"))
        #expect(PlatformDisplay.make(live: "89", planned: nil, actual: "7") == .confirmed("7"))
    }

    @Test func changeAndRenumberCompose() {
        // A genuine re-track AND a renumber: show the real sign, from the planned.
        #expect(PlatformDisplay.make(live: "9", planned: "5", actual: "7")
                == .changed(current: "7", from: "5"))
    }
}
