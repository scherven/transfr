import SwiftUI

/// Minimal settings — the prototype's `#s-settings`. Theme override writes an
/// `@AppStorage` value (DESIGN.md §13.8); the rest are placeholders for the
/// data-source toggle and the offline/regions surfaces.
struct SettingsView: View {
    @AppStorage("themeOverride") private var themeOverride: String = "system"

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Theme", selection: $themeOverride) {
                    Text("System").tag("system")
                    Text("Light").tag("light")
                    Text("Dark").tag("dark")
                }
            }
            Section("Data source") {
                LabeledContent("Mode", value: "Bundled sample")
                Text("The API is still in progress. This build serves a bundled plan; point RootView at a LiveRepository to use the server.")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
            Section("About") {
                LabeledContent("Engine", value: "core/ platform graph")
                LabeledContent("Contracts", value: "TransfrCore")
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// Resolve the stored theme override to a `ColorScheme?` for `.preferredColorScheme`.
public enum ThemePreference {
    public static func colorScheme(for raw: String) -> ColorScheme? {
        switch raw { case "light": .light; case "dark": .dark; default: nil }
    }
}
