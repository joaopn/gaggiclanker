-- 0001_init: the settings table.
--
-- Runtime overrides for the keys declared in gaggiclanker/settings.py. One row
-- per overridden key; a key with no row falls back to its environment variable
-- and then to the registry default. Values are TEXT for every type because the
-- registry owns the parsing — storing them typed would put the same coercion
-- rules in two places.
--
-- The domain schema (shots, samples, profiles, sets, judgements, analyses)
-- arrives with the sync engine; this migration deliberately creates nothing it does not yet
-- use, so the first shot-storage migration can be written against real code.

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
