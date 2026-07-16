import SwiftUI
import TransfrCore

/// The boarding / step-off guide, driven by real `BoardingGuidance` from `/walk`
/// (api/boarding.py). Replaces the prototype's hard-coded "sector C · coach 3"
/// mock: the position comes from the resolved path's step-off node projected onto
/// the OSM platform edge, and the coach is shown ONLY when a live formation feed
/// resolves it (it doesn't, from a generic host — so we say so rather than invent
/// one).
///
/// `BoardingGuidance.stepoffFraction` is oriented so 1.0 is the platform end
/// nearest the departure side, which is why the strip puts "→ Pl {dep}" on the
/// right and the marker reads left-to-right as "further toward your connection".

// MARK: - Copy (shared by the card and the compact cue)

enum BoardingCopy {
    /// Which end of the platform the step-off sits toward, in the traveller's
    /// terms. Honest about orientation: we know the geometry, not the painted
    /// sector letters, so we name the end relative to the onward platform.
    static func end(_ g: BoardingGuidance) -> String {
        if g.stepoffFraction >= 0.6 { return "the Platform \(g.departurePlatform) end" }
        if g.stepoffFraction <= 0.4 { return "the far end" }
        return "the middle"
    }

    /// The one-line instruction. `low` significance means position barely matters;
    /// no position means the platform isn't finely enough mapped to say.
    static func instruction(_ g: BoardingGuidance) -> String {
        guard g.hasPosition else {
            return "We can't pin the best door here — this platform isn't finely mapped."
        }
        if g.band == .low {
            return "Board at any door — every coach is about the same walk to Platform \(g.departurePlatform)."
        }
        return "Board toward \(end(g)) — stepping off there saves up to \(Fmt.approxSaved(g.timeSavedS)) over the far end."
    }

    /// The compact step-off cue for the on-trip / AR overlays.
    static func stepoff(_ g: BoardingGuidance) -> String {
        guard g.hasPosition else { return "Step off and follow the path to Platform \(g.departurePlatform)." }
        if g.band == .low { return "Step off at any door — it's the same walk to Platform \(g.departurePlatform)." }
        return "Step off toward \(end(g)) — that's your quickest line to Platform \(g.departurePlatform)."
    }

    /// The significance chip text + whether it's an emphasised (accent) chip.
    static func savedChip(_ g: BoardingGuidance) -> (text: String, emphasised: Bool) {
        guard g.hasPosition, g.band != .low else { return ("any door", false) }
        return ("saves up to \(Fmt.approxSaved(g.timeSavedS))", true)
    }
}

// MARK: - The platform position strip

/// A proportional platform bar with a marker at the optimal step-off. Deliberately
/// NOT labelled with sector letters (A/B/C…): those are painted signage we don't
/// have data for, and inventing them is exactly the mock this replaces. It shows a
/// true along-platform position, with the connection direction marked.
struct BoardingStrip: View {
    let guidance: BoardingGuidance
    var accent: Color = Theme.accent

    private var fraction: CGFloat { CGFloat(min(max(guidance.stepoffFraction, 0), 1)) }

