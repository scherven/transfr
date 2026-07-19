import Foundation
import Testing
@testable import TransfrCore

/// The pure on-disk cache primitive behind `CachingRepository` (#37): write a
/// `Codable` value under a namespaced key, read it back. No repository, no UI, no
/// simulator — the decorator's offline behaviour is exercised in TransfrUITests;
/// here we pin the storage contract headlessly (write-through / hit / miss /
/// overwrite / namespace isolation / survives a relaunch), the same split as
/// `RecentSearch`'s pure rules vs the app's `RecentSearchStore`.
struct JourneyCacheTests {
    /// A cache over a fresh, empty temp directory. The returned `root` is removed by
    /// the caller so a run leaves nothing behind.
    private func tempCache() -> (cache: JourneyCache, root: URL) {
        let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("JourneyCacheTests-\(UUID().uuidString)", isDirectory: true)
        return (JourneyCache(root: root), root)
    }

    private struct Payload: Codable, Equatable { var name: String; var count: Int }

    // MARK: Write-through + hit

    @Test func writeThenReadReturnsTheValue() {
        let (cache, root) = tempCache(); defer { try? FileManager.default.removeItem(at: root) }
        // A key with spaces, an arrow and an umlaut — the SHA-256 filename must make
        // any key string filesystem-safe.
        let value = Payload(name: "München Hbf", count: 42)
        cache.write(value, namespace: "journeys", key: "Hamburg Hbf → München Hbf")
        #expect(cache.read(Payload.self, namespace: "journeys", key: "Hamburg Hbf → München Hbf") == value)
    }

    // MARK: Miss

    @Test func readOfAbsentKeyIsNil() {
        let (cache, root) = tempCache(); defer { try? FileManager.default.removeItem(at: root) }
        #expect(cache.read(Payload.self, namespace: "journeys", key: "never written") == nil)
    }

    // MARK: Overwrite

    @Test func writeIsLastWriteWins() {
        let (cache, root) = tempCache(); defer { try? FileManager.default.removeItem(at: root) }
        cache.write(Payload(name: "old", count: 1), namespace: "walks", key: "k")
        cache.write(Payload(name: "new", count: 2), namespace: "walks", key: "k")
        #expect(cache.read(Payload.self, namespace: "walks", key: "k") == Payload(name: "new", count: 2))
    }

    // MARK: Namespace isolation

    @Test func namespacesAreIsolated() {
        let (cache, root) = tempCache(); defer { try? FileManager.default.removeItem(at: root) }
        cache.write(Payload(name: "j", count: 1), namespace: "journeys", key: "k")
        #expect(cache.read(Payload.self, namespace: "walks", key: "k") == nil,
                "the same key in a different namespace is a different entry")
    }

    // MARK: Filename derivation

    @Test func filenameIsDeterministicAndKeyed() {
        // Stable across calls (a later launch finds the same file) and distinct per
        // key (two queries don't collide onto one file).
        #expect(JourneyCache.filename(for: "k") == JourneyCache.filename(for: "k"))
        #expect(JourneyCache.filename(for: "a") != JourneyCache.filename(for: "b"))
    }

    // MARK: Durability across a "relaunch"

    /// A value written by one cache is read by a brand-new cache over the same root —
    /// a fresh app launch reading disk, which is the whole point of an offline cache.
    @Test func valueSurvivesANewCacheOverTheSameRoot() {
        let (cache, root) = tempCache(); defer { try? FileManager.default.removeItem(at: root) }
        cache.write(Payload(name: "persisted", count: 7), namespace: "journeys", key: "k")
        let reopened = JourneyCache(root: root)
        #expect(reopened.read(Payload.self, namespace: "journeys", key: "k") == Payload(name: "persisted", count: 7))
    }
}
