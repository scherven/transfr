import SwiftUI
import CoreLocation
import TransfrCore

/// The planning screen — the prototype's "Where are you headed?" (`#s-input`,
/// §6.1). Three ways in via a segmented control: **Type it** (from/to), **Paste
/// link** (a maps/DB link → itinerary), and **Walk only** (station + two platform
/// refs → the verdict-free walk, §6.9). Gear → Settings.
struct InputView: View {
    @Environment(TripModel.self) private var model
    @Environment(LocationManager.self) private var location
    /// While the cold-launch mark is still flying, this view's own "transfr" title
    /// stays hidden so the flying mark is the only wordmark on screen; it fades in as
    /// the launch hands off (see `WordmarkAnchorKey` / LaunchView).
    @Environment(\.isLaunching) private var isLaunching
    @Environment(RecentSearchStore.self) private var recents

    enum Mode: String, CaseIterable, Identifiable { case type, paste, walk
        var id: String { rawValue }
        var label: String { switch self { case .type: "Plan"; case .paste: "Paste link"; case .walk: "Walk only" } }
        var icon: String { switch self { case .type: "text.alignleft"; case .paste: "link"; case .walk: "figure.walk" } }
    }
    @State private var mode: Mode = .type
    /// All genuinely empty. Every field below shows a real placeholder instead of
    /// shipping an example value: nothing on screen is a value the user didn't put
    /// there. The walk-only platforms fill in from the station's *real* platform
    /// list once it resolves (`resolvePlatforms`).
    @State private var link = ""
    @State private var lookupStation = ""
    @State private var fromPlatform = ""
    @State private var toPlatform = ""

    /// Walk-only resolution: the station's platforms + relation id, resolved from a
    /// picked suggestion's coordinate so the platform inputs **adapt to the entered
    /// station** (the medium-TODO ask). `resolvedForStation` records which station
    /// text the resolution belongs to, so editing the name reverts to free-form
    /// until it's re-resolved. `resolving` covers the "Show walk" inline resolve.
    @State private var resolved: StationPlatformsResponse?
    @State private var stationLatLon: (lat: Double, lon: Double)?
    @State private var resolvedForStation = ""
    @State private var resolvingWalk = false

    /// Station-autocomplete state, shared across the From/To/Station fields — one
    /// focused field owns the suggestion list at a time. `.link` joins in so the
    /// paste field is focusable and dismissible (it owns no suggestions).
    enum Field: Hashable { case from, to, station, link }
    @FocusState private var focused: Field?

    @State private var suggestions: [StationSuggestion] = []
    @State private var searchTask: Task<Void, Never>?

    /// Departure-time editor presented as a sheet (the "Depart" chip is the trigger).
    @State private var showDepartPicker = false

    /// Current-location wiring (design/route-maps.html §3). `awaitingLocation` means
    /// a fix has been asked for and should be applied when it lands; `manualLocation`
    /// distinguishes a button tap (always applies) from the first-launch default
    /// (which yields to an origin the user has already typed). The persisted flag
    /// fires the default exactly once.
    @State private var awaitingLocation = false
    @State private var manualLocation = false
    @AppStorage("didDefaultLocationFrom") private var didDefaultLocation = false

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                // The brand wordmark — the SAME mark the launch animation lands, so
                // the fly-up hand-off is pixel-identical. Its frame is the fly target,
                // so we keep publishing the anchor (opacity doesn't change layout) but
                // hold it invisible until the flying mark lands, then fade it in as the
                // overlay fades out — a seamless swap of two identical marks.
                Wordmark(height: 40)
                    .anchorPreference(key: WordmarkAnchorKey.self, value: .bounds) { $0 }
                    .opacity(isLaunching ? 0 : 1)
                    // Snap (don't fade) at the hand-off: the title appears at full opacity
                    // the instant the launch ends, so it's a solid floor under the identical
                    // launch mark as the overlay fades out on top. Fading it in would instead
                    // cross-dissolve two ~half-opaque copies of the mark — the composite only
                    // ~75% opaque — which reads as a brief lightening of the blue (the flash).
                    .animation(nil, value: isLaunching)
                    .padding(.top, 4)