    var body: some View {
        VStack(spacing: 5) {
            HStack {
                Text("Platform \(guidance.arrivalPlatform)")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Theme.ink3)
                Spacer()
                Text("→ Pl \(guidance.departurePlatform)")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundStyle(accent)
            }
            GeometryReader { geo in
                let w = geo.size.width
                let inset: CGFloat = 10
                let x = inset + fraction * (w - 2 * inset)
                ZStack(alignment: .topLeading) {
                    // Platform bar with faint zone dividers (texture, not signage).
                    RoundedRectangle(cornerRadius: 7).fill(Theme.panel3)
                    HStack(spacing: 0) {
                        ForEach(1..<6) { _ in
                            Spacer()
                            Rectangle().fill(Theme.line).frame(width: 1)
                        }
                        Spacer()
                    }
                    .padding(.vertical, 6)
                    // The step-off marker.
                    marker.position(x: x, y: geo.size.height / 2)
                }
            }
            .frame(height: 34)
            Text("\(Int(guidance.platformLengthM.rounded())) m platform")
                .font(.system(size: 10)).foregroundStyle(Theme.ink3)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var marker: some View {
        VStack(spacing: 2) {
            Circle().fill(accent).frame(width: 12, height: 12)
                .overlay(Circle().strokeBorder(Theme.paper, lineWidth: 2))
            Rectangle().fill(accent).frame(width: 2, height: 10)
        }
        .shadow(color: accent.opacity(0.4), radius: 4)
    }
}

// MARK: - The full card (carousel)

/// The boarding module for the transfer carousel. Fold in the walk's real level
/// changes as a step-free / stairs note (also from the export, not invented).
struct BoardingCard: View {
    let guidance: BoardingGuidance?
    var levelNote: String?          // derived from the walk's real transitions

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            content
            if let levelNote { levelRow(levelNote) }
            footnote
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel2))
    }

    private var header: some View {
        HStack {
            Label("Where to stand", systemImage: "tram.fill")
                .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.ink)
            Spacer()
            // Only chip a claim we can back: a measured position. A coarse
            // platform shows no chip rather than an "any door" we can't stand behind.
            if let g = guidance, g.hasPosition {
                let chip = BoardingCopy.savedChip(g)
                Text(chip.text)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(chip.emphasised ? Theme.accent : Theme.ink3)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Capsule().fill(chip.emphasised ? Theme.accentSoft : Theme.panel))
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if let g = guidance {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: g.hasPosition ? (g.band == .low ? "arrow.left.and.right" : "figure.walk")
                                                : "questionmark.circle")
                    .foregroundStyle(g.hasPosition && g.band != .low ? Theme.accent : Theme.ink3)
                    .font(.system(size: 14))
                Text(BoardingCopy.instruction(g)).font(.system(size: 13)).foregroundStyle(Theme.ink2)
            }
            if g.hasPosition {
                BoardingStrip(guidance: g, accent: g.band == .low ? Theme.ink3 : Theme.accent)
                    .padding(.top, 2)
            }
        } else {
            // Walk geometry not loaded yet (or the sample tier): honest, brief.
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Working out the best door…").font(.system(size: 13)).foregroundStyle(Theme.ink3)
            }
        }
    }

    private func levelRow(_ note: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: note.contains("Step-free") ? "checkmark" : "figure.stairs")
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(RoundedRectangle(cornerRadius: 7)
                    .fill(note.contains("Step-free") ? Theme.go : Theme.stair))
            Text(note).font(.system(size: 12)).foregroundStyle(Theme.ink2)
        }
    }

    @ViewBuilder
    private var footnote: some View {
        // Present exactly when position is known but the coach isn't — the honest
        // data gap (formation feeds are geo-blocked from a generic host).
        if let g = guidance, g.hasPosition, g.coach == nil {
            Text("Coach numbers need a live train-formation feed — not on this route yet.")
                .font(.system(size: 10.5)).foregroundStyle(Theme.ink3)
        }
    }
}

// MARK: - Compact step-off cue (on-trip / AR)

/// A single-line "the moment you step off" cue, for the Live card and AR banner.
struct BoardingStepoffCue: View {
    let guidance: BoardingGuidance?
    let departurePlatform: String
    var onDark: Bool = false

    private var tint: Color { onDark ? .white : Theme.ink2 }
    private var accent: Color { onDark ? Color(hex: 0x4EA6FF) : Theme.accent }

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "figure.walk.motion")
                .font(.system(size: 15, weight: .bold)).foregroundStyle(accent)
            Text(text).font(.system(size: 13)).foregroundStyle(tint)
        }
    }

    private var text: String {
        if let g = guidance { return BoardingCopy.stepoff(g) }
        return "Step off and follow the path to Platform \(departurePlatform)."
    }
}
