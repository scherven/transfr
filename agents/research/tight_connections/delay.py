"""Delay-injection analysis (pure computation on the real interchange data
already captured -- no new network calls).

For each real interchange MOTIS offered (train A arrives platform P at t_arr;
train B departs platform Q at t_dep; scheduled gap = t_dep - t_arr >= MOTIS_min),
inject an inbound delay d on A (onward B held on time = the stress case). Then:

  effective_gap = gap - d
  MOTIS keeps it   iff  effective_gap >= MOTIS_min     (else it reroutes)
  core keeps it    iff  effective_gap >= real_walk     (physical limit)

So core RESCUES a connection MOTIS drops exactly when
     gap - MOTIS_min  <  d  <=  gap - real_walk
The rescue-window width is (MOTIS_min - real_walk) = the transfer pad; its
POSITION (how big a delay before MOTIS drops it) is gap - MOTIS_min.
"""

import json, os, sys, statistics as stat

_HERE = os.path.dirname(os.path.abspath(__file__))
# Reads the state files produced by run.py (default suffix, or pass STATE_SUFFIX
# env when you ran the enriched variant, e.g. STATE_SUFFIX=_v2).
_SUFFIX = os.environ.get("STATE_SUFFIX", "")
STATES = [
    (os.path.join(_HERE, f"tight_state{_SUFFIX}.json"), "intercity"),
    (os.path.join(_HERE, f"tight_state_hf{_SUFFIX}.json"), "hf"),
]

SWEEP_MIN = [1, 2, 3, 4, 5, 7, 10, 15]


def load_interchanges():
    seen = set(); out = []
    for path, tag in STATES:
        d = json.load(open(path))
        for r in d["pairs"].values():
            if r.get("status") != "ok":
                continue
            for ic in r.get("interchanges", []):
                w = ic.get("core_walk_s"); m = ic.get("motis_assumed_s"); g = ic.get("gap_s")
                if w is None or m is None or g is None:
                    continue
                # real train-to-train transfer MOTIS actually offered
                if g + 1 < m:      # gap below MOTIS min -> not an offered transfer; skip
                    continue
                # exclude core over-estimates: if core says the walk exceeds the
                # scheduled gap, core contradicts a connection MOTIS offered on time
                # -> core error (Koblenz 9->C=1501s, Stuttgart 14->101=477s). Drop.
                if w > g:
                    continue
                key = (ic.get("hub"), ic.get("arr_track"), ic.get("dep_track"), round(g), round(m))
                if key in seen:
                    continue
                seen.add(key)
                out.append(dict(hub=ic.get("hub"), arr=ic.get("arr_track"), dep=ic.get("dep_track"),
                                gap=g, motis=m, walk=w, tag=tag,
                                pad=m - w, break_delay=g - m, fail_delay=g - w))
    return out


def main():
    ics = load_interchanges()
    print(f"Real core-routed interchanges pooled (deduped): {len(ics)}\n")
    pads = [ic["pad"] for ic in ics]
    print(f"Transfer pad (MOTIS_min - real_walk) = rescue-window width:")
    print(f"  median {stat.median(pads):.0f}s  max {max(pads):.0f}s  "
          f">60s: {sum(1 for p in pads if p>60)}/{len(pads)}  >120s: {sum(1 for p in pads if p>120)}/{len(pads)}\n")

    print(f"Per-interchange delay behaviour (sorted by how small a delay before MOTIS drops it):")
    print(f"  {'hub':24s} {'trk':>7s} {'gap':>5s} {'MOTIS':>5s} {'walk':>5s} {'MOTISdrops>':>11s} {'impossible>':>11s} {'rescue band'}")
    for ic in sorted(ics, key=lambda x: x["break_delay"]):
        band = f"{max(ic['break_delay'],0)/60:.1f}-{ic['fail_delay']/60:.1f}min"
        print(f"  {str(ic['hub'])[:24]:24s} {str(ic['arr'])+'->'+str(ic['dep']):>7s} "
              f"{ic['gap']:5.0f} {ic['motis']:5.0f} {ic['walk']:5.0f} "
              f"{max(ic['break_delay'],0):9.0f}s {ic['fail_delay']:9.0f}s  {band}")

    print(f"\nDelay sweep -- of {len(ics)} interchanges, at inbound delay d:")
    print(f"  {'delay':>6s} {'MOTIS drops':>12s} {'core rescues':>13s} {'physically gone':>16s}")
    for dm in SWEEP_MIN:
        d = dm * 60
        drops = sum(1 for ic in ics if d > ic["break_delay"])
        rescue = sum(1 for ic in ics if ic["break_delay"] < d <= ic["fail_delay"])
        gone = sum(1 for ic in ics if d > ic["fail_delay"])
        print(f"  {dm:4d}min {drops:9d}    {rescue:10d} ({100*rescue/len(ics):.0f}%) {gone:12d}")

    # Rescue as a fraction of MOTIS's drops (i.e. when MOTIS breaks a connection,
    # how often is it actually still makeable?)
    print(f"\nWhen MOTIS drops a connection at delay d, how often is it STILL physically makeable?")
    for dm in SWEEP_MIN:
        d = dm * 60
        drops = sum(1 for ic in ics if d > ic["break_delay"])
        rescue = sum(1 for ic in ics if ic["break_delay"] < d <= ic["fail_delay"])
        print(f"  {dm:4d}min: {rescue}/{drops} of MOTIS drops still makeable"
              + (f" ({100*rescue/drops:.0f}%)" if drops else ""))


if __name__ == "__main__":
    main()