                SegmentedControl(options: Mode.allCases, selection: $mode) { $0.label }

                switch mode {
                case .type:  typeMode
                case .paste: pasteMode
                case .walk:  walkMode
                }
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .safeAreaInset(edge: .bottom) { cta }
        .sheet(isPresented: $showDepartPicker) { departureSheet }
        .navigationBarBackButtonHidden(true)
        .task {
            if ProcessInfo.processInfo.environment["TRANSFR_OPEN_SETTINGS"] == "1" { model.path = [.settings] } // TEMP verify
            // First launch: default "From" to the user's location (design §3). Asks
            // permission once; the fix applies in onChange the moment it lands.
            if !didDefaultLocation && !AppConfig.autoplanOnLaunch {
                didDefaultLocation = true
                if !location.isDenied { requestLocation(manual: false) }
            }
            // Opt-in dev affordance: jump straight to live results on launch. The
            // route comes from TRANSFR_AUTOPLAN_FROM/_TO — the fields themselves
            // ship empty, so there's no default query to fire.
            if let q = AppConfig.autoplanQuery, model.load == .idle {
                model.origin = q.from
                model.destination = q.to
                await model.plan()
            }
        }
        .onChange(of: location.coordinate?.latitude) { _, _ in applyLocationIfReady() }
        .onChange(of: model.origin) { _, new in
            // Any origin that isn't the resolved station means the user took over.
            if new != model.locationName {
                model.originUserEdited = true
                model.usingCurrentLocation = false
            }
        }
        .onChange(of: focused) { _, now in
            // Focusing "From" is the user taking it over, so a still-pending
            // first-launch location fix must not land under the cursor. This used
            // to fall out of clearing the field's shipped example on first focus;
            // with the field genuinely empty there's nothing to clear, so the
            // intent is stated directly. The location *button* is unaffected: it
            // blurs the field and forces the fix through (`manual`).
            if now == .from { model.originUserEdited = true }
        }
    }

    private var header: some View {
        HStack {
            Spacer()
            NavigationLink(value: Route.settings) {
                Image(systemName: "gearshape").foregroundStyle(Theme.ink2)
                    .frame(width: 32, height: 32).background(Circle().fill(Theme.panel2))
            }
        }
    }

    // MARK: - Type mode

    private var typeMode: some View {
        @Bindable var model = model
        return VStack(alignment: .leading, spacing: 14) {
            Panel(padding: 6) {
                VStack(spacing: 0) {
                    fromRow(origin: $model.origin)
                    Divider().overlay(Theme.line).padding(.leading, 44)
                    fieldRow(dot: Theme.miss, label: "To", placeholder: "Stuttgart Hbf",
                             field: .to, text: $model.destination) { EmptyView() }
                }
            }
            if focused == .from || focused == .to { suggestionList }
            HStack(spacing: 10) {
                Button { focused = nil; showDepartPicker = true } label: {
                    chip(key: "Depart", value: departLabel, tappable: true)
                }.buttonStyle(.plain)
                chip(key: "Travellers", value: "1 adult")
                Spacer(minLength: 0)
            }
        }
    }

    // MARK: - From row (current location + button)

    /// The "From" row. When the trip is location-sourced and the field isn't being
    /// edited, it reads "Current location" over the resolved station; tapping the
    /// text hands control back to typing. The trailing location button is always
    /// present — the "also put in the button" half of the ask.
    @ViewBuilder private func fromRow(origin: Binding<String>) -> some View {
        if model.usingCurrentLocation && focused != .from {
            HStack(spacing: 12) {
                locationDot
                VStack(alignment: .leading, spacing: 1) {
                    Text("From").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                    Text("Current location").font(.system(size: 17, weight: .semibold)).foregroundStyle(Theme.ink)
                    if let n = model.locationName {
                        Text(n).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture {
                    model.usingCurrentLocation = false
                    model.originUserEdited = true
                    focused = .from
                }
                Spacer(minLength: 0)
                fromTrailing(active: true)
            }
            .padding(.horizontal, 12).padding(.vertical, 12)
        } else {
            fieldRow(dot: Theme.accent, label: "From", placeholder: "Hamburg Hbf",
                     field: .from, text: origin) {
                fromTrailing(active: false)
            }
        }
    }

    private func fromTrailing(active: Bool) -> some View {
        HStack(spacing: 8) {
            locationButton(active: active)
            Button {
                withAnimation(.snappy) { model.swapEndpoints() }
            } label: {
                Image(systemName: "arrow.up.arrow.down")
                    .font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink2)
                    .frame(width: 34, height: 34).background(Circle().fill(Theme.panel2))
            }
        }
    }

    private func locationButton(active: Bool) -> some View {
        Button { requestLocation(manual: true) } label: {
            Group {
                if awaitingLocation && location.isRequesting {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: active ? "location.fill" : "location")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(active ? .white : Theme.accent)
                }
            }
            .frame(width: 34, height: 34)
            .background(Circle().fill(active ? Theme.accent : Theme.accentSoft))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Use my current location")
    }

    private var locationDot: some View {
        Circle().fill(Theme.accent).frame(width: 10, height: 10)
            .overlay(Circle().stroke(Theme.accent.opacity(0.25), lineWidth: 4))
    }

    /// Ask for a fix. `manual` (button) always applies the result; the first-launch
    /// default yields if the user has since typed an origin.
    private func requestLocation(manual: Bool) {
        focused = nil
        manualLocation = manual
        awaitingLocation = true
        if manual { model.originUserEdited = false }
        location.request()
        applyLocationIfReady()   // apply straight away if a fix is already cached
    }

    private func applyLocationIfReady() {
        guard awaitingLocation, let coord = location.coordinate else { return }
        if !manualLocation && model.originUserEdited { awaitingLocation = false; return }
        awaitingLocation = false
        Task { await model.useCurrentLocation(lat: coord.latitude, lon: coord.longitude) }
    }

    /// `label` is the caption above the field; `placeholder` is the hint shown while
    /// it's empty — an example of a valid entry, which is what the old shipped-in
    /// example values were reaching for, done honestly.
    private func fieldRow<Trailing: View>(
        dot: Color, label: String, placeholder: String, field: Field, text: Binding<String>,
        @ViewBuilder trailing: () -> Trailing
    ) -> some View {
        HStack(spacing: 12) {
            Circle().fill(dot).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                TextField(placeholder, text: text)
                    .font(.system(size: 17, weight: .semibold)).foregroundStyle(Theme.ink)
                    .textInputAutocapitalization(.words).autocorrectionDisabled()
                    .focused($focused, equals: field)
                    .submitLabel(.search)
                    .onChange(of: text.wrappedValue) { _, new in
                        if focused == field { scheduleSearch(new) }
                    }
                    .onChange(of: focused) { _, now in
                        if now == field { scheduleSearch(text.wrappedValue) }
                    }
            }
            Spacer(minLength: 0)
            trailing()
        }
        .padding(.horizontal, 12).padding(.vertical, 12)
    }

    // MARK: - Paste mode

    /// The paste door. The button is the primary way in: `PasteButton` is
    /// system-mediated, so the link arrives without the app reading the clipboard
    /// itself (no "pasted from" banner, and nothing is read until you tap). The
    /// field stays so the pasted link is visible and correctable — a link that
    /// doesn't parse only produces a message on the CTA, which is unusable if you
    /// can't see what landed — and it starts empty behind a real placeholder.
    private var pasteMode: some View {
        VStack(alignment: .leading, spacing: 14) {
            SetCard {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 10) {
                        Image(systemName: "link").foregroundStyle(Theme.accent)
                        TextField("Paste a route link", text: $link)
                            .font(.system(size: 13, design: .monospaced)).foregroundStyle(Theme.ink)
                            .autocorrectionDisabled().textInputAutocapitalization(.never)
                            .focused($focused, equals: .link)
                        PasteButton(payloadType: String.self) { strings in
                            guard let s = strings.first?.trimmingCharacters(in: .whitespacesAndNewlines),
                                  !s.isEmpty else { return }
                            link = s
                            focused = nil
                        }
                        .labelStyle(.iconOnly)
                        .buttonBorderShape(.capsule)
                        .tint(Theme.accent)
                    }
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.go)
                        Text("Reads the stops from a Maps or DB link, then rebuilds it with platform transfers.")
                            .font(.system(size: 12)).foregroundStyle(Theme.ink3)
                    }
                }
            }
            recentSection
        }
    }

    /// Past searches offered back for one-tap reuse (#38): the persisted history
    /// from `RecentSearchStore`, newest first. Every row is a search the user
    /// actually ran — until there is one, the section says so plainly rather than
    /// showing seeded examples. (Where "Recent" belongs is its own polish, #43.)
    @ViewBuilder private var recentSection: some View {
        SectionHeader(text: "Recent")
        if recents.items.isEmpty {
            Text("No recent searches yet.")
                .font(.system(size: 13)).foregroundStyle(Theme.ink3)
                .padding(.vertical, 20)
        } else {
            ForEach(recents.items) { search in
                recentRow(origin: search.origin, destination: search.destination,
                          when: Fmt.relativeDay(search.date))
            }
        }
    }

    /// One past search. Tapping it re-runs that route through the same live path as
    /// typed input.
    private func recentRow(origin: String, destination: String, when: String) -> some View {
        Button {
            guard !origin.isEmpty, !destination.isEmpty else { return }
            model.origin = origin
            model.destination = destination
            model.usingCurrentLocation = false
            model.originUserEdited = true
            Task { await model.plan() }
        } label: {
            HStack(spacing: 10) {
                SetIcon("clock")
                Text("\(origin) → \(destination)")
                    .font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                Spacer()
                Text(when).font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
            .padding(.horizontal, 13).padding(.vertical, 12)
            .frame(maxWidth: .infinity)
            .background(RoundedRectangle(cornerRadius: 13).fill(Theme.panel))
            .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(Theme.line, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    // MARK: - Walk-only mode

    private var walkMode: some View {
        VStack(alignment: .leading, spacing: 14) {
            Panel(padding: 6) {
                fieldRow(dot: Theme.accent, label: "Station", placeholder: "Berlin Hbf",
                         field: .station, text: $lookupStation) { EmptyView() }
            }
            if focused == .station { suggestionList }

            // The platform inputs adapt to the entered station: real dropdowns of
            // its actual platforms once resolved, free-form text until then.
            HStack(spacing: 8) {
                platformInput("From platform", placeholder: "1", $fromPlatform)
                Image(systemName: "arrow.right").font(.system(size: 15, weight: .bold)).foregroundStyle(Theme.ink3)
                platformInput("To platform", placeholder: "16", $toPlatform)
            }

            walkHint
        }
    }

    /// The platforms currently offered as a dropdown — the resolved station's real
    /// list, but only while it still matches the station text (editing the name
    /// reverts to free-form until re-resolved).
    private var adaptedPlatforms: [String] {
        guard resolvedForStation == lookupStation.trimmingCharacters(in: .whitespaces),
              let refs = resolved?.platforms, !refs.isEmpty else { return [] }
        return refs
    }

    /// A menu of the station's real platforms when we have them; the free-form
    /// field otherwise — so an unmapped station or a hand-typed ref still works.
    @ViewBuilder
    private func platformInput(_ label: String, placeholder: String, _ text: Binding<String>) -> some View {
        if adaptedPlatforms.isEmpty {
            platformField(label, placeholder, text)
        } else {
            platformMenu(label, text, adaptedPlatforms)
        }
    }

    private func platformField(_ label: String, _ placeholder: String, _ text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            TextField(placeholder, text: text)
                .font(.system(size: 17, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
                .autocorrectionDisabled().textInputAutocapitalization(.never)
        }
        .padding(.horizontal, 12).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line, lineWidth: 1))
    }

    private func platformMenu(_ label: String, _ text: Binding<String>, _ options: [String]) -> some View {
        Menu {
            Picker(label, selection: text) {
                ForEach(options, id: \.self) { ref in Text("Platform \(ref)").tag(ref) }
            }
        } label: {
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                HStack(spacing: 6) {
                    Text(text.wrappedValue.isEmpty ? "—" : text.wrappedValue)
                        .font(.system(size: 17, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.ink3)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line, lineWidth: 1))
        }
    }

    /// Status line under the platform inputs: resolving / resolved (n platforms) /
    /// the free-form fallback copy.
    @ViewBuilder
    private var walkHint: some View {
        if resolvingWalk {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Finding this station's platforms…")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
        } else if !adaptedPlatforms.isEmpty {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.go)
                Text("\(adaptedPlatforms.count) platforms at \(lookupStation). Pick any two — we draw the walk between them, timed at your pace.")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
        } else {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.go)
                Text("Any two platforms at one station — we draw the walk between them, timed at your pace. Pick a station above to choose from its real platforms; names are free-form (5a, Gl 1).")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
        }
    }

    // MARK: - Autocomplete

    /// Debounced station lookup for the focused field. Under two characters we show
    /// nothing; a cancelled task supersedes the last keystroke so results never race.
    private func scheduleSearch(_ query: String) {
        searchTask?.cancel()
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard q.count >= 2 else { suggestions = []; return }
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(180))
            if Task.isCancelled { return }
            let results = await model.stations(matching: q)
            if Task.isCancelled { return }
            suggestions = results
        }
    }

    /// Commit a suggestion to whichever field is focused, then dismiss the list.
    /// Picking the walk-only station also resolves its platforms so the pickers
    /// adapt to it.
    private func pick(_ s: StationSuggestion) {
        @Bindable var model = model
        switch focused {
        case .from:    model.origin = s.name
        case .to:      model.destination = s.name
        case .station:
            lookupStation = s.name
            if let lat = s.latitude, let lon = s.longitude {
                Task { await resolvePlatforms(name: s.name, lat: lat, lon: lon) }
            }
        case .link, nil: break   // the paste field owns no suggestions
        }
        searchTask?.cancel()
        suggestions = []
        focused = nil
    }

    // MARK: - Walk-only resolution

    /// Resolve a station's coordinate to its platforms (+ relation id) and adapt
    /// the pickers. Defaults the two selected platforms to the first/last of the
    /// real list when the current values aren't among them. Returns the response
    /// so the "Show walk" path can reuse it.
    @discardableResult
    private func resolvePlatforms(name: String, lat: Double, lon: Double) async -> StationPlatformsResponse? {
        resolvingWalk = true
        defer { resolvingWalk = false }
        stationLatLon = (lat, lon)
        let r = await model.stationPlatforms(lat: lat, lon: lon)
        guard let r, r.found, !r.platforms.isEmpty else { return r }
        resolved = r
        resolvedForStation = name.trimmingCharacters(in: .whitespaces)
        if !r.platforms.contains(fromPlatform) { fromPlatform = r.platforms.first ?? fromPlatform }
        if !r.platforms.contains(toPlatform) { toPlatform = r.platforms.last ?? toPlatform }
        return r
    }

    /// The "Show walk" action: make sure the station is resolved (resolving inline
    /// if the user typed a name without picking a suggestion), then hand the
    /// resolved lookup to `WalkLookupView`. relationId 0 (sample tier / unresolved)
    /// still navigates — the lookup falls back to its schematic there.
    private func showWalk() async {
        resolvingWalk = true
        defer { resolvingWalk = false }

        let station = lookupStation.trimmingCharacters(in: .whitespaces)
        var lookup = resolved
        if resolvedForStation != station || lookup == nil {
            var coord = stationLatLon
            if coord == nil {
                let hits = await model.stations(matching: station)
                if let top = hits.first, let la = top.latitude, let lo = top.longitude { coord = (la, lo) }
            }
            if let (la, lo) = coord {
                lookup = await resolvePlatforms(name: station, lat: la, lon: lo)
            }
        }

        model.walkLookup = TripModel.WalkLookup(
            station: station.isEmpty ? (lookup?.station ?? "Walk") : station,
            relationId: lookup?.relationId ?? 0,
            fromPlatform: fromPlatform,
            toPlatform: toPlatform)
        model.path.append(.walkLookup)
    }

    @ViewBuilder private var suggestionList: some View {
        if !suggestions.isEmpty {
            Panel(padding: 0) {
                VStack(spacing: 0) {
                    ForEach(Array(suggestions.prefix(6).enumerated()), id: \.offset) { i, s in
                        if i > 0 { Divider().overlay(Theme.line).padding(.leading, 44) }
                        Button { pick(s) } label: { suggestionRow(s) }
                            .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private func suggestionRow(_ s: StationSuggestion) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "mappin.circle.fill")
                .font(.system(size: 18)).foregroundStyle(Theme.ink3)
                .frame(width: 20)
            Text(s.name).font(.system(size: 15, weight: .medium)).foregroundStyle(Theme.ink)
            Spacer(minLength: 8)
            if let c = s.country, !c.isEmpty {
                Text(c).font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Theme.ink3)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 12)
        .contentShape(Rectangle())
    }

    // MARK: - Shared

    private func chip(key: String, value: String, tappable: Bool = false) -> some View {
        HStack(spacing: 6) {
            Text(key).font(.system(size: 12)).foregroundStyle(Theme.ink3)
            Text(value).font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
            if tappable {
                Image(systemName: "chevron.down")
                    .font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.ink3)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(Capsule().fill(Theme.panel))
        .overlay(Capsule().strokeBorder(Theme.line, lineWidth: 1))
    }

    /// "Today · 08:34", "Tomorrow · 09:10", or "Wed 16 · 09:10" once the picker
    /// moves off today, so the chip always reads back the real departure.
    private var departLabel: String {
        let cal = Calendar.current
        let t = DateFormatter(); t.locale = Locale(identifier: "en_GB"); t.dateFormat = "HH:mm"
        let time = t.string(from: model.departure)
        if cal.isDateInToday(model.departure) { return "Today · \(time)" }
        if cal.isDateInTomorrow(model.departure) { return "Tomorrow · \(time)" }
        let d = DateFormatter(); d.locale = Locale(identifier: "en_GB"); d.dateFormat = "EEE d"
        return "\(d.string(from: model.departure)) · \(time)"
    }

    /// Date + time editor. Departure is unrestricted (past allowed) so the
    /// prototype's default 08:34-today anchor stays valid even after that time.
    private var departureSheet: some View {
        @Bindable var model = model
        return NavigationStack {
            VStack(spacing: 16) {
                DatePicker("Departure", selection: $model.departure)
                    .datePickerStyle(.graphical)
                    .tint(Theme.accent)
                Button { model.departure = Date() } label: {
                    Label("Leave now", systemImage: "clock.arrow.circlepath")
                        .font(.system(size: 14, weight: .semibold))
                }
                .buttonStyle(.plain).foregroundStyle(Theme.accent)
                Spacer(minLength: 0)
            }
            .padding(20)
            .background(Theme.paper.ignoresSafeArea())
            .navigationTitle("Departure")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { showDepartPicker = false }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private var ctaLabel: String { mode == .walk ? "Show walk" : "Find connections" }

    /// The CTA shows a spinner while planning (type/paste) or resolving the walk.
    private var ctaBusy: Bool { mode == .walk ? resolvingWalk : model.load == .loading }

    /// Whether the current mode has a query to submit. The fields ship empty now,
    /// so each mode gates on its own inputs — previously the shipped example values
    /// meant type mode could never be empty, and it went unguarded. A location
    /// origin fills `model.origin` with the resolved station, so it counts here too.
    private var canSubmit: Bool {
        func filled(_ s: String) -> Bool { !s.trimmingCharacters(in: .whitespaces).isEmpty }
        switch mode {
        case .type:  return filled(model.origin) && filled(model.destination)
        case .paste: return filled(link)
        case .walk:  return filled(lookupStation) && filled(fromPlatform) && filled(toPlatform)
        }
    }

    private var cta: some View {
        VStack(spacing: 8) {
            if case .failed(let msg) = model.load {
                Label(msg, systemImage: "exclamationmark.circle")
                    .font(.system(size: 13)).foregroundStyle(Theme.miss)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Button {
                switch mode {
                case .walk:  Task { await showWalk() }
                case .paste: Task { await model.planFromLink(link) }
                case .type:  Task { await model.plan() }
                }
            } label: {
                HStack {
                    if ctaBusy {
                        ProgressView().tint(.white)
                    } else {
                        Text(ctaLabel)
                        Image(systemName: "arrow.right")
                    }
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(ctaBusy || !canSubmit)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.thinMaterial)
    }
}
