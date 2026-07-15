#!/usr/bin/env python3
"""Generate the hand-verification report: way IDs and openstreetmap.org
links for every test case in tests/test_ground_truth.py, so the results can
be checked against the real map."""
import os
import sys

# core/ root plus the pathfinding submodule (ground_truth moved there in the reorg).
_C = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # core/
for _p in (_C, *(os.path.join(_C, _d) for _d in ("pathfinding", "boarding", "tooling"))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db import connect
from ground_truth import find_shortest_path
from report import format_verification_report

CASES = [
    ("Strasbourg-Ville 1->7", 5347313, "1", "7"),
    ("Strasbourg-Ville 1->2", 5347313, "1", "2"),
    ("Strasbourg-Ville 3->3 (same platform)", 5347313, "3", "3"),
    ("Berlin Hbf 1->2", 5688517, "1", "2"),
    ("Berlin Hbf 1->16 (far apart)", 5688517, "1", "16"),
    ("Colmar A->B (letter refs)", 6365739, "A", "B"),
    ("Basel SBB F->H (letter refs)", 4272361, "F", "H"),
    ("Colmar A->E (known gap in the map data)", 6365739, "A", "E"),
]

conn = connect()
for label, rel_id, ref_1, ref_2 in CASES:
    result = find_shortest_path(conn, rel_id, ref_1, ref_2)
    print(format_verification_report(label, ref_1, ref_2, result))
    print()
conn.close()
