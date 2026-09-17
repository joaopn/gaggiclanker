-- The bean loses its altitude.
--
-- `beans.altitude_m` was the growing altitude in metres. Almost no bag prints
-- it, so for most beans the field sat empty, and the one place the number was
-- used was a single seeded rule ("beans grown above about 1 800 m are denser")
-- whose advice — a degree or two hotter, a step finer — is exactly the move the
-- roast level and the taste of the cup already lead to. A field that is blank
-- on most rows and changes one sentence of one prompt is form weight, not
-- information.
--
-- The rule goes from the seed at the same time. An archive that already holds
-- it keeps the row (seeding never deletes a row somebody may have edited), and
-- it can no longer be selected: nothing emits the signal it matches on.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

-- One curated view reads the column: `v_beans`, whose latest text is 0015's.
-- SQLite re-parses every view in the schema when a table is altered, so the
-- DROP COLUMN below fails with "error in view v_beans" while it stands. `v_sets`
-- joins `beans` too, but names none of this column, so it survives the re-parse
-- and is left alone.
--
-- The view's text is API: `describe_schema()` reads it out of the schema and the
-- SQL tool's allow-list is exactly these names, so a chat asking about beans
-- sees the change the moment this runs.
DROP VIEW v_beans;

-- No index, no constraint and no generated column mentions it (the only index
-- on `beans` is `(archived, name)`), so the plain form is enough.
ALTER TABLE beans DROP COLUMN altitude_m;

CREATE VIEW v_beans AS
SELECT b.id AS bean_id,
       b.name,
       b.roaster,
       b.origin,
       b.process,
       b.roast_level,
       b.decaf,
       b.description,
       b.notes,
       b.archived,
       b.created_at
  FROM beans b;
