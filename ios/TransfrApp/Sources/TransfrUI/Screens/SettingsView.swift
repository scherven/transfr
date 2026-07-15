import SwiftUI

/// Full settings — the prototype's `#s-settings` (§6.8), grouped: Getting around ·
/// Making the connection · Appearance · On the move · Power tools · About. Every
/// control is bound to `SettingsStore` and persisted; the Theme control drives
/// `.preferredColorScheme` for real. Whether each preference yet affects routing
/// is tracked in `ios/SUI_TODO.md`.
struct SettingsView: View {
    @Environment(SettingsStore.self) private var s

    var body: some View {
        @Bindable var s = s
        ScrollView {
            VStack(spacing: 0) {
                SectionHeader(text: "Getting around")
                SettingRow(icon: "figure.walk", title: "Step-free routes",
                           subtitle: "Skip stairs — route via lifts and ramps") {
                    TransfrToggle(isOn: $s.stepFree)
                }.padding(.bottom, 8)
                SettingStack(icon: "figure.walk.motion", title: "Walking pace",
                             subtitle: "How fast we assume you move between platforms") {
                    SegmentedControl(options: SettingsStore.Pace.allCases, selection: $s.pace) { $0.label }
                }.padding(.bottom, 8)
                SettingRow(icon: "stairs", title: "Prefer escalators",
                           subtitle: "Use them over stairs where there's a choice") {
                    TransfrToggle(isOn: $s.preferEscalators)
                }

                SectionHeader(text: "Making the connection")
                makeableCard
                SettingStack(icon: "clock", title: "Boarding buffer",
                             subtitle: "Spare time kept to reach the doors") {
                    SegmentedControl(options: [30, 60, 90], selection: $s.bufferS) { "\($0)s" }
                }

                SectionHeader(text: "Appearance")
                SettingStack(icon: "moon.stars", title: "Theme",
                             subtitle: "Try it — this one actually switches") {
                    SegmentedControl(options: SettingsStore.ThemeMode.allCases, selection: $s.theme) { $0.label }
                }.padding(.bottom, 8)
                SettingStack(icon: "ruler", title: "Distance units",
                             subtitle: "Shown on walks and platforms") {
                    SegmentedControl(options: SettingsStore.Units.allCases, selection: $s.units) { $0.label }
                }

                SectionHeader(text: "On the move")
                SettingRow(icon: "iphone", title: "Live Activity",
                           subtitle: "Next transfer on your lock screen") {
                    TransfrToggle(isOn: $s.liveActivity)
                }.padding(.bottom, 8)
                SettingStack(icon: "viewfinder", title: "Open AR automatically",
                             subtitle: "Prompt me to point my phone before I arrive") {
                    SegmentedControl(options: [0, 60, 90], selection: $s.autoARLeadS) {
                        $0 == 0 ? "Off" : "\($0)s"
                    }
                }

                SectionHeader(text: "Power tools")
                NavRow(icon: "shield.lefthalf.filled", title: "Advanced",
                       subtitle: "Station walks · nearest facilities · offline data · map health",
                       route: .advanced)

                SectionHeader(text: "About")
                NavRow(icon: "info.circle", title: "Attributions & data sources",
                       subtitle: "Map data © OpenStreetMap contributors · licences",
                       route: .attributions)
            }
            .padding(20)
        }
        .scrollBounceBehavior(.basedOnSize)
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: s.theme) { _, _ in s.persist() }
        .onDisappear { s.persist() }
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
                Text("Call a connection makeable when the walk uses under this much of the layover. Above it, we flag it tight.")
                    .font(.system(size: 11.5)).foregroundStyle(Theme.ink3)
                    .padding(.top, 5).padding(.bottom, 12)

                Slider(value: Binding(
                    get: { Double(s.makeablePct) },
                    set: { s.makeablePct = (Int($0) / 5) * 5 }
                ), in: 40...90, step: 5)
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

                (Text("On an 8-min connection, that's up to ")
                 + Text(s.makeableExample).font(.system(size: 12, weight: .semibold, design: .monospaced)).foregroundColor(Theme.ink)
                 + Text(" of walking before it's flagged."))
                    .font(.system(size: 11.5)).foregroundStyle(Theme.ink3).padding(.top, 10)
            }
        }
    }
}
