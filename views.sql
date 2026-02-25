-- Drop all materialized views in reverse order of dependencies
DROP MATERIALIZED VIEW IF EXISTS station_ways_with_nodes CASCADE;
DROP MATERIALIZED VIEW IF EXISTS platform_edges_indexed CASCADE;
DROP MATERIALIZED VIEW IF EXISTS station_platform_nodes CASCADE;
DROP MATERIALIZED VIEW IF EXISTS station_platform_ways CASCADE;
DROP MATERIALIZED VIEW IF EXISTS station_pedestrian_ways CASCADE;
DROP MATERIALIZED VIEW IF EXISTS pedestrian_line_connections CASCADE;
DROP MATERIALIZED VIEW IF EXISTS platform_polygon_to_pedestrian_line CASCADE;

-- View 1: Flatten station relations with their platform ways
CREATE MATERIALIZED VIEW station_platform_ways AS
SELECT 
    r.id AS relation_id,
    r.tags->>'name' AS station_name,
    (jsonb_array_elements(r.members::jsonb) ->> 'ref')::bigint AS way_ref,
    jsonb_array_elements(r.members::jsonb) ->> 'role' AS member_role,
    jsonb_array_elements(r.members::jsonb) ->> 'type' AS member_type
FROM planet_osm_rels r
WHERE r.tags->>'type' = 'public_transport'
    AND r.tags->>'public_transport' = 'stop_area';

CREATE INDEX idx_station_platform_ways_name ON station_platform_ways(station_name);
CREATE INDEX idx_station_platform_ways_rel ON station_platform_ways(relation_id);
CREATE INDEX idx_station_platform_ways_ref ON station_platform_ways(way_ref);

-- View 2: Pre-compute all platform nodes for each station
CREATE MATERIALIZED VIEW station_platform_nodes AS
SELECT 
    spw.relation_id,
    spw.station_name,
    unnest(w.nodes) AS node_id
FROM station_platform_ways spw
JOIN planet_osm_ways w ON w.id = spw.way_ref
WHERE spw.member_role = 'platform' 
    AND spw.member_type = 'W';

CREATE INDEX idx_station_platform_nodes_rel ON station_platform_nodes(relation_id);
CREATE INDEX idx_station_platform_nodes_station ON station_platform_nodes(station_name);
CREATE INDEX idx_station_platform_nodes_node ON station_platform_nodes(node_id);

-- View 3: Platform edges with their station context
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

CREATE INDEX idx_platform_edges_station ON platform_edges_indexed(station_name);
CREATE INDEX idx_platform_edges_ref ON platform_edges_indexed(edge_ref);
CREATE INDEX idx_platform_edges_rel ON platform_edges_indexed(relation_id);
CREATE INDEX idx_platform_edges_nodes ON platform_edges_indexed USING GIN(nodes);

-- View 4: All ways in station relations with their nodes
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

CREATE INDEX idx_station_ways_rel ON station_ways_with_nodes(relation_id);
CREATE INDEX idx_station_ways_station ON station_ways_with_nodes(station_name);
CREATE INDEX idx_station_ways_nodes ON station_ways_with_nodes USING GIN(nodes);

-- -- View: Platform polygons that touch/intersect pedestrian lines (using PostGIS)
-- CREATE MATERIALIZED VIEW platform_polygon_to_pedestrian_line AS
-- SELECT DISTINCT
--     poly.osm_id AS polygon_id,
--     poly.name AS polygon_name,
--     poly.railway AS polygon_railway_type,
--     poly.ref AS polygon_ref,
--     line.osm_id AS line_id,
--     line.name AS line_name,
--     line.highway AS line_highway_type,
--     ST_Intersects(poly.way, line.way) AS intersects,
--     ST_Touches(poly.way, line.way) AS touches
-- FROM planet_osm_polygon poly
-- JOIN planet_osm_line line 
--     ON line.highway IN ('steps', 'footway', 'corridor', 'elevator')
--     AND ST_Intersects(poly.way, line.way)
-- WHERE poly.railway IN ('platform', 'platform_edge');

-- CREATE INDEX idx_platform_line_polygon ON platform_polygon_to_pedestrian_line(polygon_id);
-- CREATE INDEX idx_platform_line_line ON platform_polygon_to_pedestrian_line(line_id);
-- CREATE INDEX idx_platform_line_ref ON platform_polygon_to_pedestrian_line(polygon_ref);


-- -- View: Pedestrian lines that touch/intersect other pedestrian lines
-- CREATE MATERIALIZED VIEW pedestrian_line_connections AS
-- SELECT DISTINCT
--     line1.osm_id AS line1_id,
--     line1.name AS line1_name,
--     line1.highway AS line1_highway_type,
--     line2.osm_id AS line2_id,
--     line2.name AS line2_name,
--     line2.highway AS line2_highway_type,
--     ST_Intersects(line1.way, line2.way) AS intersects,
--     ST_Touches(line1.way, line2.way) AS touches
-- FROM planet_osm_line line1
-- JOIN planet_osm_line line2 
--     ON line2.osm_id > line1.osm_id  -- Avoid duplicates
--     AND line2.highway IN ('steps', 'footway', 'corridor', 'elevator')
--     AND ST_Intersects(line1.way, line2.way)
-- WHERE line1.highway IN ('steps', 'footway', 'corridor', 'elevator');

-- CREATE INDEX idx_ped_line_conn_line1 ON pedestrian_line_connections(line1_id);
-- CREATE INDEX idx_ped_line_conn_line2 ON pedestrian_line_connections(line2_id);
-- CREATE INDEX idx_ped_line_conn_level1 ON pedestrian_line_connections(line1_level);
-- CREATE INDEX idx_ped_line_conn_level2 ON pedestrian_line_connections(line2_level);