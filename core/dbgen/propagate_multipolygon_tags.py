#!/usr/bin/env python3
"""
Copy walk-relevant tags from `type=multipolygon` RELATIONS onto their untagged
member ways. Run once after core/etl.py loads/reloads data (same rule as
build_node_way_ids.py -- a reload wipes the effect and it must be re-run).

Why this exists
---------------
OSM maps an indoor corridor, hall or plaza as a multipolygon: the tags live on
the RELATION and the member ring way carries NOTHING. Our graph reads ways, so
such a ring arrives untagged, and that is quietly wrong in two ways:

  1. `parse_levels(None)` -> [0.0], i.e. the way is treated as GROUND level. A
     walk crossing a level=-1 passage is then charged no vertical cost at all,
     so the transfer time is under-estimated -- it silently omits the descent
     and the climb back.
  2. `is_walkable_way` is a denylist (it only excludes public_transport=station),
     so the ring is walkable by ACCIDENT rather than because anything says it is
     a path.

Worked example: Zürich HB way 107612450 is untagged, but it is the `outer` ring
of relation 17459087 = "Passage Sihlquai" (highway=pedestrian, indoor=corridor,
level=-1). Walks between Zürich tracks ride it and were priced as a flat
ground-level hop.

Measured effect (55-transfer EU sample + Zürich, 2026-07-20)
-----------------------------------------------------------
83,901 untagged ways are multipolygon members; 65,495 gain a `highway` and
8,988 gain a `level` (the bit that actually changes routing cost). EU-wide the
sample showed ZERO verdict regressions and zero new failures -- the ways that
gain `highway` were already walkable via the denylist, so walkability does not
widen, it just becomes principled. The benefit is concentrated where indoor
corridors are multipolygons: at Zürich HB, 8->10 went 15.9 s -> 36.5 s (the
vertical legs are finally charged) and 8->6 went from
`exceeded_plausibility_bound` to a plausible 252.6 s / 325.6 m.

Notably this does NOT repeat the ~12% disconnection damage of past walkability
tightening: nothing is removed from the graph, only described more accurately.

Safety
------
Only ways with `tags = '{}'` are touched, so it is idempotent (a second run is a
no-op) and never overwrites real tagging. Every touched way id is recorded in
`multipolygon_tag_propagation`, so `--revert` restores them to untagged without
a full reload. Runs in one transaction: it lands whole or not at all.

Usage:
    .venv/bin/python -m core.dbgen.propagate_multipolygon_tags
    .venv/bin/python -m core.dbgen.propagate_multipolygon_tags --revert
    .venv/bin/python -m core.dbgen.propagate_multipolygon_tags --dry-run
"""
import argparse
import sys
import time

# Reorg bootstrap: this script lives in core/dbgen/ but imports the engine by
# bare name (db/graph/...). Put core/ and its submodule dirs on sys.path so it
# runs both directly and as `python -m core.dbgen.<name>`.
import os as _os
_C = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _p in (_C, _os.path.join(_C, "pathfinding"), _os.path.join(_C, "dbgen"), _os.path.join(_C, "viz")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db import connect  # noqa: E402

# Only tags that affect walkability, cost or vertical placement. Deliberately NOT
# `type` (meaningless on a way) or `name` (not routing-relevant), so a propagated
# way stays minimal and its provenance obvious.
PROPAGATED_KEYS = (
    "highway", "indoor", "level", "railway", "public_transport",
    "conveying", "oneway", "foot", "layer", "area",
)

_RECORD_TABLE = "multipolygon_tag_propagation"

# A way can be a member of more than one multipolygon (rare). DISTINCT ON picks a
# single deterministic parent (lowest relation id) rather than letting the planner
# apply an arbitrary one, so repeated runs on the same data agree.
#
# `only_way_ids` scopes the whole operation to a given set of ways. Production
# passes None (the full table); the tests pass a handful so the suite doesn't
# rewrite 83k rows per assertion.
def _source_sql(only_way_ids):
    scope = "AND m.member_ref = ANY(%s)" if only_way_ids is not None else ""
    return f"""
    SELECT DISTINCT ON (m.member_ref)
           m.member_ref AS way_id,
           COALESCE((
             SELECT jsonb_object_agg(k, v)
             FROM jsonb_each_text(r.tags) AS t(k, v)
             WHERE k = ANY(%s)
           ), '{{}}'::jsonb) AS inherited
    FROM osm_relation_members m
    JOIN osm_relations r ON r.id = m.relation_id
    JOIN osm_ways w ON w.id = m.member_ref
    WHERE m.member_type = 'W'
      AND r.tags->>'type' = 'multipolygon'
      AND w.tags = '{{}}'::jsonb
      {scope}
    ORDER BY m.member_ref, m.relation_id
"""


def _source_params(only_way_ids):
    """Params for _source_sql, in the order the placeholders appear."""
    params = [list(PROPAGATED_KEYS)]
    if only_way_ids is not None:
        params.append(list(only_way_ids))
    return params


def _ensure_record_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_RECORD_TABLE} ("
        "  way_id BIGINT PRIMARY KEY"
        ")"
    )


