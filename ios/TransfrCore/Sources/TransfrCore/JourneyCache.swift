import Foundation
import CryptoKit

/// A tiny durable JSON store: persist any `Codable` value under a namespaced string
/// key and read it back on a later launch. This is the persistence primitive behind
/// the app's `CachingRepository` (#37, DESIGN.md §13.9) — it has no knowledge of the
/// `JourneyRepository` seam, so its write-through / hit / miss behaviour is
/// unit-tested headlessly here in `TransfrCore` (the same split as `RecentSearch`'s
/// pure rules vs the app's `RecentSearchStore`).
///
/// A key can be any string (station names carry spaces, slashes, umlauts — "Frankfurt
/// (Main) Hbf"), so the on-disk filename is a SHA-256 hex of the key: filesystem-safe
/// and stable across launches (unlike Swift's per-launch-seeded `Hasher`). `namespace`
/// is a subdirectory, keeping different value kinds (planned journeys vs walk
/// geometry) apart. It is a **cache**: writes are best-effort — any failure (encode or
/// disk) is swallowed, so persisting a value never breaks the fetch it shadows, and a
/// later miss just re-fetches. Matches `RecentSearchStore`/`SettingsStore`'s `try?`
/// persistence.
///
/// The coder is a plain matched `JSONEncoder`/`JSONDecoder` pair, not the snake_case
/// `TransfrJSON` one: the payload is private (Swift → disk → Swift, never the wire),
/// so a matched pair round-trips any `Codable` — including the nested `VizExport`
/// geometry — with no key-strategy caveats.
public struct JourneyCache: Sendable {
    /// Root directory under which namespaced entries are written. Injected, so the
    /// app points it at `Caches/` (see `CachingRepository`) and a test at a temp dir.
    public let root: URL

    public init(root: URL) {
        self.root = root
    }

    /// Persist `value` under `namespace`/`key`, overwriting any previous entry
    /// (last-write-wins). Best-effort: any failure (encode or disk) is swallowed, so
    /// a cache write never breaks the fetch it shadows.
    public func write<Value: Encodable>(_ value: Value, namespace: String, key: String) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        let dir = root.appendingPathComponent(namespace, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try? data.write(to: fileURL(dir: dir, key: key), options: .atomic)
    }

    /// The value previously written under `namespace`/`key`, or nil if none exists —
    /// or it no longer decodes (a shape change invalidates the entry safely rather
    /// than throwing).
    public func read<Value: Decodable>(_ type: Value.Type, namespace: String, key: String) -> Value? {
        let dir = root.appendingPathComponent(namespace, isDirectory: true)
        guard let data = try? Data(contentsOf: fileURL(dir: dir, key: key)) else { return nil }
        return try? JSONDecoder().decode(Value.self, from: data)
    }

    private func fileURL(dir: URL, key: String) -> URL {
        dir.appendingPathComponent(Self.filename(for: key)).appendingPathExtension("json")
    }

    /// SHA-256 hex of the key — a stable, filesystem-safe filename for any key string
    /// (deterministic across launches, so a later launch finds the same file).
    static func filename(for key: String) -> String {
        SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}
