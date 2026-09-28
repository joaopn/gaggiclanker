-- Which tier the person put each item of shot information in, where it
-- differs from the catalogue's default.
--
-- A chat is told about a shot in two tiers, base (always shown) and extended
-- (asked for), and an item may be excluded altogether. The items, their
-- meanings and their default tiers live in code
-- (`gaggiclanker/shotinfo/catalogue.py`); Settings -> Shot information lets a
-- person move an item, and this table is where that choice is kept.
--
-- **Overrides only.** An item with no row sits at its default, so an item a
-- later release adds arrives at the default it ships with, and a default a
-- later release changes reaches everybody who never touched that item. Moving
-- an item back to its default deletes its row rather than storing a copy of
-- the default, which would pin it there for good; "reset to defaults" is
-- deleting every row.
--
-- `item_key` is not checked against the catalogue here: a CHECK listing every
-- key would be a second copy of the catalogue to keep in step. The
-- repository's model refuses a key the catalogue does not have on the way in,
-- and a row whose key a later release removed is ignored on the way out.
--
-- No transaction control here: the runner wraps the whole file plus its ledger
-- row in one.

CREATE TABLE shot_info_tiers (
    item_key   TEXT NOT NULL PRIMARY KEY,
    tier       TEXT NOT NULL CHECK (tier IN ('base', 'extended', 'excluded')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
