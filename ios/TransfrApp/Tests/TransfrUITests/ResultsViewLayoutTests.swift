import XCTest
import SwiftUI
import TransfrCore
@testable import TransfrUI

/// Layout tests for the two states the results screen only grew once nav went
/// instant (#17): the empty window before `/journeys` lands (skeletons), and a
/// search that failed *after* we'd already navigated there (error + recovery).
///
/// These host `ResultsView` in a real `UIWindow` and assert on the laid-out
/// `UIView` hierarchy. That is deliberate: `ImageRenderer` — the path
/// `PreparingWalksSnapshotTests` uses — does NOT lay out `ScrollView` content
/// headless, so a pixel snapshot of this screen comes back blank and would pass
/// no matter how broken the screen was. A hosted window lays the ScrollView out
/// for real (verified: the failed panel and both recovery buttons materialise as
/// hit-test views; the searching state materialises three skeleton cards), so the
/// frame-based assertions below actually fail if a branch renders nothing or the
/// wrong thing.
///
/// What these do NOT check: exact copy or colour (SwiftUI does not surface Text
/// content onto `UIView` props in this headless path). WHICH branch renders for
/// which `load` state is pinned separately, in `TripModelStreamingTests`.
final class ResultsViewLayoutTests: XCTestCase {

    @MainActor
    private func host(_ view: some View) -> UIView {
        let host = UIHostingController(rootView: view)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 780))
        window.rootViewController = host
        window.makeKeyAndVisible()
        host.view.frame = window.bounds
        host.view.setNeedsLayout()
        host.view.layoutIfNeeded()
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        return host.view
    }

    @MainActor
    private func descendants(_ v: UIView) -> [UIView] {
        v.subviews + v.subviews.flatMap(descendants)
    }

    /// Phase A: on the results screen, the search still out. Three skeleton cards
    /// lay out — and demonstrably no journey cards, because there are no journeys.
    @MainActor
    func testSearchingLaysOutThreeSkeletonCards() async throws {
        let repo = TripModelStreamingTests.GatedRepo()
        let model = TripModel(repository: repo)
        let planning = Task { await model.plan() }
        await repo.waitUntilCalled()

        XCTAssertEqual(model.load, .loading)
        XCTAssertTrue(model.journeys.isEmpty, "no fabricated journeys behind the skeletons")

        let all = descendants(host(ResultsView().environment(model).environment(SettingsStore())))
        // Each skeleton is a full-width Panel (~350pt wide) at the card height the
        // real JourneyCard settles into. Three of them = the "a few options" shape.
        let cards = all.filter { (340...360).contains($0.frame.width) && (108...122).contains($0.frame.height) }
        XCTAssertGreaterThanOrEqual(cards.count, 3,
            "three skeleton cards must lay out; got \(cards.count) card-sized views in \(all.count) descendants")

        await repo.open()
        await planning.value
    }

    /// The fetch failed after the instant nav. The error and BOTH recovery
    /// affordances — "Try again" and "Change the search" — lay out on this screen,
    /// because this is where the user already is; the failure is never a dead end.
    @MainActor
    func testFailedLaysOutErrorAndBothRecoveryButtons() async throws {
        let model = TripModel(repository: TripModelStreamingTests.FailingRepo())
        await model.plan()
        XCTAssertEqual(model.load, .failed("No connection to the planning service."))

        let all = descendants(host(ResultsView().environment(model).environment(SettingsStore())))
        // The two recovery buttons are the only full-width (~318pt inside the
        // Panel) button-height controls on the screen.
        let buttons = all.filter { (300...340).contains($0.frame.width) && (40...56).contains($0.frame.height) }
        XCTAssertGreaterThanOrEqual(buttons.count, 2,
            "both recovery buttons must lay out; got \(buttons.count) button-sized views in \(all.count) descendants")
        // And there is real content above them (the error label + explanation),
        // so the panel isn't just two bare buttons.
        XCTAssertGreaterThan(all.count, 8, "the error panel must render substantive content")
    }
}
