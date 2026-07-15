import SwiftUI
import TransfrUI

/// The shipping app target's only source file.
///
/// The real app — every screen, the theme, the data layer — lives in the
/// `TransfrUI` library so it builds and previews without a simulator (see
/// ios/README.md). A library can't declare `@main`, so this shell is the one
/// place that does: it just re-hosts the library's `TransfrApp` scene, so there
/// is no app logic here to keep in sync.
@main
struct TransfrMain: App {
    var body: some Scene {
        TransfrApp().body
    }
}
