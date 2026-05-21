-- Export street list for a given city to use in fuzzy matching.
-- Run for each city after nominatim import:
--   docker exec -it nominatim psql -U nominatim -d nominatim \
--     -c "\copy (SELECT ...) TO STDOUT CSV HEADER" > /tmp/astana_streets.csv
--
-- Or use the export_streets.py script which calls this automatically.

SELECT
    coalesce(p.name->'name:ru', p.name->'name:kk', p.name->'name') AS name,
    ST_Y(ST_Centroid(p.geometry)) AS lat,
    ST_X(ST_Centroid(p.geometry)) AS lon
FROM placex p
WHERE
    p.class = 'highway'
    AND p.type IN ('residential', 'primary', 'secondary', 'tertiary',
                   'unclassified', 'trunk', 'motorway', 'pedestrian',
                   'living_street', 'service', 'path', 'footway')
    AND (p.name->'name:ru' IS NOT NULL OR p.name->'name' IS NOT NULL)
    AND ST_DWithin(
        ST_Centroid(p.geometry)::geography,
        ST_MakePoint(71.4282, 51.1801)::geography,  -- Астана centroid
        60000  -- 60 km radius
    )
ORDER BY name;
