import SwiftUI
import TransfrCore

/// The app entry. This lives in the `TransfrUI` library so the whole surface
/// compiles via `xcodebuild -scheme TransfrUI`; the shipping Xcode app target is
/// a one-file shell that re-declares this `@main` and imports `TransfrUI` (see
/// ios/README.md).
public struct TransfrApp: App {
    public init() {}

    public var body: some Scene {
        WindowGroup {
            // RootView owns the SettingsStore and applies the theme override.
            RootView(repository: Self.repository)
        }
    }

    /// The live service by default now that `api/` is up. `AppConfig` reads the
    /// base URL / key from the environment (injected by the Xcode scheme), so no
    /// secret is committed and `TRANSFR_USE_SAMPLE=1` still forces the offline
    /// tier — no view changes either way.
    static var repository: JourneyRepository {
        AppConfig.repository
    }
}

public extension JourneyRepository where Self == SampleRepository {
    /// Bundled, offline. Runs the whole app with no server.
    static var sample: SampleRepository { SampleRepository() }
}

public extension JourneyRepository where Self == LiveRepository {
    /// The real FastAPI service at `baseURL`. Pass `apiKey` for the deployed
    /// (tunnelled) service, which requires the `X-API-Key` header.
    static func live(_ baseURL: URL, apiKey: String? = nil) -> LiveRepository {
        LiveRepository(baseURL: baseURL, apiKey: apiKey)
    }
}
