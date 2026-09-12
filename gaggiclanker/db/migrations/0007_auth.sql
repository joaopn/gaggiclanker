-- Optional single-user authentication.
--
-- Two tables, and they exist for the same reason: a JWT is a bearer token that
-- the server cannot take back once it is signed. Keeping the signing secret and
-- one row per issued token in the database is what turns "sign out" from a
-- client-side gesture into an actual revocation, and what lets a restored
-- backup keep the sessions it was taken with.

-- Secrets the process generates for itself, as opposed to the ones the operator
-- configures (those live in `settings`). Exactly one row today, `jwt_secret`,
-- written once with insert-if-absent semantics: regenerating it on every boot
-- would sign every open tab out on every container restart, which is how people
-- end up disabling auth.
CREATE TABLE runtime_secrets (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- One row per issued token, keyed by its `jti` claim. The guard looks the row up
-- on every request, so a logout is effective immediately and everywhere rather
-- than "once the token expires" — thirty days later, by default.
--
-- `expires_at` is epoch seconds, matching the JWT `exp` claim it mirrors, so the
-- two cannot drift apart through a formatting difference. Everything else in
-- this schema is ISO-8601 text; this column is the deliberate exception.
CREATE TABLE auth_sessions (
    id         TEXT PRIMARY KEY,
    subject    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at INTEGER NOT NULL,
    revoked_at TEXT,
    -- Purely so the maintainer can tell one signed-in browser from another when
    -- deciding whether to revoke. Truncated by the writer; never trusted.
    user_agent TEXT    NOT NULL DEFAULT ''
) STRICT;

-- The cleanup pass at boot deletes by expiry; the guard reads by primary key.
CREATE INDEX idx_auth_sessions_expires ON auth_sessions(expires_at);
