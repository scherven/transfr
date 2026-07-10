-- ============================================================================
-- Minimal schema for platform-to-platform pathfinding.
--
-- Only three raw OSM tables (nodes/ways/relations) plus one flattened
-- relation-membership table.  No rendering-oriented tables (point/line/
-- polygon/roads) — those were dead weight in the old schema (~16GB unused).
-- No duplicate/overlapping indexes either — the old schema had two ~7.4GB
-- GIN indexes on planet_osm_ways.nodes where one was a strict subset of the
-- other's predicate.
--
-- This is loaded by core/etl.py from a pbf that has ALREADY been scoped to
-- Europe + railway/pedestrian tags (see core/data/README or extract_europe.sh),
-- so there is no need for partial/filtered indexes here — virtually every
-- row in these tables is already relevant.
-- ============================================================================

DROP TABLE IF EXISTS osm_relation_members CASCADE;
DROP TABLE IF EXISTS osm_relations CASCADE;
DROP TABLE IF EXISTS osm_ways CASCADE;
DROP TABLE IF EXISTS osm_nodes CASCADE;

CREATE TABLE osm_nodes (
    id   BIGINT PRIMARY KEY,
    lat  DOUBLE PRECISION NOT NULL,
    lon  DOUBLE PRECISION NOT NULL,
    tags JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE osm_ways (
    id    BIGINT PRIMARY KEY,
    nodes BIGINT[] NOT NULL,
    tags  JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE osm_relations (
    id   BIGINT PRIMARY KEY,
    tags JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- One row per relation member, in order.  Populated directly by the Python
-- loader from each relation's member list — no jsonb_array_elements() or
-- text[] parsing needed at query time like the old station_platform_ways view.
CREATE TABLE osm_relation_members (
    relation_id BIGINT NOT NULL REFERENCES osm_relations(id) ON DELETE CASCADE,
    sequence    INT NOT NULL,
    member_type CHAR(1) NOT NULL,   -- 'N', 'W', or 'R'
    member_ref  BIGINT NOT NULL,
    member_role TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (relation_id, sequence)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- The core adjacency query: "which ways touch any of these nodes".
-- Single GIN index, not scoped by a tag predicate since the whole table is
-- already walkable/railway-relevant.
CREATE INDEX idx_osm_ways_nodes_gin ON osm_ways USING GIN (nodes);

-- Tag lookups (arbitrary containment, e.g. tags @> '{"railway":"platform_edge"}')
CREATE INDEX idx_osm_ways_tags_gin ON osm_ways USING GIN (tags);

-- Hot path: look up a platform edge by station + ref/track_ref.
CREATE INDEX idx_osm_ways_ref
    ON osm_ways ((tags->>'ref'))
    WHERE tags->>'railway' = 'platform_edge';
CREATE INDEX idx_osm_ways_track_ref
    ON osm_ways ((tags->>'railway:track_ref'))
    WHERE tags->>'railway' = 'platform_edge';

CREATE INDEX idx_osm_relations_tags_gin ON osm_relations USING GIN (tags);
CREATE INDEX idx_osm_relations_name ON osm_relations ((tags->>'name'));
CREATE INDEX idx_osm_relations_public_transport ON osm_relations ((tags->>'public_transport'));

-- Reverse lookup ("which relations is this way/node a member of") and
-- forward lookup ("all members of this relation") both need to be fast.
CREATE INDEX idx_relmembers_type_ref ON osm_relation_members (member_type, member_ref);
CREATE INDEX idx_relmembers_relation ON osm_relation_members (relation_id);
