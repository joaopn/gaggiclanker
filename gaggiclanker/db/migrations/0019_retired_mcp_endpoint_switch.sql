-- The switch for the MCP endpoint at /mcp is retired, and its stored override
-- goes with it.
--
--   * `mcpEnabled` — served the tool registry over Streamable HTTP to any MCP
--     client holding a token. That transport is gone: the MCP server is the
--     chat's own database tool, spoken over stdio to the child process the
--     claude_code provider spawns, and there is nothing left to switch on.
--
-- Deleted for 0018's reason: the row records somebody's consent to hand the
-- archive to an outside agent, and a stored "true" must not be picked up by any
-- later setting that happens to reuse the name. A restored backup from before
-- this migration has no ledger row for it, so the delete runs again at that
-- boot.
--
-- No transaction control in this file: the runner wraps it plus its ledger row
-- in one.

DELETE FROM settings
WHERE key = 'mcpEnabled';
