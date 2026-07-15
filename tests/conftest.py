"""Shared pytest bootstrap.

The test modules import the engine by bare module name (``from graph import
...``, ``from dijkstra import ...``, ``from db import ...``). core/ used to be a
single flat directory on sys.path; it is now split into core/pathfinding/,
core/dbgen/ and core/viz/, with db.py and the formation/live/tooling layer left
at the core/ root. Putting all of those directories on sys.path here -- before
any test module is collected -- keeps every existing bare import resolving,
regardless of which submodule a file now lives in. The per-file
``sys.path.insert(..., "core")`` lines in the tests remain harmless.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE = os.path.join(_ROOT, "core")
_DIRS = [_CORE] + [os.path.join(_CORE, d) for d in ("pathfinding", "dbgen", "viz", "boarding", "tooling")] + [_ROOT]
for _p in _DIRS:
    if _p not in sys.path:
        sys.path.insert(0, _p)
