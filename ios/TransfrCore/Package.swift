// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TransfrCore",
    platforms: [
        // Matches the app's deployment target; the models themselves are
        // platform-agnostic (Foundation only), so macOS is listed too to keep
        // `swift test` fast on a dev machine without a simulator.
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "TransfrCore", targets: ["TransfrCore"]),
    ],
    targets: [
        // Pure value types + decoding + verdict logic. No UI, no networking side
        // effects beyond an injectable async client — so it tests without a
        // simulator and ports cleanly if the app is ever rebuilt (DESIGN.md §13.2).
        .target(
            name: "TransfrCore",
            path: "Sources/TransfrCore"
        ),
        .testTarget(
            name: "TransfrCoreTests",
            dependencies: ["TransfrCore"],
            path: "Tests/TransfrCoreTests",
            // The Python engine's own outputs are the golden corpus: contract
            // drift on the server side fails a Swift decode test immediately.
            resources: [.copy("Fixtures")]
        ),
    ]
)
