-- 0002_archive: the archive itself — machines, profiles, shots, samples, notes,
-- and the sync ledger.
--
-- The machine is a buffer with ~300 KB of heap that deletes its oldest shots
-- once free space drops below 500 KB. This schema is the archive that outlives
-- it, and two decisions follow from that:
--
--   * `shots.raw_slog` is NOT NULL and is the source of truth. Every derived
--     column (phases, diagnostics, score, the sample rows) can be rebuilt from
--     it, so a parser bug fixed next month costs a re-derive rather than a lost
--     shot.
--   * a shot whose bytes do not parse is still stored, with `quarantined = 1`
--     and a reason. It simply produces no sample rows.
--
-- Timestamps are ISO-8601 UTC strings ('YYYY-MM-DDTHH:MM:SS.sssZ'), which sort
-- lexicographically and are what SQLite's own date functions emit. Booleans are
-- INTEGER 0/1 — STRICT tables have no boolean type.
--
-- Everything is STRICT: a REAL column that quietly accepts the string 'n/a' is
-- how an archive rots.

-- ── the machine ──────────────────────────────────────────────────────
--
-- Identity is the configured host, not anything the firmware reports: there is
-- no serial number in `res:ota-settings`, and a display board that is reflashed
-- keeps its address while changing every version string. `capabilities` come
-- from the status state frame (cp/cd/gp/led) and decide which diagnostics mean
-- anything — pressure and flow are a hard zero on a Standard board.
CREATE TABLE machines (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    host                 TEXT    NOT NULL UNIQUE,
    name                 TEXT    NOT NULL DEFAULT '',
    hardware_string      TEXT,
    display_version      TEXT,
    controller_version   TEXT,
    has_pressure         INTEGER NOT NULL DEFAULT 0,
    has_dimming          INTEGER NOT NULL DEFAULT 0,
    has_gear_pump        INTEGER NOT NULL DEFAULT 0,
    has_led              INTEGER NOT NULL DEFAULT 0,
    temperature_offset_c REAL,
    pid                  TEXT,
    brew_delay_ms        INTEGER,
    -- The two device documents verbatim, so a field we have not modelled yet is
    -- still on disk when somebody asks what the machine said.
    identity_json        TEXT,
    settings_json        TEXT,
    notes                TEXT    NOT NULL DEFAULT '',
    first_seen_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- ── profiles ─────────────────────────────────────────────────────────
--
-- A profile version is identified by *what it brews*: the sha256 of
-- `canonical_profile_json`, which drops the fields the firmware rewrites on
-- every load (id, favorite, selected, the implied transition, a phase
-- temperature of 0). Without that, every profile would appear to change the
-- first time the machine touched it.
CREATE TABLE profile_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT    NOT NULL UNIQUE,
    label        TEXT    NOT NULL,
    type         TEXT    NOT NULL,
    utility      INTEGER NOT NULL DEFAULT 0,
    -- The canonical JSON, not the bytes the device sent: two device documents
    -- that brew the same shot must be one row, and this is the form they agree
    -- on. `device_json` keeps the last raw document for reference.
    json         TEXT    NOT NULL,
    device_json  TEXT,
    source       TEXT    NOT NULL DEFAULT 'device'
                 CHECK (source IN ('device', 'draft', 'import')),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- The mirror of `/p/` on the machine: which id currently holds which version.
-- `favorite`, `selected` and `position` live in the display's NVS rather than in
-- the profile file, so they belong here and never in the content hash.
-- Deletion is recorded, not applied: a profile the user removed from the device
-- is still the profile a year of shots was brewed with.
CREATE TABLE device_profiles (
    device_id          TEXT    NOT NULL,
    machine_id         INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    current_version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    favorite           INTEGER NOT NULL DEFAULT 0,
    selected           INTEGER NOT NULL DEFAULT 0,
    position           INTEGER,
    first_seen_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at         TEXT,
    PRIMARY KEY (machine_id, device_id)
) STRICT;

CREATE INDEX idx_device_profiles_version ON device_profiles(current_version_id);

-- ── shots ────────────────────────────────────────────────────────────
--
-- `set_version_id` is the user's answer to "what were you brewing" and arrives
-- with Sets and judgements; it carries no foreign key yet because `set_versions` does
-- not exist. `profile_id_on_device` is the machine's answer, and the two are
-- allowed to disagree — the disagreement is itself a finding.
CREATE TABLE shots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The 6-digit zero-padded form, which is what the URL and the notes file
    -- use. `unpad()` converts when the unpadded int is needed.
    device_id               TEXT    NOT NULL,
    machine_id              INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    set_version_id          INTEGER,

    -- header
    started_at              TEXT,
    start_epoch             INTEGER NOT NULL DEFAULT 0,
    duration_ms             INTEGER NOT NULL DEFAULT 0,
    profile_version_id      INTEGER REFERENCES profile_versions(id),
    profile_id_on_device    TEXT    NOT NULL DEFAULT '',
    profile_name_on_device  TEXT    NOT NULL DEFAULT '',
    final_weight_g          REAL,
    final_exit_reason       INTEGER,
    brew_delay_ms           INTEGER,
    slog_version            INTEGER,
    sample_interval_ms      INTEGER,
    fields_mask             INTEGER,
    sample_count            INTEGER NOT NULL DEFAULT 0,
    scale_connected         INTEGER NOT NULL DEFAULT 0,
    incomplete              INTEGER NOT NULL DEFAULT 0,

    -- the archive's own bookkeeping
    --
    -- `source` says where these bytes came from. 'device' is the sync engine;
    -- 'import' is the JSON importer reading a JSON export of a shot the machine has already
    -- deleted, which is the only way those come back.
    source                  TEXT    NOT NULL DEFAULT 'device'
                            CHECK (source IN ('device', 'import')),
    deleted_on_device       INTEGER NOT NULL DEFAULT 0,
    quarantined             INTEGER NOT NULL DEFAULT 0,
    quarantine_reason       TEXT,
    raw_slog                BLOB    NOT NULL,

    -- derived, and rebuildable from raw_slog
    phases_json             TEXT,
    diagnostics_json        TEXT,
    execution_score         REAL,
    execution_reason        TEXT,

    -- what the device's own index said about this shot, kept so a changed
    -- rating or volume can be spotted without re-reading the file
    index_rating            INTEGER,
    index_volume_g          REAL,
    index_avg_temp_c        REAL,
    index_max_pressure_bar  REAL,
    index_avg_flow_ml_s     REAL,
    index_flags             INTEGER,

    synced_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE (machine_id, device_id)
) STRICT;

