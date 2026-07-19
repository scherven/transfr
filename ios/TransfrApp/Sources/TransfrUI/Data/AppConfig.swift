import Foundation
import TransfrCore

/// Resolves which `JourneyRepository` the app runs against — read from the process
/// environment so **no base URL or shared secret is baked into committed source**
/// (the key lives in `deploy/secrets/api_key`, gitignored). The Xcode scheme
/// injects these at run time; see `ios/project.yml`.
///
/// Now that `api/` is deployed this defaults to the **live** service at the stable
/// named-tunnel hostname, a deliberate flip from the old hard-coded `.sample`. Set
/// `TRANSFR_USE_SAMPLE=1` to force the bundled offline tier (handy for demos or a
/// flight), or point `TRANSFR_API_URL` at `http://localhost:5001` for a local dev
/// server. The URL is not a secret and is committed; the key never is.
///
///   TRANSFR_API_URL     base URL (default `https://api.trans-fr.com`)
///   TRANSFR_API_KEY     shared secret sent as `X-API-Key` (from deploy/secrets/api_key)
///   TRANSFR_USE_SAMPLE  "1"/"true" ⇒ ignore the above, serve bundled sample data
public enum AppConfig {
    /// The deployed service: the stable Cloudflare named tunnel in front of the
    /// always-on host's `uvicorn api.main:app` (see deploy/launchd/). Overridable
    /// with `TRANSFR_API_URL` (e.g. `http://localhost:5001` against a local server).
    static let defaultBaseURL = URL(string: "https://api.trans-fr.com")!

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

        // Wrap the live service in the offline cache (#37, DESIGN.md §13.9): each
        // planned trip + its walk geometry is persisted on fetch, so reopening a
        // planned trip works with no signal. Transparent — live always wins; the
        // cache is only read when the network fails. The sample tier is already
        // fully offline, so it isn't wrapped.
        return CachingRepository(wrapping: LiveRepository(baseURL: url, apiKey: apiKey))
    }

    /// Dev affordance: when `TRANSFR_AUTOPLAN=1`, the input screen plans a query on
    /// launch so you land straight on live results (skips retyping every run). Off
    /// by default; nothing user-facing.
    public static var autoplanOnLaunch: Bool {
        isTruthy(ProcessInfo.processInfo.environment["TRANSFR_AUTOPLAN"])
    }

    /// The endpoints autoplan fires, or nil when it's off or either end is unset.
    /// The app ships no example query — the fields start empty — so a dev supplies
    /// the route per run, the same way the base URL and key are supplied, and no
    /// example route is baked into committed source.
    ///
    ///   TRANSFR_AUTOPLAN_FROM / TRANSFR_AUTOPLAN_TO   e.g. "Hamburg Hbf" / "Stuttgart Hbf"
    public static var autoplanQuery: (from: String, to: String)? {
        guard autoplanOnLaunch else { return nil }
        let env = ProcessInfo.processInfo.environment
        guard let from = env["TRANSFR_AUTOPLAN_FROM"]?
                .trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty,
              let to = env["TRANSFR_AUTOPLAN_TO"]?
                .trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty
        else { return nil }
        return (from, to)
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
