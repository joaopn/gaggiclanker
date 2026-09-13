-- A bean is a type of coffee, not an individual bag.
--
-- `beans.roast_date` was the one field on the row that described a *bag* rather
-- than the coffee: the roaster, the origin, the process and the roast level are
-- true of every bag of that coffee ever bought, and the date is true of one of
-- them. Recording it on the type meant that re-buying a bag either silently
-- aged the old row or forced a duplicate bean, and every consumer of the number
-- — days off roast in the analysis prompt, the freshness rules, the pill on two
-- pages — was then reasoning from whichever of the two had happened.
--
-- Bag ageing is not tracked here at all now. It is a real thing about coffee and
-- it may come back as its own row with its own dates; what it is not is an
-- attribute of the type, and advice derived from a date nobody maintains is
-- worse than no advice.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

-- Two of the curated views read the column: `v_beans` (0013) and `v_sets`
-- (re-created verbatim by 0014, which is where their latest text lives).
-- SQLite re-parses every view in the schema when a table is altered, so the
-- DROP COLUMN below fails with "error in view v_beans" while they stand. They
-- are dropped here and re-created underneath without the column.
--
-- Their text is API: `describe_schema()` reads it out of the schema and the SQL
-- tool's allow-list is exactly these names, so a chat asking about beans sees
-- the change the moment this runs.
DROP VIEW v_beans;
DROP VIEW v_sets;

-- No index, no constraint and no generated column mentions it, so the plain
-- form is enough — unlike 0014's rebuild, nothing here references `beans`
-- through this column.
ALTER TABLE beans DROP COLUMN roast_date;

CREATE VIEW v_beans AS
SELECT b.id AS bean_id,
       b.name,
       b.roaster,
       b.origin,
       b.variety,
       b.altitude_m,
       b.process,
       b.roast_level,
       b.decaf,
       b.tasting_notes_bag,
       b.notes,
       b.archived,
       b.created_at
  FROM beans b;

CREATE VIEW v_sets AS
SELECT st.id AS set_id,
       st.name,
       st.status,
       st.active,
       st.machine_id,
       st.bean_id,
       b.name AS bean_name,
       b.roaster,
       b.roast_level,
       b.process,
       b.origin,
       st.grinder_id,
       g.name AS grinder_name,
       g.step_unit AS grinder_step_unit,
       st.created_at,
       (SELECT COUNT(*) FROM set_versions v WHERE v.set_id = st.id) AS version_count,
       (SELECT COUNT(*) FROM shots s
          JOIN set_versions v ON v.id = s.set_version_id
         WHERE v.set_id = st.id) AS shot_count
  FROM sets st
  LEFT JOIN beans b    ON b.id = st.bean_id
  LEFT JOIN grinders g ON g.id = st.grinder_id;
