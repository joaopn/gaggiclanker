-- 0043_llm_call_cache: what the cache did on each call, and how big the context was.
--
-- The ledger kept one input figure per call: fresh input plus cache writes plus cache
-- reads, summed over every request the call made. That is what was billed, and it is why a
-- chat answer of ten tool rounds reads as 382k input when the conversation was about 55k.
-- Three nullable columns say the rest:
--
--   * cache_read_tokens / cache_write_tokens: the cached part of input_tokens, summed the
--     same way. OpenAI-compatible servers report a read (`cached_tokens`) and no write.
--   * context_tokens: the last request's whole input, i.e. how big the conversation was when
--     the answer was written.
--
-- NULL means the provider did not say, which is different from 0; rows written before this
-- migration stay NULL. Nothing is backfilled: the figures cannot be recovered from a sum.

ALTER TABLE llm_calls ADD COLUMN cache_read_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN cache_write_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN context_tokens INTEGER;
