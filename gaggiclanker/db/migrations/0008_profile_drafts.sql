-- Profile drafts, and the audit trail of every byte we sent the machine.
--
-- Up to here gaggiclanker has written *nothing* to the device: the client's
-- public surface was ten reads and a test failed the build if an eleventh
-- appeared. This migration is the storage half of removing that restriction for
-- one narrow case — turning an analysis's `profile_patch`, or a hand edit, into
-- a new `[AI]` profile on the machine.
--
-- Three things get a table or a column, and each answers a different question:
--
--   * `device_writes` — *what did this box ever send the machine?* One row per
--     attempted write, including the ones that were refused. It is the only
--     record that survives a wedged display, and it is what `delete_profile`
--     consults before it will delete anything: we delete our own profiles and
--     nobody else's.
--   * `profile_drafts` — *what is proposed, and how far has it got?* The state
--     machine draft -> approved -> pushed | failed, plus the two side channels
--     (discarded by a person, superseded by a refinement).
--   * `set_versions.pushed_device_profile_id` — *which profile on the machine
--     does this recipe mean?* A Set version already names a `profile_version_id`
--     (what it brews); after a push it can also name the device id the firmware
--     assigned (where it lives), which is what makes "select the profile this
--     Set wants" answerable later.
--
-- Everything is STRICT and every timestamp is the same ISO-8601 UTC string the
-- rest of the schema uses, so ORDER BY on a text column is chronological.

-- ── the write audit ──────────────────────────────────────────────────
--
-- Written by the device client itself, through a gate object, on **every**
-- attempt: authorised or refused, succeeded or failed. A refusal row is the
-- valuable one — "somebody tried to push with writes disabled" is a thing the
-- Device page should be able to show, and an audit that only records successes
-- is an audit that cannot answer the question anybody asks it.
--
-- `payload_hash` is the sha256 of the canonical profile JSON for a save, and of
-- the target id for everything else. The document itself is not stored here:
-- for a push it is already on the draft row, and for a delete or a select there
-- is no document. What this column is for is proving that the thing we read
-- back is the thing we sent.
--
-- `device_id` is the profile id on the machine, not the machine — a save has
-- none until the firmware answers, so it is NULL for the attempt and filled in
-- by the row the result writes. `host` says which machine, because a person may
-- point this box at a second one and the audit has to stay legible when they do.
--
-- No foreign keys. This table outlives the rows it talks about on purpose: a
-- profile we pushed and then deleted from the display must still appear here,
-- and a `device_profiles` row cascading a delete into the audit would erase the
-- evidence of the write that created it.
CREATE TABLE device_writes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL CHECK (kind IN
                   ('profile_save', 'profile_delete', 'profile_select',
                    'profile_favorite', 'profile_unfavorite')),
    host         TEXT    NOT NULL DEFAULT '',
    device_id    TEXT,
    payload_hash TEXT    NOT NULL DEFAULT '',
    -- `refused` never reached the wire; `failed` did and the machine said no or
    -- did not answer. Keeping them apart is the difference between "the switch
    -- is off" and "the machine is unhappy", which are fixed in different places.
    result       TEXT    NOT NULL CHECK (result IN ('ok', 'refused', 'failed')),
    error        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- The Device page's list, newest first.
CREATE INDEX idx_device_writes_created ON device_writes(created_at DESC, id DESC);
-- The provenance check `delete_profile` runs: "is there an `ok` `profile_save`
-- for this id on this host?". Keyed so that question is an index seek rather
-- than a scan of every write this box has ever made.
CREATE INDEX idx_device_writes_provenance ON device_writes(device_id, kind, result);

