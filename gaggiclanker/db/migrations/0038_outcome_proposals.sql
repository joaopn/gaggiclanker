-- A version's outcome proposed by an agent, waiting for the person.
--
-- The loop this archive is built around ends each experiment with a grade, and the grade is
-- what the track record, every later conversation and the "no new change while the outcome is
-- open" block read. Until now it was set by hand on the Set page, so the person had to leave
-- the chat to unblock the agent's next proposal. A row here is the agent's half of that:
-- **the grade as words, changing nothing** until somebody presses Accept. The version's own
-- outcome columns are written only by the person's press (or by the Set page, as before), and
-- nothing that is still `proposed`, `dismissed` or `superseded` is read as an outcome by
-- anything: not the ledger, not the track record, not another version's conversation.
--
--   * One waiting row per version: the partial unique index is the real guard (the stdio
--     child is a second process); writing a newer grade marks the waiting one `superseded`
--     in the same transaction.
--   * `counted_shots` is how many graded (Keep or Improve) shots the version had when the
--     grade was written, so the card can say "on 3 counted shots" and, later, "2 more since".
--   * `changed` means the person recorded a different outcome from the card; what they
--     recorded is `recorded_outcome`. `decision_note` is the short note a dismissal may carry.
--   * The conversation it came from is kept so the Set page can link back to it; deleting the
--     conversation leaves the row.
--
-- No existing data is touched.

CREATE TABLE set_outcome_proposals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id           INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    set_version_id   INTEGER NOT NULL REFERENCES set_versions(id) ON DELETE CASCADE,
    thread_id        INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
    outcome          TEXT    NOT NULL
                     CHECK (outcome IN ('held', 'partly_held', 'failed', 'inconclusive')),
    note             TEXT    NOT NULL,
    counted_shots    INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'proposed'
                     CHECK (status IN ('proposed', 'accepted', 'changed', 'dismissed', 'superseded')),
    recorded_outcome TEXT
                     CHECK (recorded_outcome IS NULL
                            OR recorded_outcome IN ('held', 'partly_held', 'failed', 'inconclusive')),
    decision_note    TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at       TEXT
) STRICT;

CREATE UNIQUE INDEX idx_set_outcome_proposals_waiting
    ON set_outcome_proposals(set_version_id) WHERE status = 'proposed';

CREATE INDEX idx_set_outcome_proposals_set ON set_outcome_proposals(set_id, id DESC);
