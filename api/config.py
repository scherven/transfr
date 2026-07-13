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

# Default number of itinerary options requested from the journey provider.
DEFAULT_MAX_JOURNEYS = _int("TRANSFR_MAX_JOURNEYS", 5)
MAX_JOURNEYS_LIMIT = _int("TRANSFR_MAX_JOURNEYS_LIMIT", 10)

# CORS: comma-separated origins, or "*" for all (dev default).
CORS_ORIGINS = [o.strip() for o in os.environ.get("TRANSFR_CORS_ORIGINS", "*").split(",") if o.strip()]

# Connection pool bounds.
POOL_MIN = _int("TRANSFR_POOL_MIN", 1)
POOL_MAX = _int("TRANSFR_POOL_MAX", 8)
