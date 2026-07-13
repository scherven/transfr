"""transfr HTTP API.

Connects a user's goal (departure + arrival station) to core/'s
platform-to-platform pathfinder: search journeys via Transitous, then for each
change of train assess whether the platform transfer is walkable in the layover.

Importing this package puts the repo root and core/ on sys.path so the API can
use the algorithm layer (`ground_truth`, `search_context`, `graph`, `db`) and
the tested root-level modules (`journeys`, `stations`) by their module names --
exactly the names those files import each other by. Names are disjoint across
the two directories, so there is no shadowing.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE = os.path.join(_ROOT, "core")
for _p in (_CORE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
