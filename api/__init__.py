"""transfr HTTP API.

Connects a user's goal (departure + arrival station) to core/'s
platform-to-platform pathfinder: search journeys via Transitous, then for each
change of train assess whether the platform transfer is walkable in the layover.

Journey planning (`journeys`) and station autocomplete (`stations`) live in this
package as `api.journeys` / `api.stations`.

Importing this package puts the repo root, core/, and core/'s submodule
directories on sys.path so the API can use the algorithm layer (`ground_truth`,
`search_context`, `graph` under core/pathfinding/; `db` at core/ root) by the
bare module names those engine files import each other by. Names are disjoint
across the directories, so there is no shadowing.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE = os.path.join(_ROOT, "core")
_SUBMODULES = [os.path.join(_CORE, _d) for _d in ("pathfinding", "dbgen", "viz", "boarding", "tooling")]
for _p in (_CORE, *_SUBMODULES, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
