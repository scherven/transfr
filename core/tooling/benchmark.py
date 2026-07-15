#!/usr/bin/env python3
"""
Compare every registered algorithm's correctness and speed against the
"dijkstra" baseline on a fixed set of real stations.

Usage:
    python core/benchmark.py

Correctness check: found/not-found status and walking_time_seconds must
match the baseline exactly (within floating point tolerance) -- any
disagreement is printed as a MISMATCH, not silently averaged away.
"""
import os
import sys
import time

# core/ root plus the pathfinding submodule (algorithms/graph/ground_truth moved
# there in the reorg). Lets this dev tool run directly and as `python -m core.benchmark`.
_C = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # core/
for _p in (_C, *(os.path.join(_C, _d) for _d in ("pathfinding", "boarding", "tooling"))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from algorithms import ALGORITHMS, BASELINE
from db import connect
from ground_truth import find_shortest_path

# Same fixtures as tests/test_ground_truth.py's TRANSFER_CASES, duplicated
# here deliberately (no test-suite import) so this stays runnable standalone.
CASES = [
    ("Strasbourg-Ville 1->7", 5347313, "1", "7"),
    ("Strasbourg-Ville 1->2", 5347313, "1", "2"),
    ("Strasbourg-Ville 3->3 (same platform)", 5347313, "3", "3"),
    ("Berlin Hbf 1->2", 5688517, "1", "2"),
    ("Berlin Hbf 1->16 (far apart)", 5688517, "1", "16"),
    ("Colmar A->B (letter refs)", 6365739, "A", "B"),
    ("Basel SBB F->H (letter refs)", 4272361, "F", "H"),
    ("Colmar A->E (known disconnected)", 6365739, "A", "E"),
]


def main() -> int:
    conn = connect()
    names = list(ALGORITHMS.keys())
    if len(names) < 2:
        print(f"Only one algorithm registered ({names}); add more to algorithms.py to compare.")
        return 0

    print(f"{'case':42s} " + " ".join(f"{n:>16s}" for n in names))
    mismatches = []
    totals = {n: 0.0 for n in names}

    for label, rel_id, ref_1, ref_2 in CASES:
        baseline_result = None
        row = [f"{label:42s}"]
        for name in names:
            t0 = time.monotonic()
            result = find_shortest_path(conn, rel_id, ref_1, ref_2, algorithm=name)
            elapsed = time.monotonic() - t0
            totals[name] += elapsed

            if name == BASELINE:
                baseline_result = result
            cell = f"{elapsed:.3f}s"
            if result["found"]:
                cell += f" ({result['walking_time_seconds']}s)"
            else:
                cell += f" ({result['reason']})"
            row.append(f"{cell:>16s}")

            if baseline_result is not None and name != BASELINE:
                same_found = result["found"] == baseline_result["found"]
                same_time = (
                    not result["found"]
                    or abs(result["walking_time_seconds"] - baseline_result["walking_time_seconds"]) < 1e-6
                )
                if not (same_found and same_time):
                    mismatches.append((label, name, result, baseline_result))
        print(" ".join(row))

    print()
    print("Total time by algorithm:")
    for name in names:
        print(f"  {name:16s} {totals[name]:8.3f}s")

    if mismatches:
        print(f"\n{len(mismatches)} MISMATCH(ES) vs baseline:")
        for label, name, result, baseline_result in mismatches:
            print(f"  [{name}] {label}: got {result}, baseline was {baseline_result}")
        conn.close()
        return 1

    print("\nAll algorithms agree with the baseline.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
