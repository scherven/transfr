import SwiftUI
import TransfrCore

/// The app entry. This lives in the `TransfrUI` library so the whole surface
/// compiles via `xcodebuild -scheme TransfrUI`; the shipping Xcode app target is
/// a one-file shell that re-declares this `@main` and imports `TransfrUI` (see
/// ios/README.md). Flip `Self.repository` from `.sample` to `.live(...)` when the
/// API is ready — no view changes.
public struct TransfrApp: App {
    @AppStorage("themeOverride") private var themeOverride: String = "system"

    public init() {}

    public var body: some Scene {
        WindowGroup {
            RootView(repository: Self.repository)
                .preferredColorScheme(ThemePreference.colorScheme(for: themeOverride))
        }
    }

    /// The single switch between the offline sample tier and the live service.
    /// Default is `.sample` because `api/` is still in progress.
    static var repository: JourneyRepository {
        .sample
        // .live(URL(string: "http://localhost:5001")!)
    }
}

public extension JourneyRepository where Self == SampleRepository {
    /// Bundled, offline. Runs the whole app with no server.
    static var sample: SampleRepository { SampleRepository() }
}

public extension JourneyRepository where Self == LiveRepository {
    /// The real FastAPI service at `baseURL`.
    static func live(_ baseURL: URL) -> LiveRepository { LiveRepository(baseURL: baseURL) }
}
