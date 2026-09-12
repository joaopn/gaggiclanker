-- 0004_llm_call_text: keep what was asked and what came back.
--
-- 0003 recorded that a call happened and what it cost. That answers "why is the
-- bill what it is" and nothing else. The question the analyzer will actually ask is "why
-- did the analysis say that", and neither the token count nor the error message
-- can answer it — the prompt is editable (see the `prompts` table), so the text
-- that produced a stored analysis is not reconstructable from the prompt file
-- three edits later.
--
-- Both columns are nullable and both are optional at runtime: the
-- `llmStoreCallText` setting turns them off for anyone who would rather not
-- keep the text, and a row written before this migration has neither. The
-- service caps each at 200 KB, because a shot's samples rendered into a prompt
-- is tens of kilobytes and an accidental loop is megabytes.
--
-- A new file rather than an edit to 0003: a shipped migration is immutable
-- and a checksum that changed under an install that has already
-- applied it is a hard error at boot.

ALTER TABLE llm_calls ADD COLUMN input_text TEXT;
ALTER TABLE llm_calls ADD COLUMN output_text TEXT;
