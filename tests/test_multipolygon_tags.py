"""
Tests for core/dbgen/propagate_multipolygon_tags.py.

OSM puts an indoor corridor's tags on the multipolygon RELATION, leaving the
member ring way untagged -- so our graph reads it as ground level and charges no
vertical cost for crossing it. This propagates the walk-relevant tags down.

Every DB test runs inside a transaction it ROLLS BACK, so the database is never
modified by the suite. Each test also blanks its own fixture ways first, so it
passes whether or not the propagation step has already been run against this
database (otherwise the whole file would go red the moment anyone ran it).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "dbgen"))

from propagate_multipolygon_tags import (  # noqa: E402
    PROPAGATED_KEYS, count_candidates, propagate, revert,
)

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)

# Zürich HB: the `outer` ring of relation 17459087 "Passage Sihlquai"
# (highway=pedestrian, indoor=corridor, level=-1). Untagged as a way in raw OSM.
PASSAGE_RING_WAY = 107612450


def test_propagated_keys_are_walk_relevant_only():
    """`type`/`name` must never propagate: `type=multipolygon` is meaningless on a
    way, and neither affects walkability, cost or level."""
    assert "type" not in PROPAGATED_KEYS and "name" not in PROPAGATED_KEYS
    assert {"highway", "level", "indoor"} <= set(PROPAGATED_KEYS)


@pytest.fixture
def rollback_cur():
    """A cursor whose work is always rolled back."""
    import db  # noqa: E402  -- core/db via the sys.path insert

    conn = db.connect(connect_timeout=5)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        yield cur
    finally:
        conn.rollback()
        conn.close()


def _mp_member_ways(cur, n=25):
    """Member ways of multipolygons that carry a propagatable tag -- regardless of
    whether the way is currently tagged, so the fixture doesn't depend on whether
    the propagation step has already run here."""
    cur.execute("""
        SELECT DISTINCT m.member_ref AS id
        FROM osm_relation_members m
        JOIN osm_relations r ON r.id = m.relation_id
        JOIN osm_ways w ON w.id = m.member_ref
        WHERE m.member_type = 'W' AND r.tags->>'type' = 'multipolygon'
          AND r.tags ? 'highway'
        LIMIT %s
    """, (n,))
    return [r["id"] for r in cur.fetchall()]


def _blank(cur, way_ids):
    """Force ways back to untagged INSIDE the test transaction, reproducing the
    raw-OSM state the propagation is meant to fix. Rolled back with everything else."""
    cur.execute("UPDATE osm_ways SET tags = '{}'::jsonb WHERE id = ANY(%s)", (list(way_ids),))
    cur.execute("DELETE FROM multipolygon_tag_propagation WHERE way_id = ANY(%s)", (list(way_ids),))


@DB
def test_there_are_candidates(rollback_cur):
    cur = rollback_cur
    scope = _mp_member_ways(cur)
    assert scope, "expected multipolygon member ways in this extract"
    _blank(cur, scope)
    assert count_candidates(cur, only_way_ids=scope) > 0


@DB
def test_passage_ring_gains_level_and_highway(rollback_cur):
    """The motivating case: an untagged ring inherits its relation's level, so a
    walk across it can finally be charged the vertical cost."""
    cur = rollback_cur
    cur.execute("SELECT 1 FROM osm_ways WHERE id = %s", (PASSAGE_RING_WAY,))
    if cur.fetchone() is None:
        pytest.skip("Zürich fixture way not present in this extract")
    _blank(cur, [PASSAGE_RING_WAY])

    assert propagate(cur, only_way_ids=[PASSAGE_RING_WAY]) == 1

    cur.execute("SELECT tags FROM osm_ways WHERE id = %s", (PASSAGE_RING_WAY,))
    tags = cur.fetchone()["tags"]
    assert tags.get("level") == "-1"
    assert tags.get("highway") == "pedestrian"
    assert "type" not in tags  # relation-only key must not leak onto the way


@DB
def test_is_idempotent(rollback_cur):
    """A second run is a no-op -- only `tags = '{}'` ways are eligible, and the
    first run leaves none."""
    cur = rollback_cur
    scope = _mp_member_ways(cur)
    _blank(cur, scope)
    first = propagate(cur, only_way_ids=scope)
    assert first > 0
    assert propagate(cur, only_way_ids=scope) == 0
    assert count_candidates(cur, only_way_ids=scope) == 0


@DB
def test_never_overwrites_existing_tags(rollback_cur):
    """A member way that already carries its own tags is left completely alone.
    Excludes anything the propagation itself wrote, so this really is OSM's own
    tagging even on an already-propagated database."""
    cur = rollback_cur
    cur.execute("""
        SELECT w.id, w.tags FROM osm_ways w
        JOIN osm_relation_members m ON m.member_ref = w.id AND m.member_type = 'W'
        JOIN osm_relations r ON r.id = m.relation_id
        WHERE r.tags->>'type' = 'multipolygon' AND w.tags <> '{}'::jsonb
          AND NOT EXISTS (SELECT 1 FROM multipolygon_tag_propagation p WHERE p.way_id = w.id)
        LIMIT 1
    """)
    row = cur.fetchone()
    if row is None:
        pytest.skip("no natively-tagged multipolygon member way in this extract")
    before = row["tags"]

    propagate(cur, only_way_ids=[row["id"]])

    cur.execute("SELECT tags FROM osm_ways WHERE id = %s", (row["id"],))
    assert cur.fetchone()["tags"] == before


@DB
def test_revert_restores_untagged(rollback_cur):
    """--revert undoes the propagation without needing a full reload."""
    cur = rollback_cur
    cur.execute("SELECT 1 FROM osm_ways WHERE id = %s", (PASSAGE_RING_WAY,))
    if cur.fetchone() is None:
        pytest.skip("Zürich fixture way not present in this extract")
    _blank(cur, [PASSAGE_RING_WAY])

    assert propagate(cur, only_way_ids=[PASSAGE_RING_WAY]) == 1
    assert revert(cur, only_way_ids=[PASSAGE_RING_WAY]) == 1

    cur.execute("SELECT tags FROM osm_ways WHERE id = %s", (PASSAGE_RING_WAY,))
    assert cur.fetchone()["tags"] == {}
