import SwiftUI
import TransfrCore

/// The app shell. A value-driven `NavigationStack` (DESIGN.md §13.1): the input
/// screen is the root, and every push is a `Route` case, so the default
/// push/pop *is* the prototype's forward/back slide. Swap the repository passed
/// to `TripModel` (sample ↔ live) and nothing here changes.
public struct RootView: View {
    @State private var model: TripModel
    @State private var settings = SettingsStore()
    @State private var location = LocationManager()
    /// Cold-launch animation gate. True on first creation of the shell (once per
    /// app launch), flipped to false when the launch mark finishes — a plain
    /// crossfade to InputView underneath. The app content is always present below
    /// the overlay, so nothing here can block or gate the app if the mark fails.
    @State private var showLaunch = true

    /// Inject any `JourneyRepository`. Defaults to the bundled sample tier so the
    /// app is runnable with no server (the API is still in progress).
    public init(repository: JourneyRepository = SampleRepository()) {
        _model = State(initialValue: TripModel(repository: repository))
    }

    public var body: some View {
        NavigationStack(path: $model.path) {
            InputView()
                .navigationDestination(for: Route.self) { route in
                    switch route {
                    case .results:              ResultsView()
                    case .journey:              JourneyView()
                    case .preparingWalks(let start): PreparingWalksView(startIndex: start)
                    case .carousel(let start):  CarouselView(startIndex: start)
                    case .walk(let idx):        WalkView(transferIndex: idx)
                    case .ar(let idx):          ARView(transferIndex: idx)
                    case .live:                 LiveView()
                    case .walkLookup:           WalkLookupView()
                    case .settings:             SettingsView()
                    case .attributions:         AttributionsView()
                    case .advanced:             AdvancedView()
                    case .stationWalk:          StationWalkView()
                    case .nearestFacility:      NearestFacilityView()
                    case .mapHealth:            MapHealthView()
                    case .offlineRegions:       OfflineRegionsView()
                    }
                }
        }
        .environment(model)
        .environment(settings)
        .environment(location)
        .tint(Theme.accent)
        // The launch overlay reads InputView's "transfr" title frame (published as
        // a bounds anchor) so the finished mark can fly up and settle onto it —
        // then a crossfade reveals the identical (blue) title underneath.
        .overlayPreferenceValue(WordmarkAnchorKey.self) { anchor in
            GeometryReader { proxy in
                if showLaunch {
                    LaunchView(targetRect: anchor.map { proxy[$0] }) {
                        withAnimation(.easeOut(duration: 0.35)) { showLaunch = false }
                    }
                    .transition(.opacity)
                }
            }
            .ignoresSafeArea()
        }
        .preferredColorScheme(settings.theme.colorScheme)
    }
}