-- The list view is "newest first", optionally filtered. NULL started_at (a
-- machine whose clock never synced, epoch < 10000) sorts last under DESC, which
-- is where an undated shot belongs — and the sort key is therefore
-- `COALESCE(started_at,'')`, not `started_at`.
--
-- So this is an **expression** index on exactly that key. A plain index on
-- `started_at` is never used by the list query: the planner cannot see through
-- the COALESCE, and EXPLAIN QUERY PLAN shows SCAN plus "USE TEMP B-TREE FOR
-- ORDER BY" — the whole archive sorted in memory for every page. The trailing
-- `id` makes the keyset cursor's `(key, id) < (?, ?)` a seek rather than a
-- filter.
--
-- A date-range filter (`started_at >= ?`) cannot use it, for the same reason in
-- reverse. That costs a scan of a table with a few thousand rows in it, which
-- is the cheaper half of the trade: the sort runs on every page, the date
-- filter on some of them, and `idx_shots_machine` still covers the common
-- "this machine, in this window" shape.
CREATE INDEX idx_shots_list            ON shots(COALESCE(started_at, '') DESC, id DESC);
CREATE INDEX idx_shots_profile_version ON shots(profile_version_id);
CREATE INDEX idx_shots_quarantined     ON shots(quarantined);
CREATE INDEX idx_shots_machine         ON shots(machine_id, started_at DESC);

