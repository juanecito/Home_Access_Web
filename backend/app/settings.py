"""Runtime settings changed by admins, stored in SQLite. Unset keys fall back to the environment config."""
from flask import current_app

from .db import get_db

# setting key -> config key that holds its default
DEFAULTS = {"allow_registration": "ALLOW_REGISTRATION", "ssh_enabled": "SSH_ENABLED"}


def get_bool(key):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return bool(current_app.config[DEFAULTS[key]]) if row is None else row["value"] == "1"


def set_bool(key, value):
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, "1" if value else "0"))
    db.commit()


def allow_registration():
    return get_bool("allow_registration")


def ssh_enabled():
    return get_bool("ssh_enabled")
