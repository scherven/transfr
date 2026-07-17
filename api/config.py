"""
API settings, env-driven with sensible local-dev defaults.

DB connection parameters come from core/db.py's DB_CONFIG (standard PG* vars);
this module only carries the API-level knobs.
"""

import os


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Seconds of slack required on top of the raw walk before a transfer counts as
# comfortably "feasible" rather than "tight".
BUFFER_S = _float("TRANSFR_BUFFER_S", 60.0)

# Enable opt-in synthetic stitch bridges (core/dbgen/build_stitch_bridges.py) on
# every production walk -- BOTH the verdict pathfind (assess_transfer, /transfer)
# and the drawable geometry (viz_export via /walk, /walks) -- so a stitched
# transfer is judged feasible and its walk draws identically (the geometry==verdict
# invariant in api/walks.py). Recovers class-1 "connector ends inside the platform
# polygon" disconnects (e.g. Colmar A->E). A no-op on a DB whose `synthetic_bridges`
# table was never built (SearchContext._load_stitch_bridges guards on to_regclass).
# On by default; TRANSFR_STITCH_BRIDGES=0 restores classic no-stitch routing.
STITCH_BRIDGES = os.environ.get("TRANSFR_STITCH_BRIDGES", "1") != "0"

# Default number of itinerary options requested from the journey provider.
DEFAULT_MAX_JOURNEYS = _int("TRANSFR_MAX_JOURNEYS", 5)
MAX_JOURNEYS_LIMIT = _int("TRANSFR_MAX_JOURNEYS_LIMIT", 10)

# Upper bound on walks requested in one POST /walks batch. A journey rarely has
# more than a handful of transfers; this caps the per-request pathfinding work
# (each walk re-runs the platform search).
MAX_WALKS_BATCH = _int("TRANSFR_MAX_WALKS_BATCH", 12)

# Upper bound on interchanges assessed in one POST /assess. A journey has only a
# handful of changes; the client streams by firing these per-transfer, so this is
# a generous safety cap, not a normal batch size.
MAX_ASSESS_BATCH = _int("TRANSFR_MAX_ASSESS_BATCH", 24)

# CORS: comma-separated origins, or "*" for all (dev default). Irrelevant to the
# native iOS client (CORS is a browser mechanism); tighten before any web surface.
CORS_ORIGINS = [o.strip() for o in os.environ.get("TRANSFR_CORS_ORIGINS", "*").split(",") if o.strip()]

# Beta access controls (see api/security.py). Both opt-in: unset => disabled, so
# dev and tests are unaffected and the deployment enables them explicitly.
#   TRANSFR_API_KEY       shared secret the iOS build sends as the X-API-Key header.
#   TRANSFR_API_KEY_FILE  alternatively, a path to read the secret from -- so the
#                         key can live in a gitignored file (deploy/secrets/api_key)
#                         and never be baked into the launchd plist.
#   TRANSFR_RATE_LIMIT    per-IP ceiling in slowapi syntax, e.g. "60/minute".
def _read_api_key() -> str:
    key = os.environ.get("TRANSFR_API_KEY", "").strip()
    if key:
        return key
    path = os.environ.get("TRANSFR_API_KEY_FILE", "").strip()
    if path:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return ""
    return ""


API_KEY = _read_api_key()
RATE_LIMIT = os.environ.get("TRANSFR_RATE_LIMIT", "").strip()

# Connection pool bounds.
POOL_MIN = _int("TRANSFR_POOL_MIN", 1)
POOL_MAX = _int("TRANSFR_POOL_MAX", 8)
