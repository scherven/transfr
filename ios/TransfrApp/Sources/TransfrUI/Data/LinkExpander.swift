import Foundation
import TransfrCore

/// Follows a short-link redirect (e.g. `maps.app.goo.gl/…`) to the real URL. This
/// is the one runtime, network half of "Paste link": the *parsing* stays pure in
/// `TransfrCore.RouteLinkParser` (unit-tested with fixed strings), and only the
/// redirect hop lives here. Behind a protocol so `TripModel` can inject a stub.
public protocol LinkExpanding: Sendable {
    /// Resolve `url` to the URL it ultimately redirects to (or itself if it doesn't).
    func expand(_ url: URL) async throws -> URL
}

/// Real expander: issues a GET and follows redirects, but **stops before
/// downloading the destination page** — the moment a redirect points at a
/// non-short URL we capture it and cancel the chain, so we never pull the (large)
/// Maps HTML, only the `/maps/dir/…` Location. `URLSession` follows the 302 for us.
public struct URLSessionLinkExpander: LinkExpanding {
    public init() {}

    public func expand(_ url: URL) async throws -> URL {
        let catcher = RedirectCatcher()
        var req = URLRequest(url: url, timeoutInterval: 12)
        req.httpMethod = "GET"
        // A desktop UA — some shorteners vary the target for unknown agents.
        req.setValue("Mozilla/5.0 (compatible; TransfrApp/1.0)", forHTTPHeaderField: "User-Agent")
        let (_, response) = try await URLSession.shared.data(for: req, delegate: catcher)
        return catcher.finalURL ?? response.url ?? url
    }
}

/// Records the last redirect target and stops the chain once it leaves the
/// short-link hosts (so multi-hop shorteners still resolve, but the big
/// destination page is never fetched). Delegate callbacks land on a background
/// queue, so the captured URL is guarded by a lock.
private final class RedirectCatcher: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var _final: URL?
    var finalURL: URL? { lock.withLock { _final } }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        guard let target = request.url else { completionHandler(request); return }
        if RouteLinkParser.isShortLink(target.absoluteString) {
            completionHandler(request)                 // keep following short → short hops
        } else {
            lock.withLock { _final = target }
            completionHandler(nil)                       // stop before the destination page
        }
    }
}
