import SwiftUI
import TransfrCore

/// The planning screen — the prototype's "Where are you headed?" (`#s-input`).
/// Only the "Type it" mode is wired for this first cut; paste-link and walk-only
/// are flagged as follow-ups in the app README.
struct InputView: View {
    @Environment(TripModel.self) private var model

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header

                Text("Where are\nyou headed?")
                    .font(.system(size: 34, weight: .bold))
                    .foregroundStyle(Theme.ink)
                    .padding(.top, 4)

                endpointFields
                whenRow

                Label("Every field here is editable — tap to change.",
                      systemImage: "pencil")
                    .font(.system(size: 13))
                    .foregroundStyle(Theme.ink3)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .safeAreaInset(edge: .bottom) { cta }
        .navigationBarBackButtonHidden(true)
    }

    private var header: some View {
        HStack {
            Text("Plan a trip")
                .font(.system(size: 11, weight: .semibold)).tracking(0.8)
                .foregroundStyle(Theme.ink3)
            Spacer()
            NavigationLink(value: Route.settings) {
                Image(systemName: "gearshape")
                    .foregroundStyle(Theme.ink2)
                    .frame(width: 32, height: 32)
                    .background(Circle().fill(Theme.panel2))
            }
        }
    }

    private var endpointFields: some View {
        @Bindable var model = model
        return Panel(padding: 6) {
            VStack(spacing: 0) {
                fieldRow(dot: Theme.accent, label: "From", text: $model.origin) {
                    Button {
                        withAnimation(.snappy) { model.swapEndpoints() }
                    } label: {
                        Image(systemName: "arrow.up.arrow.down")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Theme.ink2)
                            .frame(width: 34, height: 34)
                            .background(Circle().fill(Theme.panel2))
                    }
                }
                Divider().overlay(Theme.line).padding(.leading, 44)
                fieldRow(dot: Theme.miss, label: "To", text: $model.destination) { EmptyView() }
            }
        }
    }

    private func fieldRow<Trailing: View>(
        dot: Color, label: String, text: Binding<String>,
        @ViewBuilder trailing: () -> Trailing
    ) -> some View {
        HStack(spacing: 12) {
            Circle().fill(dot).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.system(size: 11)).foregroundStyle(Theme.ink3)
                TextField(label, text: text)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Theme.ink)
                    .textInputAutocapitalization(.words)
                    .autocorrectionDisabled()
            }
            Spacer(minLength: 0)
            trailing()
        }
        .padding(.horizontal, 12).padding(.vertical, 12)
    }

    private var whenRow: some View {
        HStack(spacing: 10) {
            chip(key: "Depart", value: departLabel)
            chip(key: "Travellers", value: "1 adult")
            Spacer(minLength: 0)
        }
    }

    private func chip(key: String, value: String) -> some View {
        HStack(spacing: 6) {
            Text(key).font(.system(size: 12)).foregroundStyle(Theme.ink3)
            Text(value).font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(Theme.ink)
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(Capsule().fill(Theme.panel))
        .overlay(Capsule().strokeBorder(Theme.line, lineWidth: 1))
    }

    private var departLabel: String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_GB"); f.dateFormat = "HH:mm"
        return "Today · \(f.string(from: model.departure))"
    }

    private var cta: some View {
        VStack(spacing: 8) {
            if case .failed(let msg) = model.load {
                Label(msg, systemImage: "exclamationmark.circle")
                    .font(.system(size: 13)).foregroundStyle(Theme.miss)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Button {
                Task { await model.plan() }
            } label: {
                HStack {
                    if model.load == .loading {
                        ProgressView().tint(.white)
                    } else {
                        Text("Find connections")
                        Image(systemName: "arrow.right")
                    }
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(model.load == .loading)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.thinMaterial)
    }
}
