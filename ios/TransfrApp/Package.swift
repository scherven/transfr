// swift-tools-version: 6.0
import PackageDescription

// The SwiftUI client. `TransfrUI` is a UI library (screens, theme, the observable
// trip model, and the agnostic data layer) so it compiles and previews without a
// full app target and depends only on `TransfrCore` for the wire contracts.
//
// The shipping app is a thin Xcode app target that imports `TransfrUI` and hosts
// `RootView` (see App/ and ios/README.md). Keeping the UI in a package means the
// whole surface builds via `xcodebuild -scheme TransfrUI` on the Simulator SDK,
// exactly like `TransfrCore`.
let package = Package(
    name: "TransfrApp",
    platforms: [
        .iOS(.v17),
    ],
    products: [
        .library(name: "TransfrUI", targets: ["TransfrUI"]),
    ],
    dependencies: [
        .package(path: "../TransfrCore"),
    ],
    targets: [
        .target(
            name: "TransfrUI",
            dependencies: [
                .product(name: "TransfrCore", package: "TransfrCore"),
            ],
            path: "Sources/TransfrUI",
            // A bundled journeys plan so the UI runs against a real contract shape
            // with no server (the `.sample` repository). Same JSON schema the API
            // serves, so swapping in `.live` is a one-line change.
            resources: [.process("Resources")]
        ),
    ]
)
