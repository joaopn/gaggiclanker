-- Three switches that let something other than a person write to the machine
-- are retired, and their stored overrides go with them.
--
--   * `mcpDeviceWrites` — the second switch in front of MCP device-write tools.
--     No such tool class exists any more: MCP and the chat read and propose.
--   * `deviceCleanupAuto` — ran the cleanup policy after every index read.
--     A cleanup now runs only when a person confirms its plan.
--   * `notesWritebackEnabled` — sent a judgement to the machine on every save.
--     Notes now go only when a person selects them and sends them.
--
-- Resolution walks the registry, so a row for a key it no longer holds is
-- never read and would be harmless today. It is deleted anyway because each of
-- these rows records somebody's consent to an automatic write, and a stored
-- "true" must not be picked up by any later setting that happens to reuse the
-- name. A restored backup from before this migration has no ledger row for it,
-- so the delete runs again at that boot.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one.

DELETE FROM settings
WHERE key IN ('mcpDeviceWrites', 'deviceCleanupAuto', 'notesWritebackEnabled');
