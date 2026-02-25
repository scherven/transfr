-- ==========================================================================
-- Materialized views for OpenRailwayMap pathfinding.
--
-- Dependency chain (create in order, drop in reverse):
--   station_platform_ways
--     -> station_platform_nodes
--       -> platform_edges_indexed
--     -> station_ways_with_nodes
--       -> station_pedestrian_ways_with_nodes  (regular view)
--       -> station_ways_with_nodes_plus_pedestrian
--         -> station_way_segments              (regular view)
--
-- After importing new OSM data, refresh materialized views:
--   REFRESH MATERIALIZED VIEW station_platform_ways;
--   REFRESH MATERIALIZED VIEW station_platform_nodes;
--   REFRESH MATERIALIZED VIEW platform_edges_indexed;
--   REFRESH MATERIALIZED VIEW station_ways_with_nodes;
--   REFRESH MATERIALIZED VIEW station_ways_with_nodes_plus_pedestrian;
-- ==========================================================================

-- Drop in reverse dependency order
DROP VIEW IF EXISTS station_way_segments CASCADE;
DROP MATERIALIZED VIEW IF EXISTS station_ways_with_nodes_plus_pedestrian CASCADE;
DROP VIEW IF EXISTS station_pedestrian_ways_with_nodes CASCADE;
DROP MATERIALIZED VIEW IF EXISTS station_ways_with_nodes CASCADE;
DROP MATERIALIZED VIEW IF EXISTS platform_edges_indexed CASCADE;
DROP MATERIALIZED VIEW IF EXISTS station_platform_nodes CASCADE;
DROP MATERIALIZED VIEW IF EXISTS station_platform_ways CASCADE;

-- ---------------------------------------------------------------------------
-- 1. station_platform_ways
--
-- Flattens every public_transport=stop_area relation into one row per member.
-- Each row records (relation_id, station_name, way_ref, member_role, member_type).
-- This is the root of the dependency chain: everything else joins through it.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW station_platform_ways AS
SELECT
    r.id AS relation_id,
    r.tags->>'name' AS station_name,
    (jsonb_array_elements(r.members::jsonb) ->> 'ref')::bigint AS way_ref,
    jsonb_array_elements(r.members::jsonb) ->> 'role'  AS member_role,
    jsonb_array_elements(r.members::jsonb) ->> 'type'  AS member_type
FROM planet_osm_rels r
WHERE r.tags->>'type' = 'public_transport'
  AND r.tags->>'public_transport' = 'stop_area';

CREATE INDEX idx_spw_name ON station_platform_ways (station_name);
CREATE INDEX idx_spw_rel  ON station_platform_ways (relation_id);
CREATE INDEX idx_spw_ref  ON station_platform_ways (way_ref);

-- ---------------------------------------------------------------------------
-- 2. station_platform_nodes
--
-- Every individual OSM node that belongs to a platform way inside a station.
-- Used to associate platform_edge ways (which are NOT relation members) with
-- their station by checking node overlap.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW station_platform_nodes AS
SELECT
    spw.relation_id,
    spw.station_name,
    unnest(w.nodes) AS node_id
FROM station_platform_ways spw
JOIN planet_osm_ways w ON w.id = spw.way_ref
WHERE spw.member_role = 'platform'
  AND spw.member_type = 'W';

CREATE INDEX idx_spn_rel     ON station_platform_nodes (relation_id);
CREATE INDEX idx_spn_station ON station_platform_nodes (station_name);
CREATE INDEX idx_spn_node    ON station_platform_nodes (node_id);

-- ---------------------------------------------------------------------------
-- 3. platform_edges_indexed
--
-- All ways tagged railway=platform_edge, enriched with the station context
-- (relation_id, station_name) they belong to.  A platform edge is linked to
-- a station when it shares at least one node with that station's platforms.
-- Queried by station_name + edge_ref to look up specific edges.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW platform_edges_indexed AS
SELECT
    spn.relation_id,
    spn.station_name,
    w.id AS way_id,
    w.nodes,
    w.tags,
    w.tags->>'ref' AS edge_ref
