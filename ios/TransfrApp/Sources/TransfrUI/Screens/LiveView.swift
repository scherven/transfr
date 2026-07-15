import SwiftUI
import TransfrCore

/// On-trip mode — the prototype's `#s-live` (§6.7). A simplified route map with a
/// pulsing "you", the next-transfer flagged, and a next-transfer card (countdown,
/// verdict, step-off cue, platform move, Preview). **All values here are
/// illustrative** — real position/countdown/delays need CoreLocation + the live
/// feed (see `ios/SUI_TODO.md`).
struct LiveView: View {
    @Environment(TripModel.self) private var model

    // The next tight transfer to feature, if the selected journey has one.
    private var nextTransfer: Transfer? {
        model.transfers.first { $0.verdictKind == .tight } ?? model.transfers.first
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                RouteMap()
                    .frame(height: 220)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(Theme.line, lineWidth: 1))

                nextCard

                Label("We nudge you to open AR ~90 s before arrival, so the path is ready the moment the doors open.",
                      systemImage: "location.north.circle")
                    .font(.system(size: 12)).foregroundStyle(Theme.ink3)
            }
            .padding(20)
        }
        .background(Theme.paper.ignoresSafeArea())
        .navigationTitle("On your way")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text("On your way").font(.system(size: 16, weight: .semibold))
                    Text("ICE 271 · to Mannheim Hbf").font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.ink3)
                }
            }
            ToolbarItem(placement: .topBarTrailing) { StatusBadge(text: "Live", color: Theme.go, showDot: true) }
        }
    }

    private var nextCard: some View {
        let t = nextTransfer
        let v = t?.verdictKind ?? .tight
        return SetCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("NEXT TRANSFER").font(.system(size: 10.5, weight: .semibold)).tracking(1.2).foregroundStyle(Theme.ink3)
                    Spacer()
                    VerdictBadge(verdict: v)
                }
                VStack(alignment: .leading, spacing: 6) {
                    (Text("9:12").font(.system(size: 34, weight: .bold, design: .monospaced))
                     + Text("  to \(t?.atStation ?? "Mannheim")").font(.system(size: 13)).foregroundColor(Theme.ink3))
                        .foregroundStyle(Theme.ink)
                    Text("You have ~2 min spare on the platform change")
                        .font(.system(size: 13)).foregroundStyle(Theme.ink2)
                }
                // Step-off cue
                HStack(spacing: 9) {
                    Image(systemName: "arrow.left").font(.system(size: 15, weight: .bold)).foregroundStyle(Theme.accent)
                    (Text("The moment you step off: walk toward ")
                     + Text("sector C").font(.system(size: 13, weight: .bold)).foregroundColor(Theme.accent)
                     + Text(" — the stairs to Pl 5 are there."))
                        .font(.system(size: 13)).foregroundStyle(Theme.ink2)
                }
                .padding(11)
                .background(RoundedRectangle(cornerRadius: 11).fill(Theme.accentSoft))

                // Progress
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Theme.panel3).frame(height: 6)
                        Capsule().fill(Theme.accent).frame(width: geo.size.width * 0.62, height: 6)
                    }
                }.frame(height: 6)

                // Platform move + Preview
                HStack(spacing: 12) {
                    moveEnd("Arrive", t?.arrivalPlatform ?? "4")
                    Image(systemName: "arrow.right").font(.system(size: 15, weight: .bold)).foregroundStyle(Theme.ink3)
                    moveEnd("Depart", t?.departurePlatform ?? "5")
                    Spacer()
                    VStack(alignment: .trailing, spacing: 1) {
                        Text("Walk").font(.system(size: 11)).foregroundStyle(Theme.ink3)
                        Text(Fmt.walkTime(t?.walkTimeS ?? 78)).font(.system(size: 16, weight: .bold, design: .monospaced)).foregroundStyle(Theme.ink)
                    }
                    Button { model.path.append(.carousel(startIndex: 0)) } label: {
                        Label("Preview", systemImage: "cube.transparent").font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white).padding(.horizontal, 12).padding(.vertical, 9)
                            .background(RoundedRectangle(cornerRadius: 10).fill(Theme.accent))
                    }.buttonStyle(.plain)
                }
            }
        }
    }

    private func moveEnd(_ k: String, _ n: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(k).font(.system(size: 11)).foregroundStyle(Theme.ink3)
            Text(n).font(.system(size: 22, weight: .bold, design: .monospaced)).foregroundStyle(Theme.ink)
        }
    }
}