-- ── profile drafts ───────────────────────────────────────────────────
--
-- One row per proposed profile. The document itself is **not** here: it is a
-- `profile_versions` row with `source = 'draft'`, exactly like a mirrored or an
-- imported profile, and `draft_version_id` points at it. That is deliberate.
-- Profile versions are content-hashed and immutable, shots resolve to them, and
-- a draft that becomes the profile a Set is brewed with has to be the same kind
-- of thing as every other profile in the archive — not a JSON blob in a
-- different table that a later join has to special-case.
--
-- The document stored under `draft_version_id` is byte-for-byte what gets sent,
-- label suffix included. The suffix is applied when the draft is built rather
-- than at push time so that the thing a person approves, the thing that goes on
-- the wire and the thing the round trip compares against are one document.
--
-- `base_version_id` is what it was derived from, and it is what the diff renders
-- against. It is never NULL: a draft with nothing to compare to is a new profile
-- somebody typed, and that is an import, not a draft.
--
-- `source_analysis_id` / `source_suggestion_id` are the provenance. Both NULL
-- means a manual edit. No foreign key on either, for the same reason
-- `shot_analyses.set_version_id` has none: re-running an analysis must not be
-- able to cascade away a draft somebody is halfway through approving.
--
-- `stop_condition_changes_json` is the array of per-phase target changes
-- computed at draft time (`gaggiclanker/domain/profile_policy.py`). It is stored
-- rather than recomputed because the approval is *about it*: approving a draft
-- whose stop conditions moved requires an explicit acknowledgement, and the list
-- that was acknowledged has to be the list that was shown.
--
-- `clamp_changes_json` is the other half of the same story — every number the
-- safety policy moved on the way in, with its before and after. Not in the
-- original deliverable list; added because a draft that was silently clamped and
-- a draft that needed no clamping look identical without it, and the whole point
-- of clamping-with-a-list is that nothing is silent.
--
-- `verification_json` holds both documents when the round trip disagreed: what
-- we sent and what the machine served back. It is the evidence for the one
-- failure mode nobody can debug from a log line — "the machine says it saved it"
-- is not the same as "the machine stored what we sent".
CREATE TABLE profile_drafts (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    base_version_id          INTEGER NOT NULL REFERENCES profile_versions(id),
    draft_version_id         INTEGER REFERENCES profile_versions(id),
    source_analysis_id       INTEGER,
    source_suggestion_id     INTEGER,
    -- The draft this one refines. A refinement supersedes its parent rather
    -- than editing it, so the advice that produced each attempt stays readable.
    parent_draft_id          INTEGER REFERENCES profile_drafts(id),
    change_summary           TEXT    NOT NULL DEFAULT '',
    stop_condition_changes_json TEXT NOT NULL DEFAULT '[]',
    clamp_changes_json       TEXT    NOT NULL DEFAULT '[]',
    -- What the barista asked for, in their words. Carried into the next
    -- refinement's prompt, which is the whole reason a refinement is a new
    -- draft rather than a re-run.
    notes                    TEXT    NOT NULL DEFAULT '',
    status                   TEXT    NOT NULL DEFAULT 'draft'
                             CHECK (status IN ('draft', 'approved', 'pushed',
                                               'failed', 'discarded', 'superseded')),
    -- Set by the approval, and read by the push: a draft whose stop conditions
    -- moved cannot be pushed without somebody having said so.
    acknowledged_stop_changes INTEGER NOT NULL DEFAULT 0,
    pushed_device_profile_id TEXT,
    verification_json        TEXT,
    error                    TEXT,
    created_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- The queue: everything still open, newest first.
CREATE INDEX idx_profile_drafts_status ON profile_drafts(status, id DESC);
-- "What has been drafted from this profile" — the Profiles page's per-version
-- badge, and the check a second draft from the same analysis runs.
CREATE INDEX idx_profile_drafts_base ON profile_drafts(base_version_id, id DESC);

-- ── the Set version's link to the machine ────────────────────────────
--
-- `profile_version_id` says what a Set version brews. This says which file on
-- the display holds it, once a draft has been pushed. Nullable and unconstrained
-- because a device id is the machine's to own: it is gone the moment somebody
-- deletes the profile from the display, and a foreign key to a table of
-- tombstones would be a promise this schema cannot keep.
ALTER TABLE set_versions ADD COLUMN pushed_device_profile_id TEXT;
