import SwiftUI
import TransfrCore

/// The planning screen — the prototype's "Where are you headed?" (`#s-input`,
/// §6.1). Three ways in via a segmented control: **Type it** (from/to), **Paste
/// link** (a maps/DB link → itinerary), and **Walk only** (station + two platform
/// refs → the verdict-free walk, §6.9). Gear → Settings.
struct InputView: View {
    @Environment(TripModel.self) private var model

    enum Mode: String, CaseIterable, Identifiable { case type, paste, walk
        var id: String { rawValue }
        var label: String { switch self { case .type: "Type it"; case .paste: "Paste link"; case .walk: "Walk only" } }
        var icon: String { switch self { case .type: "text.alignleft"; case .paste: "link"; case .walk: "figure.walk" } }
    }
    @State private var mode: Mode = .type
    @State private var link = "https://maps.app.goo.gl/JWTvpehbneTcqad39"
    @State private var lookupStation = "Berlin Hbf"
    @State private var fromPlatform = "1"
    @State private var toPlatform = "16"

    /// Station-autocomplete state, shared across the From/To/Station fields — one
    /// focused field owns the suggestion list at a time.
    enum Field: Hashable { case from, to, station }
    @FocusState private var focused: Field?
    @State private var suggestions: [StationSuggestion] = []
    @State private var searchTask: Task<Void, Never>?

    /// Departure-time editor presented as a sheet (the "Depart" chip is the trigger).
    @State private var showDepartPicker = false

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                Text("Where are\nyou headed?")
                    .font(.system(size: 34, weight: .bold))
                    .foregroundStyle(Theme.ink)
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
            // Opt-in (TRANSFR_AUTOPLAN=1): jump straight to live results on launch.
            if AppConfig.autoplanOnLaunch, model.load == .idle {
                await model.plan()
                if let j = model.journeys.first { model.select(j); model.path.append(.walk(transferIndex: 0)) } // TEMP verify
            }
        }
    }

    private var header: some View {
        HStack {
            Text("PLAN A TRIP").font(.system(size: 11, weight: .semibold)).tracking(0.8)
                .foregroundStyle(Theme.ink3)
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
                    fieldRow(dot: Theme.accent, label: "From", field: .from, text: $model.origin) {
                        Button {
                            withAnimation(.snappy) { model.swapEndpoints() }
                        } label: {
                            Image(systemName: "arrow.up.arrow.down")
                                .font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink2)
                                .frame(width: 34, height: 34).background(Circle().fill(Theme.panel2))
                        }
                    }
                    Divider().overlay(Theme.line).padding(.leading, 44)
                    fieldRow(dot: Theme.miss, label: "To", field: .to, text: $model.destination) { EmptyView() }
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
            Label("Every field here is editable — tap to change.", systemImage: "pencil")
                .font(.system(size: 13)).foregroundStyle(Theme.ink3)
        }
    }

    private func fieldRow<Trailing: View>(
        dot: Color, label: String, field: Field, text: Binding<String>,
        @ViewBuilder trailing: () -> Trailing
    ) -> some View {
        HStack(spacing: 12) {
            Circle().fill(dot).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                TextField(label, text: text)
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

    private var pasteMode: some View {
        VStack(alignment: .leading, spacing: 14) {
            SetCard {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 10) {
                        Image(systemName: "link").foregroundStyle(Theme.accent)
                        TextField("Paste a route link", text: $link)
                            .font(.system(size: 13, design: .monospaced)).foregroundStyle(Theme.ink)
                            .autocorrectionDisabled().textInputAutocapitalization(.never)
                    }
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.go)
                        Text("We read the stops & departure time straight from a Google/Apple Maps or DB Navigator link — then rebuild it with platform transfers.")
                            .font(.system(size: 12)).foregroundStyle(Theme.ink3)
                    }
                }
            }
            SectionHeader(text: "Recent")
            recentRow(title: "Hamburg Hbf → Stuttgart Hbf", when: "yesterday")
            recentRow(title: "Berlin Hbf → Basel SBB", when: "Mon")
        }
    }

    private func recentRow(title: String, when: String) -> some View {
        HStack(spacing: 10) {
            SetIcon("clock")
            Text(title).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
            Spacer()
            Text(when).font(.system(size: 12)).foregroundStyle(Theme.ink3)
        }
        .padding(.horizontal, 13).padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 13).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(Theme.line, lineWidth: 1))
    }

    // MARK: - Walk-only mode

    private var walkMode: some View {
        VStack(alignment: .leading, spacing: 14) {
            Panel(padding: 6) {
                fieldRow(dot: Theme.accent, label: "Station", field: .station, text: $lookupStation) { EmptyView() }
            }
            if focused == .station { suggestionList }
            HStack(spacing: 8) {
                platformField("From platform", $fromPlatform)
                Image(systemName: "arrow.right").font(.system(size: 15, weight: .bold)).foregroundStyle(Theme.ink3)
                platformField("To platform", $toPlatform)
            }
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.go)
                Text("Any two platforms at one station — we draw the walk between them. No trip, no train, no verdict: just the route, timed at your pace. Platform names are free-form (5a, Gl 1).")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
        }
    }

    private func platformField(_ label: String, _ text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            TextField(label, text: text)
                .font(.system(size: 17, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.ink)
                .autocorrectionDisabled().textInputAutocapitalization(.never)
        }
        .padding(.horizontal, 12).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.line, lineWidth: 1))
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
    private func pick(_ s: StationSuggestion) {
        @Bindable var model = model
        switch focused {
        case .from:    model.origin = s.name
        case .to:      model.destination = s.name
        case .station: lookupStation = s.name
        case nil:      break
        }
        searchTask?.cancel()
        suggestions = []
        focused = nil
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

    private var cta: some View {
        VStack(spacing: 8) {
            if case .failed(let msg) = model.load {
                Label(msg, systemImage: "exclamationmark.circle")
                    .font(.system(size: 13)).foregroundStyle(Theme.miss)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Button {
                if mode == .walk {
                    model.path.append(.walkLookup)
                } else {
                    Task { await model.plan() }
                }
            } label: {
                HStack {
                    if model.load == .loading && mode != .walk {
                        ProgressView().tint(.white)
                    } else {
                        Text(ctaLabel)
                        Image(systemName: "arrow.right")
                    }
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(model.load == .loading && mode != .walk)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.thinMaterial)
    }
}
