import os
import sqlite3

from flask import current_app, g


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    path = current_app.config["DATABASE"]
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = get_db()
    with current_app.open_resource("schema.sql") as f:
        conn.executescript(f.read().decode("utf-8"))
    _migrate(conn)


def _migrate(conn):
    """Bring databases created by older versions up to the current schema."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "auth_source" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local' "
                     "CHECK (auth_source IN ('local', 'pam'))")
    if "avatar" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN avatar BLOB")
    if "type_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(items)")}:
        conn.execute("ALTER TABLE items ADD COLUMN type_id INTEGER REFERENCES item_types(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_type ON items(type_id)")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(refresh_tokens)")}
    if "session_id" not in columns:
        conn.execute("ALTER TABLE refresh_tokens ADD COLUMN session_id TEXT")
    if "rotated_at" not in columns:
        conn.execute("ALTER TABLE refresh_tokens ADD COLUMN rotated_at INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_session ON refresh_tokens(session_id)")
    conn.commit()


def row_to_dict(row):
    return dict(row) if row is not None else None
