import Foundation
import TransfrCore

/// Resolves which `JourneyRepository` the app runs against — read from the process
/// environment so **no base URL or shared secret is baked into committed source**
/// (the key lives in `deploy/secrets/api_key`, gitignored). The Xcode scheme
/// injects these at run time; see `ios/project.yml`.
///
/// Now that `api/` is up this defaults to the **live** service (localhost dev
/// server), a deliberate flip from the old hard-coded `.sample`. Set
/// `TRANSFR_USE_SAMPLE=1` to force the bundled offline tier (handy for demos or a
/// flight), or point `TRANSFR_API_URL` at the tunnel for an on-device build.
///
///   TRANSFR_API_URL     base URL (default `http://localhost:5001`)
///   TRANSFR_API_KEY     shared secret sent as `X-API-Key` (nil ⇒ unsecured dev)
///   TRANSFR_USE_SAMPLE  "1"/"true" ⇒ ignore the above, serve bundled sample data
public enum AppConfig {
    /// The default dev server: a local `uvicorn api.main:app --port 5001`. The
    /// simulator shares the host network, so `localhost` reaches it directly.
    static let defaultBaseURL = URL(string: "https://observed-representative-abc-tigers.trycloudflare.com")!

    public static var repository: JourneyRepository {
        let env = ProcessInfo.processInfo.environment

        if isTruthy(env["TRANSFR_USE_SAMPLE"]) {
            return SampleRepository()
        }

        let url = env["TRANSFR_API_URL"]
            .flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty }
            .flatMap(URL.init(string:)) ?? defaultBaseURL
        let apiKey = env["TRANSFR_API_KEY"]?
            .trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty

        return LiveRepository(baseURL: url, apiKey: apiKey)
    }

    /// Dev affordance: when `TRANSFR_AUTOPLAN=1`, the input screen fires the default
    /// query on launch so you land straight on live results (skips retyping every
    /// run). Off by default; nothing user-facing.
    public static var autoplanOnLaunch: Bool {
        isTruthy(ProcessInfo.processInfo.environment["TRANSFR_AUTOPLAN"])
    }

    private static func isTruthy(_ value: String?) -> Bool {
        guard let v = value?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() else { return false }
        return v == "1" || v == "true" || v == "yes"
    }
}

private extension String {
    /// Self unless empty — lets `?? default` skip blank env values.
    var nonEmpty: String? { isEmpty ? nil : self }
}
