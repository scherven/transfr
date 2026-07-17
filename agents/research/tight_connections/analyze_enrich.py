"""Within-run IRIS contribution (avoids the noisy before/after: MOTIS returns
slightly different itineraries each run). Reports, in one enriched run, how many
platforms IRIS supplied that MOTIS omitted, and how many became real core walks."""

import json, os, sys
from collections import Counter

SC = os.path.dirname(os.path.abspath(__file__))


def report(fn, label):
    path = os.path.join(SC, fn)
    if not os.path.exists(path):
        print(f"{label}: (no file)"); return
    pairs = json.load(open(path))["pairs"]
    ok = [r for r in pairs.values() if r.get("status") == "ok"]
    ics = [ic for r in ok for ic in r.get("interchanges", [])]
    routed = [ic for ic in ics if ic.get("core_walk_s") is not None]

    dep_iris = [ic for ic in ics if ic.get("dep_track_src") == "iris"]
    arr_iris = [ic for ic in ics if ic.get("arr_track_src") == "iris"]
    any_iris = [ic for ic in ics if ic.get("dep_track_src") == "iris" or ic.get("arr_track_src") == "iris"]
    iris_routed = [ic for ic in any_iris if ic.get("core_walk_s") is not None]

    print(f"\n{'='*60}\n{label}\n{'='*60}")
    print(f"interchanges: {len(ics)} | core-routed: {len(routed)} ({100*len(routed)//max(len(ics),1)}%)")
    print(f"IRIS supplied a platform MOTIS omitted:")
    print(f"  departure side: {len(dep_iris)}  (confidence {dict(Counter(ic.get('dep_track_iris_how') for ic in dep_iris))})")
    print(f"  arrival side:   {len(arr_iris)}  (confidence {dict(Counter(ic.get('arr_track_iris_how') for ic in arr_iris))})")
    print(f"  interchanges touched by IRIS: {len(any_iris)}, of which core then routed: {len(iris_routed)}")
    if iris_routed:
        print("  -> rescued into a real core walk:")
        for ic in iris_routed:
            tag = []
            if ic.get("dep_track_src") == "iris": tag.append(f"dep{ic['dep_track']}[{ic.get('dep_track_iris_how')}]")
            if ic.get("arr_track_src") == "iris": tag.append(f"arr{ic['arr_track']}[{ic.get('arr_track_iris_how')}]")
            print(f"     {ic['hub'][:22]:22s} {ic['arr_line']}->{ic['dep_line']} {' '.join(tag)} "
                  f"core={ic['core_walk_s']:.0f}s")
    # impact side
    impr = [c for r in ok if r.get("impact") for c in r["impact"].get("improving", [])]
    iris_impact = [c for c in impr if c.get("dep_track_src") == "iris" and c.get("core_walk_s") is not None]
    print(f"impact: {len(impr)} earlier onward trains examined; {len(iris_impact)} validated via an IRIS platform")
    for c in iris_impact:
        print(f"     {c.get('line')} dep-trk {c.get('dep_track')} gap={c['gap_s']:.0f}s walk={c['core_walk_s']:.0f}s makeable={c.get('makeable')}")


report(sys.argv[1] if len(sys.argv) > 1 else "tight_state_v2.json", "INTERCITY (IRIS dep+arr)")
report(sys.argv[2] if len(sys.argv) > 2 else "tight_state_hf_v2.json", "HIGH-FREQUENCY (IRIS dep+arr)")
