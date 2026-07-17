"""Richer analysis, works on either state file (intercity or hf).

Usage: analyze2.py <state.json>

Separates, among journeys where an earlier onward train exists in the band
MOTIS excluded:
  - CONFIRMED  : core validated the cross-platform walk fits -> real missed connection
  - UNVALIDATED: an earlier train exists but MOTIS/OSM lack the platform to check
  - NOT-MAKEABLE: core says the walk doesn't fit the gap -> MOTIS was right
and characterizes onward frequency (to show the dataset really is high-frequency).
"""

import json, sys, statistics as stat
from collections import Counter

STATE = sys.argv[1] if len(sys.argv) > 1 else "tight_state.json"


def pct(x, n):
    return f"{100*x/n:.0f}%" if n else "n/a"


def main():
    d = json.load(open(STATE))
    recs = list(d["pairs"].values())
    ok = [r for r in recs if r.get("status") == "ok"]
    direct = [r for r in recs if r.get("status") == "direct"]
    err = [r for r in recs if r.get("status") not in ("ok", "direct")]
    print(f"=== {STATE} ===")
    print(f"PAIRS: {len(recs)} | {len(ok)} with changes | {len(direct)} direct | {len(err)} err/none")
    for r in err:
        print("   ERR", r.get("origin"), "->", r.get("dest"), r.get("status"), str(r.get("error"))[:60])

    # ---- mechanism
    ics = [ic for r in ok for ic in r.get("interchanges", [])]
    routed = [ic for ic in ics if ic.get("core_walk_s") is not None]
    print(f"\nINTERCHANGES: {len(ics)} | both platforms {sum(1 for ic in ics if ic.get('arr_track') and ic.get('dep_track'))}"
          f" | core-routed {len(routed)} ({pct(len(routed),len(ics))})")
    if routed:
        pads = [ic["motis_assumed_s"] - ic["core_walk_s"] for ic in routed]
        print(f"  MOTIS assumed median {stat.median(ic['motis_assumed_s'] for ic in routed):.0f}s vs "
              f"core median {stat.median(ic['core_walk_s'] for ic in routed):.0f}s | median pad {stat.median(pads):+.0f}s")

    # ---- onward frequency characterization
    imps = [r["impact"] for r in ok if r.get("impact")]
    gaps = [im["earliest_onward_gap_s"] for im in imps if im.get("earliest_onward_gap_s") is not None]
    if gaps:
        print(f"\nONWARD FREQUENCY at first hub (n={len(gaps)} journeys):")
        print(f"  earliest onward gap: median {stat.median(gaps):.0f}s  min {min(gaps):.0f}s  max {max(gaps):.0f}s")
        print(f"  journeys with an onward train <=5min after arrival: "
              f"{sum(1 for im in imps if (im.get('earliest_onward_gap_s') or 1e9) <= 300)}")
        print(f"  median onward trains within 10min: {stat.median(im.get('n_onward_10min',0) for im in imps):.0f}")

    # ---- opportunity / miss analysis
    conf, unval, notmake = [], [], []
    for r in ok:
        im = r.get("impact")
        if not im or not im.get("improving"):
            continue
        # earliest-arriving improving candidate
        for c in im["improving"]:
            tag = None
            if c.get("makeable"):
                tag = "CONFIRMED"
            elif c.get("core_walk_s") is None:
                tag = "UNVALIDATED"
            else:
                tag = "NOTMAKE"
            entry = dict(pair=f"{r['origin']}->{r['dest']}", hub=im["hub"],
                         gap=c["gap_s"], improves=c["improves_s"], walk=c.get("core_walk_s"),
                         reason=c.get("core_reason"), motis_min=im.get("motis_assumed_s"),
                         arr_trk=im.get("arr_track"), dep_trk=c.get("dep_track"))
            if tag == "CONFIRMED": conf.append(entry)
            elif tag == "UNVALIDATED": unval.append(entry)
            else: notmake.append(entry)
            break  # only the best improving candidate per journey

    print(f"\nOPPORTUNITY ANALYSIS (journeys with an earlier onward train MOTIS excluded):")
    print(f"  total journeys with such a train: {len(conf)+len(unval)+len(notmake)} of {len(imps)} reconstructed")
    print(f"  CONFIRMED makeable (core-validated real miss): {len(conf)}")
    for e in sorted(conf, key=lambda x: -(x['improves']or 0)):
        print(f"     {e['pair'][:34]:34s} @{e['hub'][:16]:16s} save {e['improves']/60:.0f}min "
              f"gap={e['gap']:.0f}s walk={e['walk']:.0f}s MOTISmin={e['motis_min']} trk {e['arr_trk']}->{e['dep_trk']}")
    print(f"  UNVALIDATED (earlier train exists, no platform to check): {len(unval)}")
    for e in sorted(unval, key=lambda x: -(x['improves'] or 0))[:12]:
        print(f"     {e['pair'][:34]:34s} @{e['hub'][:16]:16s} save {e['improves']/60:.0f}min "
              f"gap={e['gap']:.0f}s MOTISmin={e['motis_min']} reason={e['reason']} trk {e['arr_trk']}->{e['dep_trk']}")
    print(f"  NOT-MAKEABLE (core says walk doesn't fit -> MOTIS right): {len(notmake)}")
    for e in notmake:
        print(f"     {e['pair'][:34]:34s} @{e['hub'][:16]:16s} gap={e['gap']:.0f}s walk={e['walk']:.0f}s")


if __name__ == "__main__":
    main()
