"""Run the tight-connection experiment over a set of O-D pairs and checkpoint."""

from __future__ import annotations

import sys, os, json, time as _time
from dataclasses import asdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import iris  # noqa: E402  DB IRIS platform enrichment
from tight import (  # noqa: E402
    plan, best_itin, interchanges_of, core_walk, enumerate_onward,
    itin_arrival, load_state, save_state, _iso, _db, geocode,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

BUFFER_S = 60.0  # slack required on top of the real walk before we call it makeable

# O-D pairs. Swiss pulse nodes first (cross-platform-heavy by timetable design),
# then German intercity corridors through big hubs. Departure at 08:00 local.
PAIRS = [
    # --- Switzerland (Taktknoten: Zurich, Bern, Basel, Olten, Biel, Lucerne) ---
    ("Genève", "St. Gallen"), ("Lausanne", "Luzern"), ("Basel SBB", "Chur"),
    ("Brig", "Zürich HB"), ("Biel/Bienne", "Lugano"), ("Neuchâtel", "Winterthur"),
    ("Fribourg", "St. Gallen"), ("Sion", "Luzern"), ("Interlaken Ost", "Basel SBB"),
    ("La Chaux-de-Fonds", "Zürich HB"), ("Locarno", "Bern"), ("Chur", "Genève"),
    ("Luzern", "Lausanne"), ("St. Gallen", "Sion"), ("Zürich HB", "Brig"),
    ("Schaffhausen", "Bern"), ("Bellinzona", "Basel SBB"), ("Thun", "Zürich HB"),
    # --- Germany (Mannheim, Köln, Hamburg, Frankfurt, Hannover, Stuttgart) ---
    ("Saarbrücken Hbf", "Stuttgart Hbf"), ("Kaiserslautern Hbf", "Frankfurt (Main) Hbf"),
    ("Freiburg (Breisgau) Hbf", "Nürnberg Hbf"), ("Kiel Hbf", "Bremen Hbf"),
    ("Konstanz", "Stuttgart Hbf"), ("Kassel-Wilhelmshöhe", "München Hbf"),
    ("Trier Hbf", "Frankfurt (Main) Hbf"), ("Münster (Westf) Hbf", "Frankfurt (Main) Hbf"),
    ("Aachen Hbf", "Berlin Hbf"), ("Rostock Hbf", "Hannover Hbf"),
    ("Karlsruhe Hbf", "München Hbf"), ("Bonn Hbf", "Hamburg Hbf"),
    ("Ulm Hbf", "Frankfurt (Main) Hbf"), ("Osnabrück Hbf", "Berlin Hbf"),
    ("Würzburg Hbf", "Hamburg Hbf"), ("Heidelberg Hbf", "Dresden Hbf"),
    # --- Austria / cross-border ---
    ("Salzburg Hbf", "Graz Hbf"), ("Innsbruck Hbf", "Wien Hbf"),
    ("Bregenz", "Wien Hbf"), ("München Hbf", "Wien Hbf"),
]

# High-frequency dataset: O-D pairs whose natural change is at a dense S-Bahn /
# regional junction where onward trains toward D run every few minutes -- so an
# earlier train can actually sit inside the [real_walk, MOTIS_minimum) band.
PAIRS_HF = [
    # --- Berlin (Stadtbahn + Ring S-Bahn every ~5 min, frequent regional) ---
    ("Erkner", "Berlin Südkreuz"), ("Strausberg", "Berlin Gesundbrunnen"),
    ("Fürstenwalde (Spree)", "Berlin Hauptbahnhof"), ("Oranienburg", "Berlin Ostkreuz"),
    ("Bernau bei Berlin", "Berlin Südkreuz"), ("Potsdam Hauptbahnhof", "Berlin Ostbahnhof"),
    ("Königs Wusterhausen", "Berlin Gesundbrunnen"), ("Nauen", "Berlin Ostkreuz"),
    # --- Munich (S-Bahn Stammstrecke, ~2-3 min combined headway on the trunk) ---
    ("Freising", "München-Pasing"), ("Erding", "München-Pasing"),
    ("Tutzing", "München Ost"), ("Petershausen(Obb)", "München Ost"),
    ("Wolfratshausen", "München Ost"), ("Ebersberg(Oberbay)", "München-Pasing"),
    # --- Frankfurt Rhein-Main (S-Bahn Stammstrecke ~2-3 min) ---
    ("Hanau Hauptbahnhof", "Frankfurt-Höchst"), ("Wiesbaden Hauptbahnhof", "Frankfurt (Main) Süd"),
    ("Darmstadt Hauptbahnhof", "Frankfurt (Main) West"), ("Offenbach (Main) Ost", "Frankfurt (Main) West"),
    # --- Zürich S-Bahn (cross-platform at HB / Oerlikon / Stadelhofen) ---
    ("Winterthur", "Zürich Stadelhofen"), ("Uster", "Zürich Hardbrücke"),
    ("Wetzikon", "Zürich Oerlikon"), ("Zug", "Zürich Flughafen"),
    # --- Hamburg (S-Bahn + regional) ---
    ("Ahrensburg", "Hamburg-Altona"), ("Elmshorn", "Hamburg-Harburg"),
    # --- Rhein-Ruhr (very dense regional/S-Bahn) ---
    ("Wuppertal Hauptbahnhof", "Düsseldorf Flughafen"), ("Essen Hauptbahnhof", "Köln Messe/Deutz"),
    ("Duisburg Hauptbahnhof", "Solingen Hauptbahnhof"), ("Hagen Hauptbahnhof", "Düsseldorf Hauptbahnhof"),
    # --- Stuttgart S-Bahn ---
    ("Ludwigsburg", "Stuttgart-Vaihingen"), ("Esslingen (Neckar)", "Stuttgart-Bad Cannstatt"),
]

DATASET = os.environ.get("DATASET", "intercity")
SUFFIX = os.environ.get("STATE_SUFFIX", "")
import tight as _t
_base = "tight_state_hf" if DATASET == "hf" else "tight_state"
_t.STATE_PATH = os.path.join(os.path.dirname(__file__), f"{_base}{SUFFIX}.json")
if DATASET == "hf":
    PAIRS = PAIRS_HF

DEP_LOCAL = os.environ.get("DEP", "2026-07-14T08:00:00+02:00")


def process_pair(conn, origin: str, dest: str, when: datetime) -> dict:
    o = geocode(origin)["id"]; d = geocode(dest)["id"]
    data = plan(o, d, when, n=6)
    it = best_itin(data)
    rec: dict = {"origin": origin, "dest": dest, "when": when.isoformat()}
    if it is None:
        rec["status"] = "no_itinerary"; return rec
    baseline_arr = itin_arrival(it)
    ics = interchanges_of(it)
    rec["baseline_arrival"] = baseline_arr.isoformat() if baseline_arr else None
    rec["n_changes"] = len(ics)
    if not ics:
        rec["status"] = "direct"; return rec

    # MECHANISM: core walk for every interchange
    for ic in ics:
        core_walk(conn, ic)
    rec["interchanges"] = [asdict(ic) for ic in ics]

    # IMPACT: reconstruct at the FIRST interchange (hold train A into H fixed).
    # Only worth checking if the arrival platform is core-routable at all.
    ic0 = ics[0]
    rec["impact"] = None
    if ic0.hub_id_arr and ic0.t_arr and ic0.arr_track:
        t_arr = _iso(ic0.t_arr)
        onward = enumerate_onward(ic0.hub_id_arr, d, t_arr, pages=1, n=10)
        cands = []
        for op in onward:
            t_dep = _iso(op.t_dep)
            fa = _iso(op.final_arr)
            if t_dep is None or t_dep < t_arr or fa is None:
                continue
            cands.append({
                "t_dep": op.t_dep, "dep_track": op.dep_track, "line": op.first_line,
                "gap_s": (t_dep - t_arr).total_seconds(), "final_arr": op.final_arr,
                "improves_s": (baseline_arr - fa).total_seconds() if baseline_arr else None,
                "op": op,
            })
        # Frequency diagnostics: how dense is onward service toward D at this hub?
        gaps = sorted(c["gap_s"] for c in cands)
        earliest_onward_gap_s = gaps[0] if gaps else None
        n_onward_10min = sum(1 for g in gaps if g <= 600)

        # Core-assess ONLY candidates that would actually beat baseline (cheap: usually few).
        improving = sorted([c for c in cands if (c["improves_s"] or 0) > 60],
                           key=lambda c: c["final_arr"])
        walkcache: dict = {}
        for c in improving:
            op = c["op"]
            # IRIS enrichment: fill the onward departure platform MOTIS omitted.
            if op.dep_track is None and op.dep_lat is not None:
                p, how = iris.fill(op.dep_lat, op.dep_lon, op.hub_name or ic0.hub,
                                   op.first_line, op.headsign)
                if p:
                    op.dep_track, op.dep_track_src = p, "iris"
                    c["dep_track_iris_how"] = how
            c["dep_track"] = op.dep_track
            c["dep_track_src"] = op.dep_track_src
            key = op.dep_track
            if key not in walkcache:
                walkcache[key] = assess_walk(conn, ic0, op)
            w, reason = walkcache[key]
            c["core_walk_s"] = w
            c["core_reason"] = reason
            c["makeable"] = (w is not None and c["gap_s"] >= w + BUFFER_S)
            c["makeable_nobuf"] = (w is not None and c["gap_s"] >= w)
        for c in cands:
            c.pop("op", None)
        beat = [c for c in improving if c.get("makeable")]
        rec["impact"] = {
            "hub": ic0.hub, "arr_track": ic0.arr_track, "t_arr": ic0.t_arr,
            "baseline_arrival": baseline_arr.isoformat() if baseline_arr else None,
            "motis_assumed_s": ic0.motis_assumed_s, "core_walk_s": ic0.core_walk_s,
            "n_onward": len(cands), "n_improving": len(improving),
            "earliest_onward_gap_s": earliest_onward_gap_s,
            "n_onward_10min": n_onward_10min,
            "improving": improving,
            "missed": bool(beat),
            "best_saving_s": beat[0]["improves_s"] if beat else 0,
            "best_gap_s": beat[0]["gap_s"] if beat else None,
            "best_core_walk_s": beat[0]["core_walk_s"] if beat else None,
        }
    rec["status"] = "ok"
    return rec


def assess_walk(conn, ic, op):
    """Real core walk from ic.arr platform to op.dep platform at the hub."""
    from api.transfers import assess_transfer
    if ic.arr_track is None or op.dep_track is None:
        return (None, "no_platform_data")
    a = assess_transfer(
        conn,
        arr_lat=ic.arr_lat, arr_lon=ic.arr_lon, arr_platform=ic.arr_track, arr_time=ic.t_arr,
        dep_lat=op.dep_lat, dep_lon=op.dep_lon, dep_platform=op.dep_track, dep_time=op.t_dep,
    )
    return (a.walk_time_s, a.reason if a.walk_time_s is None else None)


def main():
    when = datetime.fromisoformat(DEP_LOCAL)
    state = load_state()
    pairs = state.setdefault("pairs", {})
    conn = _db.connect()
    try:
        for i, (o, d) in enumerate(PAIRS):
            key = f"{o}->{d}@{when.isoformat()}"
            if key in pairs and pairs[key].get("status") not in (None, "error"):
                print(f"[{i+1}/{len(PAIRS)}] skip {key}"); continue
            print(f"[{i+1}/{len(PAIRS)}] {o} -> {d} ...", flush=True)
            try:
                rec = process_pair(conn, o, d, when)
            except Exception as e:  # noqa: BLE001
                rec = {"origin": o, "dest": d, "status": "error", "error": f"{type(e).__name__}: {e}"}
                print("   ERROR", rec["error"])
            imp = rec.get("impact") or {}
            print(f"   status={rec.get('status')} changes={rec.get('n_changes')} "
                  f"missed={imp.get('missed')} saving_s={imp.get('best_saving_s')}")
            pairs[key] = rec
            save_state(state)
            _time.sleep(0.7)
    except KeyboardInterrupt:
        print("\nInterrupted -- state saved at", __import__("tight").STATE_PATH)
        save_state(state)
        sys.exit(0)
    finally:
        conn.close()
    print("DONE. state at", __import__("tight").STATE_PATH)


if __name__ == "__main__":
    from api.transfers import assess_transfer  # ensure importable
    main()
