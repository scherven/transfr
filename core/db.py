"""Shared database connection helper.

Reads connection parameters from standard PG* environment variables (with
local-dev defaults) instead of hardcoding credentials in application code.
Everything in core/ connects to the new, minimal `transfr_eu` database --
not the legacy `openrailwaymap` database.
"""

import os

import psycopg2
import psycopg2.extras

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
    "dbname": os.environ.get("PGDATABASE", "transfr_eu"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "password": os.environ.get("PGPASSWORD", ""),
}


def connect(**overrides):
    """Open a new connection with RealDictCursor as the default cursor factory."""
    config = {**DB_CONFIG, **overrides}
    return psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **config)
