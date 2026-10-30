PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    -- 'local': password stored here; 'pam': system account, password_hash unused.
    auth_source   TEXT    NOT NULL DEFAULT 'local' CHECK (auth_source IN ('local', 'pam')),
    avatar        BLOB,   -- 60x60 PNG or JPEG chosen by the user; NULL = none
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- One row per issued refresh token, so tokens can be rotated and revoked. All tokens of one
-- sign-in share a session_id, which access tokens carry as `sid`: revoking the session ends
-- them at once. A bearer token from /api/auth/token gets a row too (never sent as a refresh token).
CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti        TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT,
    expires_at INTEGER NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,  -- 0 active, 1 revoked, 2 rotated (replaced by a newer token)
    rotated_at INTEGER                      -- unix time of the rotation, for the reuse grace period
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);

-- Failed login attempts, used for brute-force throttling (shared by all workers).
CREATE TABLE IF NOT EXISTS login_failures (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT    NOT NULL,
    ts  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures_key ON login_failures(key, ts);

CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    done        INTEGER NOT NULL DEFAULT 0,
    type_id     INTEGER REFERENCES item_types(id),  -- NULL = plain item without extra fields
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_items_owner ON items(owner_id);

-- User-defined item types. Each type stores its fields' values in its own table
-- item_type_<id> (one row per item, keyed by item_id); see app/item_types.py.
CREATE TABLE IF NOT EXISTS item_types (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS item_type_fields (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id       INTEGER NOT NULL REFERENCES item_types(id) ON DELETE CASCADE,
    name          TEXT    NOT NULL,  -- also the column name in the type's table
    field_type    TEXT    NOT NULL CHECK (field_type IN ('int', 'float', 'boolean', 'string')),
    default_value TEXT    NOT NULL,  -- JSON
    position      INTEGER NOT NULL,
    UNIQUE (type_id, name)
);

-- Saved SSH connections of each user (SSH menu), opened by homeacces-ssh. The private key is
-- never sent back to the browser: the API only reports whether one is set.
CREATE TABLE IF NOT EXISTS ssh_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    host        TEXT    NOT NULL,
    port        INTEGER NOT NULL DEFAULT 22,
    username    TEXT    NOT NULL,
    private_key TEXT,   -- OpenSSH/PEM private key; NULL = password or keyboard-interactive login
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (owner_id, name)
);

-- Settings changed by admins at runtime. A missing key means the default from the environment.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Audit trail of access and activity (also appended to HOMEACCES_ACTIVITY_LOG).
-- user_id is the acting user; the username is kept so entries survive the user's deletion.
CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username   TEXT,
    event      TEXT    NOT NULL,
    success    INTEGER NOT NULL DEFAULT 1,
    ip         TEXT,
    user_agent TEXT,
    details    TEXT    -- JSON object
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(ts);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_event ON activity_log(event);
