import SwiftUI

/// Full settings — the prototype's `#s-settings` (§6.8), grouped: Getting around ·
/// Making the connection · Appearance · On the move · Power tools · About. Every
/// control is bound to `SettingsStore`, which persists each change on `didSet`, so
/// this view carries no persistence lifecycle of its own; the Theme control drives
/// `.preferredColorScheme` for real (via `RootView`). Whether each preference yet
/// affects routing is tracked in the repo-root `TODO.md` (§6).
struct SettingsView: View {
    @Environment(SettingsStore.self) private var s

    var body: some View {
        @Bindable var s = s
        ScrollView {
            VStack(spacing: 0) {
                SectionHeader(text: "Getting around")
                SettingRow(icon: "figure.stairs", title: "Avoid lifts") {
                    TransfrToggle(isOn: $s.avoidElevators)
                }.padding(.bottom, 8)
                SettingStack(icon: "figure.walk.motion", title: "Walking pace") {
                    SegmentedControl(options: SettingsStore.Pace.allCases, selection: $s.pace) { $0.label }
                }.padding(.bottom, 8)
                SettingRow(icon: "stairs", title: "Prefer escalators",
                           subtitle: "Over stairs where there's a choice") {
                    TransfrToggle(isOn: $s.preferEscalators)
                }

                SectionHeader(text: "Making the connection")
                makeableCard
                SettingStack(icon: "clock", title: "Boarding buffer",
                             subtitle: "Spare time kept to reach the doors") {
                    SegmentedControl(options: [30, 60, 90], selection: $s.bufferS) { "\($0)s" }
                }

                SectionHeader(text: "Appearance")
                SettingStack(icon: "moon.stars", title: "Theme") {
                    SegmentedControl(options: SettingsStore.ThemeMode.allCases, selection: $s.theme) { $0.label }
                }.padding(.bottom, 8)
                SettingStack(icon: "ruler", title: "Distance units") {
                    SegmentedControl(options: SettingsStore.Units.allCases, selection: $s.units) { $0.label }
                }

                SectionHeader(text: "On the move")
                SettingRow(icon: "iphone", title: "Live Activity",
                           subtitle: "Next transfer on your lock screen") {
                    TransfrToggle(isOn: $s.liveActivity)
                }.padding(.bottom, 8)
                SettingStack(icon: "viewfinder", title: "Open AR automatically",
                             subtitle: "Prompt to raise your phone before arrival") {
                    SegmentedControl(options: [0, 60, 90], selection: $s.autoARLeadS) {
                        $0 == 0 ? "Off" : "\($0)s"
                    }
                }

                SectionHeader(text: "About")
                NavRow(icon: "info.circle", title: "Attributions & data sources",
                       subtitle: "Map data © OpenStreetMap contributors · licences",
                       route: .attributions)
            }
            .padding(20)
        }
        // The page never *scrolls* sideways -- content width already equals the
        // container's -- but it still rubber-bands sideways and springs back (#32).
        // `axes:` defaults to `[.vertical]`, so the older `.scrollBounceBehavior(
        // .basedOnSize)` never governed this axis. `.basedOnSize` bounces only when
        // the content overflows, which horizontally it never does -> travel is zero.
        .scrollBounceBehavior(.basedOnSize, axes: .horizontal)
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var makeableCard: some View {
        @Bindable var s = s
        return SetCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline) {
                    Text("\u{201C}Makeable\u{201D} cut-off").font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                    Spacer()
                    Text("\(s.makeablePct)%")
                        .font(.system(size: 16, weight: .bold, design: .monospaced))
                        .foregroundStyle(Theme.accent)
                }
                Text("Makeable when the walk uses under this share of the layover.")
                    .font(.system(size: 11.5)).foregroundStyle(Theme.ink3)
                    .padding(.top, 5).padding(.bottom, 12)

                Slider(value: Binding(
                    get: { Double(s.makeablePct) },
                    set: { s.makeablePct = Int($0.rounded()) }
                ), in: 1...100)
                .tint(Theme.accent)

                ZoneBar(zones: [
                    (s.makeablePct, Theme.go),
                    (100 - s.makeablePct, Theme.tight),
                    (20, Theme.miss),
                ]).padding(.top, 6)

                HStack {
                    Text("makeable"); Spacer(); Text("tight"); Spacer(); Text("miss")
                }
                .font(.system(size: 10.5)).foregroundStyle(Theme.ink3).padding(.top, 6)

                (Text("On an 8 minute connection, that's up to ")
                 + Text(s.makeableExample).font(.system(size: 12, weight: .semibold, design: .monospaced)).foregroundColor(Theme.ink)
                 + Text(" of walking before it's flagged."))
                    .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).padding(.top, 10)
            }
        }
    }
}