/// A stylised route map with a pulsing "you" dot. Schematic — not MapKit (§13.7).
private struct RouteMap: View {
    var body: some View {
        TimelineView(.animation) { timeline in
            Canvas { ctx, size in
                let w = size.width, h = size.height
                ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .color(Theme.panel2))

                // faint rivers/roads
                var river = Path()
                river.move(to: CGPoint(x: -10, y: h * 0.27))
                river.addQuadCurve(to: CGPoint(x: w * 0.44, y: h * 0.32), control: CGPoint(x: w * 0.26, y: h * 0.41))
                river.addQuadCurve(to: CGPoint(x: w + 10, y: h * 0.55), control: CGPoint(x: w * 0.7, y: h * 0.2))
                ctx.stroke(river, with: .color(Theme.line), lineWidth: 6)

                // route points (normalised)
                let pts = [CGPoint(x: 0.18, y: 0.15), CGPoint(x: 0.35, y: 0.35),
                           CGPoint(x: 0.44, y: 0.55), CGPoint(x: 0.74, y: 0.85)]
                    .map { CGPoint(x: $0.x * w, y: $0.y * h) }
                var route = Path(); route.addLines(pts)
                ctx.stroke(route, with: .color(Theme.line), style: StrokeStyle(lineWidth: 7, lineCap: .round))

                // done portion (to the you-dot at ~45%)
                let t = 0.45
                if let (idx, frac) = segmentAt(t, pts) {
                    var done = Path(); done.addLines(Array(pts[0...idx]))
                    let next = pts[min(idx + 1, pts.count - 1)]
                    let cur = interp(pts[idx], next, frac)
                    done.addLine(to: cur)
                    ctx.stroke(done, with: .color(Theme.accent), style: StrokeStyle(lineWidth: 7, lineCap: .round))

                    // stations
                    dot(&ctx, pts[0], Theme.panel, Theme.accent, r: 5)
                    dot(&ctx, pts[1], Theme.accent, Theme.panel, r: 5)
                    dot(&ctx, pts[2], Theme.panel, Theme.tight, r: 6)   // Mannheim ⚠
                    dot(&ctx, pts[3], Theme.panel, Theme.ink3, r: 5)

                    // pulsing you-dot
                    let pulse = 8 + 7 * (0.5 + 0.5 * sin(timeline.date.timeIntervalSinceReferenceDate * 2.6))
                    ctx.fill(Path(ellipseIn: CGRect(x: cur.x - pulse, y: cur.y - pulse, width: 2 * pulse, height: 2 * pulse)),
                             with: .color(Theme.accent.opacity(0.22)))
                    dot(&ctx, cur, Theme.accent, .white, r: 6)
                }
            }
        }
    }

    private func segmentAt(_ t: Double, _ pts: [CGPoint]) -> (Int, Double)? {
        guard pts.count >= 2 else { return nil }
        let seg = t * Double(pts.count - 1)
        return (min(Int(seg), pts.count - 2), seg - Double(Int(seg)))
    }
    private func interp(_ a: CGPoint, _ b: CGPoint, _ f: Double) -> CGPoint {
        CGPoint(x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f)
    }
    private func dot(_ ctx: inout GraphicsContext, _ p: CGPoint, _ fill: Color, _ stroke: Color, r: CGFloat) {
        let rect = CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r)
        ctx.fill(Path(ellipseIn: rect), with: .color(fill))
        ctx.stroke(Path(ellipseIn: rect), with: .color(stroke), lineWidth: 2.5)
    }
}
