-- The machine is only ever written with profiles: the storage cleanup and the notes
-- write-back are gone, and what only they used goes with them.
--
--   * The cleanup ledger. One row per cleanup pass, read only by the Sync page's cleanup
--     history. Every delete it recorded is also a `device_writes` row (kind `shot_delete`)
--     and a `shots.deleted_on_device` flag, so the per-shot record survives; only the
--     per-pass summary is lost, and dropping it is the intent. The index goes with the
--     table, and is named so the statement says what it removes.
--   * The stored values of the four settings that configured those features: the cleanup
--     policy (`deviceCleanupMode`, `deviceCleanupKeepNewest`, `deviceCleanupMinFreeKb`)
--     and the notes card's field list (`notesWritebackFields`). Resolution walks the
--     registry, so a row for a key it no longer holds would never be read; it is deleted
--     anyway so a later setting that reuses a name does not inherit somebody's old choice.
--
-- `device_writes` is left alone, rows and CHECK: an archive's audit of a shot delete or a
-- notes save is history the Sync page still lists, and rebuilding the table to narrow a
-- constraint would either drop those rows or need a second copy of them.
--
-- No transaction control in this file: the runner wraps it plus its ledger row in one.

DROP INDEX IF EXISTS idx_cleanup_runs_started;
DROP TABLE IF EXISTS cleanup_runs;

DELETE FROM settings
WHERE key IN (
    'deviceCleanupMode',
    'deviceCleanupKeepNewest',
    'deviceCleanupMinFreeKb',
    'notesWritebackFields'
);
