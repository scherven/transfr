import Foundation

extension Foundation.Bundle {
    static let module: Bundle = {
        let mainPath = Bundle.main.bundleURL.appendingPathComponent("TransfrApp_TransfrUI.bundle").path
        let buildPath = "/private/tmp/claude-501/-Users-simonchervenak-Documents-GitHub-transfr/4c697f26-fb6a-4253-8cfb-c9b810ff4395/scratchpad/merge-work/ios/TransfrApp/.build/arm64-apple-macosx/debug/TransfrApp_TransfrUI.bundle"

        let preferredBundle = Bundle(path: mainPath)

        guard let bundle = preferredBundle ?? Bundle(path: buildPath) else {
            // Users can write a function called fatalError themselves, we should be resilient against that.
            Swift.fatalError("could not load resource bundle: from \(mainPath) or \(buildPath)")
        }

        return bundle
    }()
}