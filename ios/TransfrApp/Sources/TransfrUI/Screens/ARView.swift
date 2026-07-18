import SwiftUI
import TransfrCore

/// The AR view — the prototype's `#s-ar` (§6.6). A **mocked camera**: a receding
/// floor grid, the glowing azure path with chevrons toward a vanishing point, an
/// instruction banner, a destination pill, and a distance badge. The real thing is
/// ARKit + RealityKit anchored from the georeferenced `viz_export` (§7.7, §13.5) —
/// flagged as the hard v2 frontier in the repo-root `TODO.md` (§5).
struct ARView: View {
    @Environment(TripModel.self) private var model
    @Environment(SettingsStore.self) private var settings
    @Environment(\.dismiss) private var dismiss
    let transferIndex: Int

    @State private var boarding: BoardingGuidance?

    private var transfer: Transfer? { model.transfers[safe: transferIndex] }

    /// The train boarded after this transfer — the (i+1)-th named leg. Real, not a
    /// fixed "ICE 1197"; nil when the journey doesn't name it.
    private var boardingTrain: String? {
        let transit = (model.selected?.legs ?? []).filter { $0.trainName != nil }
        return transit[safe: transferIndex + 1]?.trainName
    }

    // Distance badge, in the user's units (the number and unit render separately).
    private var imperial: Bool { settings.units == .imperial }
    private var distanceMeters: Double? { transfer?.walkDistanceM }
    private var distanceText: String {
        guard let m = distanceMeters else { return "—" }
        return "\(Int((imperial ? m * 3.28084 : m).rounded()))"
    }
    private var distanceUnit: String { imperial ? "ft" : "m" }

    var body: some View {
        ZStack {
            // Camera stand-in: sky → floor gradient
            LinearGradient(colors: [Color(hue: 0.6, saturation: 0.35, brightness: 0.16),
                                    Color(hue: 0.62, saturation: 0.2, brightness: 0.06)],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()

            ARFloor().ignoresSafeArea()

            VStack {
                // Instruction banner — the real step-off direction from /walk.
                HStack(spacing: 12) {
                    Image(systemName: "arrow.up").font(.system(size: 20, weight: .bold)).foregroundStyle(.white)
                        .frame(width: 44, height: 44).background(Circle().fill(.white.opacity(0.15)))
                    VStack(alignment: .leading, spacing: 1) {
                        Text(bannerTitle).font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
                        Text(bannerSub).font(.system(size: 12)).foregroundStyle(.white.opacity(0.7))
                    }
                    Spacer()
                }
                .padding(12)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
                .environment(\.colorScheme, .dark)
                .padding(.horizontal, 16).padding(.top, 8)

                Spacer()

                // Destination pill + distance
                VStack(spacing: 10) {
                    Text(destinationPill)
                        .font(.system(size: 13, weight: .semibold, design: .monospaced))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .background(Capsule().fill(Color(hex: 0x4EA6FF).opacity(0.85)))

                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(distanceText).font(.system(size: 30, weight: .bold, design: .monospaced))
                        Text("\(distanceUnit) to go · \(Fmt.walkTime(transfer?.pacedWalkTimeS(settings.pace.factor)))").font(.system(size: 13))
                    }
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16).padding(.vertical, 8)
                    .background(Capsule().fill(.black.opacity(0.35)))
                }

                // Bottom controls
                HStack(spacing: 22) {
                    arControl("dot.scope", "Recenter")
                    Button { dismiss() } label: { arControl("cube.transparent", "3D map", filled: true) }
                        .buttonStyle(.plain)
                    arControl("speaker.wave.2", "Sound")
                }
                .padding(.top, 18).padding(.bottom, 8)
            }
        }
        .navigationTitle("AR").navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task(id: transferIndex) { await loadBoarding() }
    }

    private var bannerTitle: String {
        if let b = boarding, b.hasPosition, b.band != .low {
            return "Head toward \(BoardingCopy.end(b))"
        }
        return "Follow the path"
    }
    private var bannerSub: String {
        "your line to Platform \(transfer?.departurePlatform ?? "?")"
    }
    private var destinationPill: String {
        let dep = transfer?.departurePlatform ?? "?"
        if let train = boardingTrain { return "▼ Platform \(dep) · \(train)" }
        return "▼ Platform \(dep)"
    }

    private func loadBoarding() async {
        guard let t = transfer, let key = WalkKey(transfer: t, stepFree: settings.avoidElevators) else { return }
        boarding = await model.walk(for: key)?.boarding
    }

    private func arControl(_ icon: String, _ label: String, filled: Bool = false) -> some View {
        VStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 18, weight: .medium)).foregroundStyle(.white)
                .frame(width: 52, height: 52)
                .background(Circle().fill(filled ? Color(hex: 0x4EA6FF).opacity(0.9) : .white.opacity(0.14)))
            Text(label).font(.system(size: 10)).foregroundStyle(.white.opacity(0.8))
        }
    }
}

/// The receding floor grid + glowing path with chevrons.
private struct ARFloor: View {
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            let vp = CGPoint(x: w * 0.5, y: h * 0.42)   // vanishing point
            Canvas { ctx, _ in
                // Receding verticals
                for i in stride(from: -3.0, through: 3.0, by: 1.0) {
                    let x = w * 0.5 + i * w * 0.5
                    var p = Path(); p.move(to: CGPoint(x: x, y: h)); p.addLine(to: vp)
                    ctx.stroke(p, with: .color(.white.opacity(0.14)), lineWidth: 1)
                }
                // Receding horizontals
                for frac in [0.0, 0.32, 0.55, 0.72, 0.85] {
                    let y = h + (vp.y - h) * frac
                    let spread = (1 - frac)
                    var p = Path()
                    p.move(to: CGPoint(x: vp.x - w * spread, y: y))
                    p.addLine(to: CGPoint(x: vp.x + w * spread, y: y))
                    ctx.stroke(p, with: .color(.white.opacity(0.12)), lineWidth: 1)
                }

                // Glowing path
                var path = Path()
                path.move(to: CGPoint(x: w * 0.5, y: h * 0.99))
                path.addLine(to: CGPoint(x: w * 0.5, y: h * 0.86))
                path.addQuadCurve(to: CGPoint(x: w * 0.44, y: h * 0.62),
                                  control: CGPoint(x: w * 0.5, y: h * 0.74))
                path.addQuadCurve(to: CGPoint(x: w * 0.5, y: h * 0.5),
                                  control: CGPoint(x: w * 0.42, y: h * 0.54))
                let blue = Color(hex: 0x4EA6FF)
                ctx.stroke(path, with: .color(blue.opacity(0.28)), style: StrokeStyle(lineWidth: 26, lineCap: .round, lineJoin: .round))
                ctx.stroke(path, with: .linearGradient(
                    Gradient(colors: [blue.opacity(0.95), blue.opacity(0.1)]),
                    startPoint: CGPoint(x: 0, y: h), endPoint: CGPoint(x: 0, y: h * 0.5)),
                    style: StrokeStyle(lineWidth: 15, lineCap: .round, lineJoin: .round))

                // Chevrons
                for cy in [0.9, 0.8, 0.7] {
                    let y = h * cy
                    let x = w * (0.5 - (0.9 - cy) * 0.2)
                    var chev = Path()
                    chev.move(to: CGPoint(x: x - 7, y: y))
                    chev.addLine(to: CGPoint(x: x, y: y - 9))
                    chev.addLine(to: CGPoint(x: x + 7, y: y))
                    ctx.stroke(chev, with: .color(.white.opacity(0.92)), style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round))
                }
            }
        }
    }
}