FROM planet_osm_ways w
JOIN LATERAL (
    SELECT DISTINCT relation_id, station_name
    FROM station_platform_nodes spn
    WHERE spn.node_id = ANY(w.nodes)
) spn ON true
WHERE w.tags->>'railway' = 'platform_edge';

CREATE INDEX idx_pe_station ON platform_edges_indexed (station_name);
CREATE INDEX idx_pe_ref     ON platform_edges_indexed (edge_ref);
CREATE INDEX idx_pe_rel     ON platform_edges_indexed (relation_id);
CREATE INDEX idx_pe_nodes   ON platform_edges_indexed USING GIN (nodes);

-- ---------------------------------------------------------------------------
-- 4. station_ways_with_nodes
--
-- Every way that is a direct member (type='W') of a stop_area relation,
-- together with its full node array and tags.  This includes platforms,
-- footways, rail lines, etc. — anything the relation references.
-- Used for the "opposite platform" check (shared nodes between two edges)
-- and as the seed set for pathfinding.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW station_ways_with_nodes AS
SELECT
    spw.relation_id,
    spw.station_name,
    w.id AS way_id,
    w.nodes,
    w.tags
FROM station_platform_ways spw
JOIN planet_osm_ways w ON w.id = spw.way_ref
WHERE spw.member_type = 'W';

CREATE INDEX idx_swn_rel     ON station_ways_with_nodes (relation_id);
CREATE INDEX idx_swn_station ON station_ways_with_nodes (station_name);
CREATE INDEX idx_swn_nodes   ON station_ways_with_nodes USING GIN (nodes);

-- ---------------------------------------------------------------------------
-- 5. station_pedestrian_ways_with_nodes  (regular view — not materialized)
--
-- Pedestrian ways that are NOT members of a stop_area relation but DO share
-- at least one node with a station-member way.  This gives us the first
-- "hop" of footpaths connecting to the station.  Further hops are discovered
-- at query time by the Python batch-expansion logic.
-- ---------------------------------------------------------------------------
CREATE VIEW station_pedestrian_ways_with_nodes AS
SELECT DISTINCT
    s.relation_id,
    s.station_name,
    w.id AS way_id,
    w.nodes,
    w.tags
FROM planet_osm_ways w
JOIN station_ways_with_nodes s ON w.nodes && s.nodes
WHERE w.tags->>'highway' IN (
    'footway', 'steps', 'corridor', 'pedestrian',
    'path', 'cycleway', 'crossing'
);

-- ---------------------------------------------------------------------------
-- 6. station_ways_with_nodes_plus_pedestrian  (materialized)
--
-- Union of relation-member ways and the first-hop pedestrian ways above.
-- Materialized with an index on relation_id so that loading segments for a
-- single station is a fast index scan.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW station_ways_with_nodes_plus_pedestrian AS
SELECT relation_id, station_name, way_id, nodes, tags
FROM station_ways_with_nodes
UNION
SELECT relation_id, station_name, way_id, nodes, tags
FROM station_pedestrian_ways_with_nodes;

CREATE INDEX idx_swpp_rel ON station_ways_with_nodes_plus_pedestrian (relation_id);

-- ---------------------------------------------------------------------------
-- 7. station_way_segments  (regular view)
--
-- Expands every way in the union above into consecutive (node_from, node_to)
-- pairs.  This is what pathfind.py queries to build the in-memory bipartite
-- graph for BFS.  It is a view (not materialized) because it reads from the
-- materialized station_ways_with_nodes_plus_pedestrian and the per-relation
-- filter pushes down efficiently.
-- ---------------------------------------------------------------------------
CREATE VIEW station_way_segments AS
WITH ordered AS (
    SELECT
        s.relation_id,
        s.station_name,
        s.way_id,
        t.node_id,
        t.ord
    FROM station_ways_with_nodes_plus_pedestrian s,
         unnest(s.nodes) WITH ORDINALITY AS t(node_id, ord)
),
with_next AS (
    SELECT
        relation_id,
        station_name,
        way_id,
        node_id AS node_from,
        lead(node_id) OVER (PARTITION BY way_id ORDER BY ord) AS node_to
    FROM ordered
)
SELECT relation_id, station_name, way_id, node_from, node_to
FROM with_next
WHERE node_to IS NOT NULL;