-- One row per sample: a chart is one indexed range scan and cross-shot curve
-- analysis is plain SQL. A 30 s shot is ~120 rows.
--
-- WITHOUT ROWID so the primary key *is* the storage order: the rows of one shot
-- are physically adjacent and in t_ms order, which is exactly how they are read.
CREATE TABLE shot_samples (
    shot_id      INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    t_ms         INTEGER NOT NULL,
    tt           REAL,    -- target temperature, °C
    ct           REAL,    -- current temperature, °C
    tp           REAL,    -- target/limit pressure, bar
    cp           REAL,    -- current pressure, bar
    fl           REAL,    -- pump flow, ml/s
    tf           REAL,    -- target/limit flow, ml/s
    pf           REAL,    -- estimated puck flow, ml/s
    vf           REAL,    -- scale flow, g/s
    v            REAL,    -- scale weight, g
    ev           REAL,    -- estimated weight, g
    pr           REAL,    -- puck resistance
    si           INTEGER, -- system-info bitfield
    wp           REAL,    -- cumulative water pumped, ml
    phase_number INTEGER,
    PRIMARY KEY (shot_id, t_ms)
) STRICT, WITHOUT ROWID;

-- ── the device's own notes ───────────────────────────────────────────
--
-- Stored verbatim *and* parsed: the firmware writes every number as a string
-- ("18", "36.5") and will happily store a document we cannot read, so the raw
-- JSON is the record and the typed columns are the convenience.
CREATE TABLE device_shot_notes (
    shot_id          INTEGER PRIMARY KEY REFERENCES shots(id) ON DELETE CASCADE,
    raw_json         TEXT    NOT NULL,
    rating           INTEGER,
    bean_type        TEXT,
    dose_in_g        REAL,
    dose_out_g       REAL,
    ratio            REAL,
    grind_setting    TEXT,
    balance_taste    TEXT,
    notes            TEXT    NOT NULL DEFAULT '',
    device_timestamp INTEGER,
    -- What the index said when these notes were pulled. The device rewrites the
    -- index entry in place when a rating or a dose changes, so a difference
    -- here is the signal to re-pull.
    synced_rating    INTEGER,
    synced_volume_g  REAL,
    fetched_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- ── the sync ledger ──────────────────────────────────────────────────
--
-- One row per pass over the device, so "when did we last hear from the machine
-- and what did it cost" is a query rather than a log grep.
CREATE TABLE sync_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL
                      CHECK (kind IN ('backfill', 'live', 'profiles', 'notes', 'identity')),
    status            TEXT    NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'ok', 'error')),
    trigger           TEXT    NOT NULL DEFAULT '',
    started_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at       TEXT,
    shots_seen        INTEGER NOT NULL DEFAULT 0,
    shots_inserted    INTEGER NOT NULL DEFAULT 0,
    shots_updated     INTEGER NOT NULL DEFAULT 0,
    shots_quarantined INTEGER NOT NULL DEFAULT 0,
    profiles_changed  INTEGER NOT NULL DEFAULT 0,
    notes_synced      INTEGER NOT NULL DEFAULT 0,
    errors            INTEGER NOT NULL DEFAULT 0,
    error             TEXT
) STRICT;

CREATE INDEX idx_sync_runs_kind ON sync_runs(kind, started_at DESC);

-- The UI's feed. Bounded by the engine rather than by the schema: it trims to
-- the newest N after each run, because an unbounded audit table on an appliance
-- is a disk-full incident waiting for a slow week.
CREATE TABLE sync_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER REFERENCES sync_runs(id) ON DELETE CASCADE,
    at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    kind      TEXT    NOT NULL,
    shot_id   INTEGER,
    device_id TEXT,
    message   TEXT    NOT NULL DEFAULT '',
    data_json TEXT
) STRICT;

CREATE INDEX idx_sync_events_run ON sync_events(run_id);
