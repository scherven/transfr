"""
Beta access controls for the API: a shared API key and a rate limiter.

Both are **opt-in via env**, so local dev and the test suite (which set neither)
are unaffected, and the beta deployment turns them on explicitly:

    TRANSFR_API_KEY=<long-random-string>     # the iOS build ships this header
    TRANSFR_RATE_LIMIT=60/minute             # per-client-IP ceiling

When TRANSFR_API_KEY is unset, `require_api_key` is a no-op (open, as before).
When TRANSFR_RATE_LIMIT is unset, the limiter registers no default limit.

This is intentionally lightweight -- a single shared key gates a single-tenant
beta, not per-user auth. It stops an exposed tunnel from being an open door to
MOTIS calls and platform pathfinding; it is not a substitute for real auth if a
web/multi-user surface is ever added (see the web-surface note in the README).
"""

import secrets

from fastapi import Header, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address

from api import config

# One process-global limiter, keyed by client IP. In-memory storage is correct
# for the single-uvicorn beta; a multi-process/multi-host deploy would need a
# shared backend (e.g. Redis) so the counters are global rather than per-worker.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[config.RATE_LIMIT] if config.RATE_LIMIT else [],
    headers_enabled=True,  # emit X-RateLimit-* and Retry-After
)


async def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    """FastAPI dependency: reject requests without the shared key -- but only when
    a key is configured. Constant-time compare so the check can't be timed."""
    if not config.API_KEY:
        return  # auth disabled (dev / tests)
    if not (x_api_key and secrets.compare_digest(x_api_key, config.API_KEY)):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
