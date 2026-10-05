-- What a profile is for, written as expectations a person has confirmed (a signature).
--
-- A signature belongs to **one profile version**. It is a list of expectations, each with a
-- tier (critical, important, context), a phase named by the profile (NULL for the whole shot),
-- a kind (a measure in the metric language, a phase that must begin, a universal warning that
-- is part of the design, or free text for the per-shot reading) and a status. Only a
-- **confirmed** expectation is ever checked, shown as a verdict or told to any agent: a
-- proposed one is a row a person answers, and a rejected one is kept with its reason so the
-- conversation that proposed it can be told. Nothing about the results is stored: a shot's
-- checks are worked out whenever it is read, from its stored facts and these rows.
--
--   * `profile_signatures`: one row per profile version that has any expectation.
--   * `signature_expectations`: `position` is the order they were written in and is never
--     reused, so the unique index on (signature_id, position) is the guard for two writers
--     (the stdio child is a second process). `fault` is the fault word when one word is all
--     it can be (NULL for a measure bounded on both sides, whose word depends on the side it
--     fails on). `carried_from_id` is the expectation of the previous profile version it was
--     carried from; `needs_phase` marks a carried one whose phase no longer exists, which
--     cannot be confirmed until the agent proposes it again with a phase that does.
--   * `set_version_signature_overrides`: one Set version may override the `compare` values of
--     one expectation; proposed and confirmed the same way, and applied only to that
--     version's shots. At most one live (proposed or confirmed) override per Set version; a
--     person may withdraw a confirmed one (`withdrawn`), after which a new one can be proposed.

CREATE TABLE profile_signatures (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_version_id INTEGER NOT NULL UNIQUE REFERENCES profile_versions(id) ON DELETE CASCADE,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE signature_expectations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    signature_id            INTEGER NOT NULL REFERENCES profile_signatures(id) ON DELETE CASCADE,
    position                INTEGER NOT NULL,
    tier                    TEXT    NOT NULL CHECK (tier IN ('critical', 'important', 'context')),
    phase                   TEXT,
    kind                    TEXT    NOT NULL
                            CHECK (kind IN ('measure', 'reached', 'expects_warning', 'free_text')),
    expression_json         TEXT,
    warning_fault           TEXT,
    text                    TEXT    NOT NULL DEFAULT '',
    fault                   TEXT,
    sentence                TEXT    NOT NULL,
    reason                  TEXT    NOT NULL DEFAULT '',
    status                  TEXT    NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    reject_reason           TEXT    NOT NULL DEFAULT '',
    needs_phase             INTEGER NOT NULL DEFAULT 0 CHECK (needs_phase IN (0, 1)),
    proposed_by_thread_id   INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    proposed_by_draft_id    INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL,
    carried_from_id         INTEGER REFERENCES signature_expectations(id) ON DELETE SET NULL,
    proposed_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at             TEXT
) STRICT;

CREATE UNIQUE INDEX idx_signature_expectations_position
    ON signature_expectations(signature_id, position);
CREATE INDEX idx_signature_expectations_thread
    ON signature_expectations(proposed_by_thread_id) WHERE proposed_by_thread_id IS NOT NULL;

CREATE TABLE set_version_signature_overrides (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    set_version_id        INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    expectation_id        INTEGER NOT NULL REFERENCES signature_expectations(id) ON DELETE CASCADE,
    compare_json          TEXT    NOT NULL,
    reason                TEXT    NOT NULL DEFAULT '',
    status                TEXT    NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed', 'confirmed', 'rejected', 'withdrawn')),
    reject_reason         TEXT    NOT NULL DEFAULT '',
    proposed_by_thread_id INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    proposed_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at           TEXT
) STRICT;

CREATE UNIQUE INDEX idx_set_version_signature_overrides_live
    ON set_version_signature_overrides(set_version_id) WHERE status IN ('proposed', 'confirmed');
