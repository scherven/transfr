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

-- Broader platform match: when a station tags its platforms as areas
-- (railway=platform / public_transport=platform, ~10x more common than
-- platform_edge) rather than boardable edges, the transfer resolver falls back
-- to these by ref/local_ref (see core/search_context._find_platform_edges_near).
-- Partial, like the platform_edge indexes above, so they cover only the ~250k
-- platform-area ways, not the whole table.
CREATE INDEX idx_osm_ways_platform_area_ref
    ON osm_ways ((tags->>'ref'))
    WHERE tags->>'railway' = 'platform' OR tags->>'public_transport' = 'platform';
CREATE INDEX idx_osm_ways_platform_area_local_ref
    ON osm_ways ((tags->>'local_ref'))
    WHERE tags->>'railway' = 'platform' OR tags->>'public_transport' = 'platform';

CREATE INDEX idx_osm_relations_tags_gin ON osm_relations USING GIN (tags);
CREATE INDEX idx_osm_relations_name ON osm_relations ((tags->>'name'));
CREATE INDEX idx_osm_relations_public_transport ON osm_relations ((tags->>'public_transport'));

-- Reverse lookup ("which relations is this way/node a member of") and
-- forward lookup ("all members of this relation") both need to be fast.
CREATE INDEX idx_relmembers_type_ref ON osm_relation_members (member_type, member_ref);
CREATE INDEX idx_relmembers_relation ON osm_relation_members (relation_id);

-- ---------------------------------------------------------------------------
-- Materialized node -> way adjacency (built after the main load; see
-- core/build_node_way_ids.py). SearchContext.expand()'s hot-path query,
-- "which ways touch this one node", was a GIN bitmap-heap-scan over the
-- full ~7GB osm_ways table -- correct, but on a cold cache (Postgres
-- shared_buffers can't be raised on this deployment) that scan touches
-- scattered, likely-uncached heap pages every time. A PRIMARY KEY lookup
-- here touches O(1) pages regardless of cache state, then a second lookup
-- fetches only the specific rows from osm_ways by id (also a PK lookup).
-- Two cheap point lookups instead of one expensive scan.
--
-- Staleness: goes stale exactly when osm_ways is re-imported. Rebuild by
-- re-running core/build_node_way_ids.py after any core/etl.py load --
-- it's a single idempotent TRUNCATE + INSERT, not incremental, so there is
-- no partial-staleness state to worry about.
CREATE TABLE IF NOT EXISTS node_way_ids (
    node_id BIGINT PRIMARY KEY,
    way_ids BIGINT[] NOT NULL
);

-- ---------------------------------------------------------------------------
-- Materialized station centroids for coordinate-based station resolution
-- (built by core/build_station_index.py; consumed by api/bridge.py). One row
-- per stop_area/stop_area_group relation: the mean lat/lon of its member
-- geometry. Lets the API map a MOTIS journey stop (which carries lat/lon but a
-- name that does NOT reliably match OSM's) to the OSM relation core/ needs.
--
-- No PostGIS on this deployment (only available, not installed), so nearest-
-- station is a btree bbox prefilter (a few-km box -> a handful of rows) plus
-- an exact haversine in Python -- cheap on ~333k rows, and needs no extension.
--
-- Staleness: rebuild after any osm_relations/osm_ways/osm_nodes reload by
-- re-running core/build_station_index.py (idempotent; --rebuild to TRUNCATE).
CREATE TABLE IF NOT EXISTS station_points (
    relation_id BIGINT PRIMARY KEY,
    name        TEXT,
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    country     TEXT,
    n_members   INT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_station_points_lat ON station_points (lat);
CREATE INDEX IF NOT EXISTS idx_station_points_lon ON station_points (lon);
