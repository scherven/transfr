import Foundation

// MARK: - Offline station catalog (bundled corpus, client-side)

/// The offline autocomplete corpus: station suggestions parsed from a bundled
/// `stations.csv` (the trainline-eu/stations dataset, trimmed to its *suggestable*
/// stops — see `AttributionsView` for the ODbL credit). A pure value type like
/// `RecentSearch`: parsing and the ranked prefix/substring search live here so
/// they're unit-tested headlessly, while loading the bundled resource lives in the
/// app layer (`SampleRepository`). This replaces the old 9-station seed so offline
/// autocomplete returns real hits with no server (issue #40 / TODO §8).
public struct StationCatalog: Sendable {
    /// Every station in the corpus, in file order.
    public let stations: [StationSuggestion]

    /// `stations[i].name` pre-folded (lowercased, diacritics stripped) so `search`
    /// doesn't re-fold tens of thousands of names on every keystroke.
    private let folded: [String]

    /// `stations[i]` is a main station (trainline's `is_main_station`) — the ranking
    /// tiebreak that floats "Frankfurt (Main) Hbf" above "Frankfurt Hahn". Not part
    /// of the `StationSuggestion` contract, so it's carried here alongside the corpus.
    private let isMain: [Bool]

    /// Build from suggestions with no main-station data — every entry ranks as a
    /// non-main station. Used by the seed fallback and tests.
    public init(stations: [StationSuggestion]) {
        self.init(stations: stations, isMain: [Bool](repeating: false, count: stations.count))
    }

    init(stations: [StationSuggestion], isMain: [Bool]) {
        self.stations = stations
        self.folded = stations.map { Self.fold($0.name) }
        self.isMain = isMain
    }

    /// Parse the bundled CSV. Expected header:
    /// `id;name;country;latitude;longitude;is_main` — semicolon-delimited, as
    /// trainline publishes (station names contain commas, so a comma delimiter can't
    /// be used; the bundled file is pre-filtered so no name contains a `;`, hence no
    /// quoting). `is_main` is `1` for a main station, blank otherwise. Tolerant: a
    /// header row is skipped if present, blank lines and rows without a name are
    /// dropped, and missing/blank coordinates, country or `is_main` decode to
    /// nil/false rather than failing the row.
    public init(csv: String) {
        var out: [StationSuggestion] = []
        var main: [Bool] = []
        out.reserveCapacity(52_000)
        main.reserveCapacity(52_000)
        var isFirst = true
        csv.enumerateLines { line, _ in
            defer { isFirst = false }
            if line.isEmpty { return }
            let cols = line.split(separator: ";", omittingEmptySubsequences: false).map(String.init)
            // Drop a header row if the file carries one.
            if isFirst, cols.count >= 2, cols[1] == "name" { return }
            guard cols.count >= 2 else { return }
            func field(_ i: Int) -> String? {
                guard i < cols.count else { return nil }
                let v = cols[i].trimmingCharacters(in: .whitespaces)
                return v.isEmpty ? nil : v
            }
            guard let name = field(1) else { return }
            out.append(StationSuggestion(
                id: field(0),
                name: name,
                latitude: field(3).flatMap(Double.init),
                longitude: field(4).flatMap(Double.init),
                country: field(2)))
            main.append(field(5) == "1")
        }
        self.init(stations: out, isMain: main)
    }

    /// Case- and diacritic-insensitive autocomplete over the corpus. Whole-name
    /// prefix matches rank first ("berlin" → *Berlin* …), then any substring match;
    /// within a tier, main stations lead ("frankfurt" → *Frankfurt (Main) Hbf*),
    /// then the shorter, then alphabetically-earlier name — mirroring the server's
    /// `/stations` ranking (`api/stations.py`). Returns at most `limit`: the dropdown
    /// shows a handful, and a cap keeps a 2-char query over tens of thousands of rows
    /// cheap. A blank query yields nothing.
    public func search(_ query: String, limit: Int = 20) -> [StationSuggestion] {
        let q = Self.fold(query)
        guard !q.isEmpty, limit > 0 else { return [] }
        var scored: [(rank: Int, mainRank: Int, len: Int, idx: Int)] = []
        for i in stations.indices {
            let name = folded[i]
            let rank: Int
            if name.hasPrefix(q) { rank = 0 }
            else if name.contains(q) { rank = 1 }
            else { continue }
            scored.append((rank, isMain[i] ? 0 : 1, name.count, i))
        }
        scored.sort {
            if $0.rank != $1.rank { return $0.rank < $1.rank }
            if $0.mainRank != $1.mainRank { return $0.mainRank < $1.mainRank }
            if $0.len != $1.len { return $0.len < $1.len }
            return stations[$0.idx].name < stations[$1.idx].name
        }
        return scored.prefix(limit).map { stations[$0.idx] }
    }

    /// Lowercased, diacritic-stripped form so "munchen"/"MÜNCHEN" both match
    /// "München" and "gottingen" matches "Göttingen". `locale: nil` folds
    /// locale-independently (canonical) — deterministic, and side-steps the
    /// Turkish dotless-i case-folding surprise.
    static func fold(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespaces)
            .folding(options: [.diacriticInsensitive, .caseInsensitive], locale: nil)
    }
}
