-- Run this ONCE inside the Nominatim Postgres container after import:
--   docker exec -it nominatim psql -U nominatim -d nominatim -f /sql/build_old_name_index.sql
--
-- Builds a flat lookup table from OSM old_name tags for all highways and places.

DROP TABLE IF EXISTS old_name_index;

CREATE TABLE old_name_index AS
SELECT
    trim(unnest(string_to_array(
        coalesce(
            name->'old_name',
            name->'old_name:ru',
            name->'old_name:kk'
        ), ';'
    ))) AS old_name,
    coalesce(name->'name:ru', name->'name:kk', name->'name') AS current_name,
    ST_Y(ST_Centroid(geometry)) AS lat,
    ST_X(ST_Centroid(geometry)) AS lon,
    class,
    type,
    parent_place_id
FROM placex
WHERE
    (name ? 'old_name' OR name ? 'old_name:ru' OR name ? 'old_name:kk')
    AND class IN ('highway', 'place', 'boundary');

-- Remove empty rows
DELETE FROM old_name_index WHERE old_name IS NULL OR old_name = '';

CREATE INDEX IF NOT EXISTS idx_old_name_lower
    ON old_name_index (lower(old_name));

ANALYZE old_name_index;

SELECT count(*) AS total_old_name_entries FROM old_name_index;
