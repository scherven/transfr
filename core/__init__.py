"""transfr core engine.

Historically every module in core/ lived in one flat directory and imported
its siblings by bare name (``from graph import ...``), relying on core/ being
on sys.path. The engine is now split into submodules:

  * core/pathfinding/ -- the graph search engine (graph, dijkstra, a*, ...)
  * core/dbgen/       -- building the transfr_eu database from OSM (etl, indexes)
  * core/viz/         -- debug/verification visualization (viz_export, viz_render)
  * core/boarding/    -- seat-aware boarding/coach-formation/live-data layer
  * core/tooling/     -- dev/verification tooling (benchmark, reports, demos)

db.py (the shared DB connection helper used by every submodule and by api/)
stays at the core/ root.

To keep the flat bare-import contract working unchanged across all entry points
(the api/ service, pytest, and ``python -m core.<sub>.<script>`` runs), importing
this package puts core/ and each submodule directory on sys.path. That means a
module in any submodule can still do ``from graph import ...`` / ``from db import
...`` without knowing which directory a sibling now lives in.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, *(os.path.join(_HERE, _d) for _d in ("pathfinding", "dbgen", "viz", "boarding", "tooling"))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
