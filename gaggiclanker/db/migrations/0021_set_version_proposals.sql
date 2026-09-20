-- A change the agent wants to make is a proposal, not a version.
--
-- Until now the chat's `propose_set_version` appended a version the moment the
-- model called it: the next shot was filed under a recipe nobody had agreed to,
-- with no prediction attached and whether or not the last prediction had ever
-- been graded. The loop this archive is built around is the opposite one. The
-- agent proposes **one** change with a falsifiable prediction, the person
-- accepts or declines it, and nothing new is proposed while the current
-- prediction is still ungraded.
--
-- So the proposal gets a table of its own, and the important word is "waiting":
-- a row here has changed nothing. It becomes a `set_versions` row only when
-- somebody presses Accept, and Accept is a route a person reaches, never a
-- tool. That is the same rule as the rest of this box — the chat reads and
-- proposes, the person decides — applied to the one thing the chat used to be
-- able to do on its own.
--
-- What each column is for:
--
--   * `thread_id` — the room the change was argued in, so an accepted version
--     can link back to the reasoning. It must be a conversation of this Set,
--     which the repository checks: a proposal pointing at somebody else's room
--     would send a reader to a transcript that says nothing about it.
--     `ON DELETE SET NULL`, unlike the chat's own cascades: old conversations
--     are disposable and the experiment log is not, so a proposal outlives the
--     chat that made it.
--   * `base_version_id` — what was current when the proposal was made. Accept
--     compares it with what is current *then*: a Set that moved on in between
--     is a proposal made against a recipe nobody is brewing any more, and it is
--     refused rather than applied to whatever happens to be current.
--   * `patch_json` — the change itself, as the recipe fields that move, in
--     exactly the shape `SetsRepository.add_version` takes. Validated by that
--     model on the way in and on the way out, so a hand-edited row is a
--     refusal at read time rather than a surprise inside the accept.
--   * `prediction` — NOT NULL, and never empty: the write model refuses an
--     empty one and the tool refuses a short one. That is the whole point of
--     the table.
--   * `compares_to_version_id` — which version the prediction is measured
--     against. Usually the base version; NULL means "nothing", and then the
--     prediction is graded on the numbers the new version itself states.
--   * `combined_reason` — why two changes had to move together, when the agent
--     insisted on two. Empty for the ordinary one-change proposal.
--   * `resulting_version_id` — the version Accept created, so the log entry and
--     the proposal point at each other.
--
-- No foreign key from `set_versions` back to here: the version is the record
-- and it must not depend on the proposal that suggested it.
CREATE TABLE set_version_proposals (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id                 INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    thread_id              INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    base_version_id        INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    patch_json             TEXT    NOT NULL DEFAULT '{}',
    reason                 TEXT    NOT NULL DEFAULT '',
    prediction             TEXT    NOT NULL DEFAULT '',
    compares_to_version_id INTEGER REFERENCES set_versions(id) ON DELETE SET NULL,
    combined_reason        TEXT    NOT NULL DEFAULT '',
    -- `stale` is the one nobody chooses: a version appended to this Set by any
    -- other path retires whatever was waiting, because a change argued against
    -- a recipe that is no longer current is not a question anybody can answer.
    status                 TEXT    NOT NULL DEFAULT 'proposed'
                           CHECK (status IN ('proposed', 'accepted', 'declined', 'stale')),
    -- What the person said when they turned it down. The agent is told about it
    -- in the next conversation, because "not that, I have tried it" is the most
    -- useful sentence a person can write here.
    decline_note           TEXT    NOT NULL DEFAULT '',
    resulting_version_id   INTEGER REFERENCES set_versions(id) ON DELETE SET NULL,
    created_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at             TEXT
) STRICT;

-- One waiting proposal per Set, enforced here rather than by whoever remembers
-- to look first. A second proposal while one is waiting is the agent talking
-- past the person: the answer is to discuss the one on screen, which is what
-- the tool's refusal says.
CREATE UNIQUE INDEX idx_set_version_proposals_waiting
    ON set_version_proposals(set_id) WHERE status = 'proposed';

-- The Set page's list, newest first.
CREATE INDEX idx_set_version_proposals_set ON set_version_proposals(set_id, id DESC);
