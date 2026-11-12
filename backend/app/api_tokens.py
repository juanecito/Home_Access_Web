"""Personal access tokens: long-lived API credentials that each user manages for their scripts.

A token looks like `hat_<43 random URL-safe characters>` (256 bits) and is sent as
`Authorization: Bearer hat_…`. It is shown once, at creation; the database keeps only its SHA-256
(`api_tokens.token_hash`) and a short hint. Verification is done by `auth.require_auth`.

A token acts as its owner with the owner's current role, but cannot be used to manage tokens,
change the password or open a terminal (`require_auth(allow_api_token=False)`). Creating one
requires the current password, so a stolen browser session cannot quietly mint a lasting
credential; a password change (or an admin password reset) deletes all of the user's tokens.
"""
import secrets
import time
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, jsonify
from werkzeug.security import check_password_hash

from . import authlog, privd
from .activity import record
from .auth import (API_TOKEN_PREFIX, _record_failure, _too_many_failures, hash_api_token, json_body,
                   require_auth)
from .db import get_db

bp = Blueprint("api_tokens", __name__, url_prefix="/api/auth/tokens")

MAX_TOKENS_PER_USER = 20
NAME_MAX = 64
MAX_EXPIRY_DAYS = 365
HINT_CHARS = 6  # random characters kept after the prefix, e.g. "hat_Q2xv9A"


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts is not None else None


def _serialize(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "hint": row["hint"],
        "created_at": row["created_at"],
        "expires_at": _iso(row["expires_at"]),
        "expired": row["expires_at"] is not None and row["expires_at"] <= time.time(),
        "last_used_at": _iso(row["last_used_at"]),
        "last_used_ip": row["last_used_ip"],
    }


def _validate_name(name):
    if not isinstance(name, str) or not name.strip():
        abort(400, "name is required")
    name = name.strip()
    if len(name) > NAME_MAX or any(ord(c) < 32 or ord(c) == 127 for c in name):
        abort(400, f"name must be at most {NAME_MAX} printable characters")
    return name


def _validate_expiry(value):
    """`expires_in_days`: an integer from 1 to 365, or null for a token that never expires."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_EXPIRY_DAYS:
        abort(400, f"expires_in_days must be an integer from 1 to {MAX_EXPIRY_DAYS}, or null for no expiry")
    return int(time.time()) + value * 86400


def _check_password(password):
    """Re-authenticate the signed-in user (local password or system account through PAM)."""
    if not isinstance(password, str) or not password:
        abort(400, "password is required")
    key = f"reauth:{g.user['id']}"
    if _too_many_failures(key):
        record("auth.token_create", success=False, reason="throttled")
        abort(429, "too many failed attempts, try again later")
    if g.user["auth_source"] == "pam":
        if not current_app.config["PAM_ENABLED"]:
            abort(400, "PAM login is disabled")
        reply = privd.call("pam_auth", username=g.user["username"], password=password)
        valid = bool(reply.get("ok")) and reply.get("username") == g.user["username"]
    else:
        valid = check_password_hash(g.user["password_hash"], password)
    if not valid:
        _record_failure(key)
        record("auth.token_create", success=False, reason="password is incorrect")
        abort(400, "password is incorrect")
    db = get_db()
    db.execute("DELETE FROM login_failures WHERE key = ?", (key,))
    db.commit()


@bp.get("")
@require_auth(allow_api_token=False)
def list_tokens():
    rows = get_db().execute("SELECT * FROM api_tokens WHERE user_id = ? ORDER BY id DESC",
                            (g.user["id"],)).fetchall()
    return jsonify(tokens=[_serialize(r) for r in rows], max_tokens=MAX_TOKENS_PER_USER)


@bp.post("")
@require_auth(allow_api_token=False)
def create_token():
    data = json_body()
    name = _validate_name(data.get("name"))
    expires_at = _validate_expiry(data.get("expires_in_days"))
    _check_password(data.get("password"))

    db = get_db()
    if db.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id = ?", (g.user["id"],)).fetchone()[0] \
            >= MAX_TOKENS_PER_USER:
        abort(400, f"at most {MAX_TOKENS_PER_USER} tokens per user: delete one you no longer use")
    if db.execute("SELECT 1 FROM api_tokens WHERE user_id = ? AND name = ?", (g.user["id"], name)).fetchone():
        abort(409, "you already have a token with this name")

    secret = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    cur = db.execute(
        "INSERT INTO api_tokens (user_id, name, token_hash, hint, expires_at) VALUES (?, ?, ?, ?, ?)",
        (g.user["id"], name, hash_api_token(secret), secret[:len(API_TOKEN_PREFIX) + HINT_CHARS], expires_at),
    )
    db.commit()
    row = db.execute("SELECT * FROM api_tokens WHERE id = ?", (cur.lastrowid,)).fetchone()
    authlog.event("api_token.created", claims={"pat_id": row["id"]}, token=secret, user=g.user,
                  expires=_iso(expires_at) or "never")
    record("auth.token_create", token_id=row["id"], name=name, expires_at=_iso(expires_at) or "never")
    # The only time the secret leaves the server.
    return jsonify(token=_serialize(row), secret=secret), 201


@bp.delete("/<int:token_id>")
@require_auth(allow_api_token=False)
def delete_token(token_id):
    db = get_db()
    row = db.execute("SELECT * FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, g.user["id"])).fetchone()
    if row is None:
        abort(404, "token not found")
    db.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
    db.commit()
    authlog.event("api_token.revoked", claims={"pat_id": token_id}, user=g.user)
    record("auth.token_revoke", token_id=token_id, name=row["name"])
    return "", 204
