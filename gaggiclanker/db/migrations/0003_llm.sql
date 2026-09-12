-- 0003_llm: prompts as editable data, and one row per LLM call.
--
-- Two tables, for two things the LLM layer has to survive a restart with.
--
-- `prompts` makes the prompt text a *setting* rather than a string constant.
-- The files under prompts/ are seeds: on every boot each one is upserted here,
-- which is what lets the maintainer edit a prompt in the UI and have the next
-- call use it without a rebuild — while an image upgrade still delivers new
-- wording to anything untouched. That is the reason for two columns rather
-- than one: `content` is live and belongs to the user, `default_content` is
-- whatever shipped, and "edited" is simply the two disagreeing. Reset is a
-- one-line UPDATE back to the default, which is only possible because the
-- default was kept.
--
-- `llm_calls` is the durable half of the observer. The in-memory ring answers
-- "what is happening now" and is gone at the next restart; this answers "what
-- has this cost me and which model decided that", which is a question asked
-- weeks later. It is written after the call has already succeeded or failed and
-- a failure to write it never fails the call, so nothing here is NOT NULL that
-- a provider might decline to tell us — an unreported token count is NULL, not
-- zero, because zero is a number somebody would add up.

CREATE TABLE prompts (
    name            TEXT PRIMARY KEY,
    -- The live text: what the next call renders. Edited through the API.
    content         TEXT NOT NULL,
    -- What the file on disk said at the last boot. The "reset" target, and
    -- half of the edited test.
    default_content TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE llm_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The observer's record id, so a row here and a line in the live list are
    -- recognisably the same call.
    call_id        TEXT    NOT NULL UNIQUE,
    purpose        TEXT    NOT NULL,
    label          TEXT    NOT NULL DEFAULT '',
    subject        TEXT,
    provider       TEXT    NOT NULL,
    model          TEXT,
    -- Which response mode actually worked. Worth keeping: "this gateway has
    -- silently dropped to text mode" explains a lot of bad output.
    mode           TEXT,
    prompt_name    TEXT,
    prompt_version TEXT,
    -- NULL means the provider reported nothing, which is not the same as zero.
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    duration_ms    INTEGER,
    status         TEXT    NOT NULL CHECK (status IN ('succeeded', 'failed')),
    error          TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- The usage endpoint is always "since a date", and the list is always newest
-- first; one index serves both.
CREATE INDEX idx_llm_calls_created_at ON llm_calls(created_at DESC);
