-- Which flavour-wheel notes the shot panel offers, one list for taste and one
-- for aroma.
--
-- The wheel has 110 notes and nobody wants 110 chips under every shot. A person
-- picks the ten or so they actually reach for on the Taste wheel page, and the
-- panel under a shot row offers those; a note recorded on a shot that is not a
-- pick is still shown there, so it can be removed. Every slug is checked
-- against `gaggiclanker/domain/vocab.py::FLAVOR_NOTES` by the repository's
-- model before it is written: a CHECK listing 110 slugs would be a second copy
-- of the wheel to keep in step.
--
-- A table of its own rather than two settings keys: this is a list the Taste
-- wheel page edits, not configuration, and a settings key would surface on the
-- Settings pages' "Other" card as a JSON string nobody should type.
--
-- `position` is the note's place in the list as shown, which is wheel order
-- (the repository sorts before it writes), so reading the list back is an
-- ORDER BY rather than a sort in every caller.
--
-- No transaction control here: the runner wraps the whole file plus its ledger
-- row in one.

CREATE TABLE flavor_picks (
    kind     TEXT    NOT NULL CHECK (kind IN ('taste', 'aroma')),
    note     TEXT    NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (kind, note)
) STRICT;

-- A useful start, so a fresh archive's panel is not empty: the notes that come
-- up most in espresso, both sides of the cup, in wheel order.
INSERT INTO flavor_picks (kind, note, position) VALUES
    ('aroma', 'floral',                                  0),
    ('aroma', 'fruity',                                  1),
    ('aroma', 'fruity.berry',                            2),
    ('aroma', 'fruity.citrus_fruit',                     3),
    ('aroma', 'roasted',                                 4),
    ('aroma', 'spices',                                  5),
    ('aroma', 'nutty_cocoa.nutty',                       6),
    ('aroma', 'nutty_cocoa.cocoa.chocolate',             7),
    ('aroma', 'sweet.brown_sugar',                       8),
    ('aroma', 'sweet.brown_sugar.caramelized',           9),
    ('taste', 'fruity.berry',                            0),
    ('taste', 'fruity.dried_fruit',                      1),
    ('taste', 'fruity.citrus_fruit',                     2),
    ('taste', 'sour_fermented.alcohol_fermented.winey',  3),
    ('taste', 'other.papery_musty',                      4),
    ('taste', 'other.chemical.bitter',                   5),
    ('taste', 'other.chemical.salty',                    6),
    ('taste', 'nutty_cocoa.nutty',                       7),
    ('taste', 'nutty_cocoa.cocoa.chocolate',             8),
    ('taste', 'nutty_cocoa.cocoa.dark_chocolate',        9),
    ('taste', 'sweet.brown_sugar.caramelized',          10),
    ('taste', 'sweet.brown_sugar.honey',                11);
