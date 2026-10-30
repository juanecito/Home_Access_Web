import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bool(name, default):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("HOMEACCES_SECRET_KEY")
    DATABASE = os.environ.get("HOMEACCES_DATABASE", os.path.join(BASE_DIR, "instance", "homeacces.sqlite3"))

    ACCESS_TOKEN_TTL = timedelta(minutes=int(os.environ.get("HOMEACCES_ACCESS_TTL_MIN", "15")))
    REFRESH_TOKEN_TTL = timedelta(days=int(os.environ.get("HOMEACCES_REFRESH_TTL_DAYS", "7")))
    # A just-rotated refresh token is still accepted for this long, so two tabs refreshing at the
    # same moment do not look like token theft. Later reuse revokes all of the user's sessions.
    REFRESH_REUSE_GRACE_SECONDS = 10
    JWT_ALGORITHM = "HS256"

    # Secure cookies require HTTPS; set HOMEACCES_COOKIE_SECURE=0 only for local development.
    COOKIE_SECURE = _bool("HOMEACCES_COOKIE_SECURE", "1")
    # Default only: admins can switch registration on/off in the UI (stored in the settings table).
    ALLOW_REGISTRATION = _bool("HOMEACCES_ALLOW_REGISTRATION", "0")

    # Activity log file ("" = database only) and how long entries stay in the database (0 = forever).
    ACTIVITY_LOG = os.environ.get("HOMEACCES_ACTIVITY_LOG", os.path.join(BASE_DIR, "instance", "activity.log"))
    ACTIVITY_RETENTION_DAYS = int(os.environ.get("HOMEACCES_ACTIVITY_RETENTION_DAYS", "365"))

    LOGIN_MAX_FAILURES = int(os.environ.get("HOMEACCES_LOGIN_MAX_FAILURES", "5"))
    LOGIN_WINDOW_SECONDS = int(os.environ.get("HOMEACCES_LOGIN_WINDOW_SECONDS", "900"))

    # PAM login for system accounts, via the privileged helper (homeacces-privd).
    PAM_ENABLED = _bool("HOMEACCES_PAM_ENABLED", "0")
    # PAM users in this system group get the web "admin" role ("" = never).
    PAM_ADMIN_GROUP = os.environ.get("HOMEACCES_PAM_ADMIN_GROUP", "sudo")
    PRIVD_SOCKET = os.environ.get("HOMEACCES_PRIVD_SOCKET", "/run/homeacces/privd.sock")
    TERMINAL_TICKET_TTL = timedelta(seconds=30)

    # SSH client terminal for all users, served by homeacces-ssh. Default only: admins can switch it
    # on/off in the UI. SSH_DIR holds each user's known_hosts file (shared with homeacces-ssh);
    # unset means "ssh" next to the database, as in homeacces_ssh.
    SSH_ENABLED = _bool("HOMEACCES_SSH_ENABLED", "1")
    SSH_DIR = os.environ.get("HOMEACCES_SSH_DIR")

    MAX_CONTENT_LENGTH = 64 * 1024
