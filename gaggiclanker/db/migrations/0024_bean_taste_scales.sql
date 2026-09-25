-- The bean gains three taste scales: acidity, intensity and sweetness.
--
-- Each is the person's reading of the coffee on a scale of 1 to 5, the way
-- many bags print it, or NULL for "not stated". A coffee with nothing on it
-- but a name is still worth recording, so none of them is required, and the
-- prompts leave an unstated one out rather than naming it.
--
-- Columns are added, nothing is rebuilt: `sets.bean_id` references this table.
-- SQLite checks an added column's CHECK against every existing row, and every
-- existing row holds NULL there, which passes.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one, and SQLite's DDL is transactional.

ALTER TABLE beans ADD COLUMN acidity INTEGER CHECK (acidity IS NULL OR acidity BETWEEN 1 AND 5);
ALTER TABLE beans ADD COLUMN intensity INTEGER CHECK (intensity IS NULL OR intensity BETWEEN 1 AND 5);
ALTER TABLE beans ADD COLUMN sweetness INTEGER CHECK (sweetness IS NULL OR sweetness BETWEEN 1 AND 5);

-- `v_beans` names its columns one by one (its latest text is 0017's), so the
-- SQL tool sees the new ones only once the view is recreated with them. The
-- view's text is API: `describe_schema()` reads it out of the schema.
DROP VIEW v_beans;

CREATE VIEW v_beans AS
SELECT b.id AS bean_id,
       b.name,
       b.roaster,
       b.origin,
       b.process,
       b.roast_level,
       b.decaf,
       b.acidity,
       b.intensity,
       b.sweetness,
       b.description,
       b.notes,
       b.archived,
       b.created_at
  FROM beans b;