def count_candidates(cur, only_way_ids=None) -> int:
    """How many untagged ways would gain tags, without changing anything."""
    cur.execute(
        f"SELECT count(*) AS n FROM ({_source_sql(only_way_ids)}) s "
        "WHERE s.inherited <> '{}'::jsonb",
        _source_params(only_way_ids),
    )
    return cur.fetchone()["n"]


def propagate(cur, only_way_ids=None) -> int:
    """Apply the propagation and record what was touched. Returns rows updated.

    Takes a cursor (not a connection) so a caller -- notably the tests -- can run
    it inside a transaction it rolls back, leaving the database untouched."""
    _ensure_record_table(cur)
    cur.execute(
        f"""
        WITH src AS ({_source_sql(only_way_ids)}),
        upd AS (
            UPDATE osm_ways w
            SET tags = src.inherited
            FROM src
            WHERE w.id = src.way_id AND src.inherited <> '{{}}'::jsonb
            RETURNING w.id
        ),
        rec AS (
            INSERT INTO {_RECORD_TABLE} (way_id)
            SELECT id FROM upd ON CONFLICT (way_id) DO NOTHING
        )
        SELECT count(*) AS n FROM upd
        """,
        _source_params(only_way_ids),
    )
    return cur.fetchone()["n"]


def revert(cur, only_way_ids=None) -> int:
    """Restore recorded ways to untagged. Returns rows reverted.

    `only_way_ids` scopes the revert (used by the tests); None reverts everything
    the propagation recorded."""
    _ensure_record_table(cur)
    scope = "AND p.way_id = ANY(%s)" if only_way_ids is not None else ""
    params = [list(only_way_ids)] if only_way_ids is not None else []
    cur.execute(
        f"""
        WITH upd AS (
            UPDATE osm_ways w SET tags = '{{}}'::jsonb
            FROM {_RECORD_TABLE} p WHERE p.way_id = w.id {scope}
            RETURNING w.id
        )
        SELECT count(*) AS n FROM upd
        """,
        params,
    )
    n = cur.fetchone()["n"]
    cur.execute(f"DELETE FROM {_RECORD_TABLE} WHERE TRUE {scope.replace('AND p.way_id', 'AND way_id')}",
                params)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--revert", action="store_true",
                    help="restore previously propagated ways to untagged")
    ap.add_argument("--dry-run", action="store_true",
                    help="report how many ways would change, then exit without writing")
    args = ap.parse_args()

    conn = connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            if args.dry_run:
                n = count_candidates(cur)
                print(f"dry run: {n} untagged multipolygon member ways would gain tags")
                conn.rollback()
                return 0
            t0 = time.monotonic()
            if args.revert:
                n = revert(cur)
                what = "reverted to untagged"
            else:
                n = propagate(cur)
                what = "given their multipolygon relation's walk-relevant tags"
            conn.commit()
            print(f"{n} ways {what} in {time.monotonic() - t0:.1f}s", flush=True)
    except KeyboardInterrupt:
        conn.rollback()
        print("\ninterrupted; rolled back, database unchanged.", file=sys.stderr)
        return 130
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
