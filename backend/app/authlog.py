"""Diagnostic log of JWT / session events (logger "homeacces.auth", written to stderr → journald).

Complements the activity log (an audit trail of *what users did*) with *what happened to their
tokens*: issued, verified, rejected (and why), rotated, revoked. One line per event, e.g.:

  2026-11-12 10:31:07,412 INFO homeacces.auth: event=token.issued type="access" user_id=3 user="alice"
      amr="local" sid="5f0c…" jti="9a1e…" exp="2026-11-12T10:46:07Z" fp="1c9d2e7fa0b3" ip="192.168.1.20"

Never logged: the token itself, its signature, the CSRF value or passwords. A token is identified
by its `jti` and `fp`, the first 12 hex digits of its SHA-256 — enough to match a token seen in a
browser or script against the log, useless for replaying it.

Level: HOMEACCES_AUTH_LOG_LEVEL (default INFO). DEBUG adds one line per successfully verified
access token (i.e. per authenticated API request).
"""
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone

import jwt
from flask import has_request_context, request

log = logging.getLogger("homeacces.auth")

# Claims that are safe and useful to log. "csrf" is deliberately absent.
MAX_VALUE = 120

SAFE_CLAIMS = ("type", "sub", "role", "amr", "sid", "jti", "pat_id", "pam_user", "profile_id", "iat", "exp")


def setup(app):
    """Attach a stderr handler once and apply the configured level."""
    level = app.config.get("AUTH_LOG_LEVEL", "INFO")
    log.setLevel(level.upper() if isinstance(level, str) else level)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
        log.propagate = False


def fingerprint(token):
    if not token:
        return None
    if isinstance(token, str):
        token = token.encode("utf-8", "replace")
    return hashlib.sha256(token).hexdigest()[:12]


def unverified_claims(token):
    """Claims of a token that failed verification, for diagnostics only (may be forged)."""
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.InvalidTokenError:
        return {}


def _iso(value):
    if isinstance(value, datetime):
        value = value.timestamp()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def _claim_fields(claims):
    fields = {}
    for name in SAFE_CLAIMS:
        if name not in claims:
            continue
        value = claims[name]
        if name in ("iat", "exp"):
            value = _iso(value)
        elif name == "sub" and isinstance(value, str) and value.isdigit() and len(value) < 19:
            value = int(value)  # same form as user_id from a users row
        fields["user_id" if name == "sub" else name] = value
    return fields


def _format(name, fields):
    parts = [f"event={name}"]  # fixed vocabulary, never user input
    for key, value in fields.items():
        if value is None:
            continue
        # Numbers as-is; everything else JSON-quoted, so user input cannot forge log lines.
        plain = isinstance(value, (int, float)) and not isinstance(value, bool)
        if isinstance(value, str) and len(value) > MAX_VALUE:  # e.g. claims of a forged token
            value = value[:MAX_VALUE] + "…"
        parts.append(f"{key}={value if plain else json.dumps(value, default=str)}")
    return " ".join(parts)


def event(name, *, claims=None, token=None, user=None, level=logging.INFO, **details):
    """Log the token event `name`.

    `claims` are the token's claims (only SAFE_CLAIMS are written), `token` the encoded JWT (only
    its fingerprint is written), `user` a users row (adds user_id / user name).
    """
    if not log.isEnabledFor(level):
        return
    fields = _claim_fields(claims or {})
    if user is not None:
        fields["user_id"] = user["id"]
        fields["user"] = user["username"]
    if token is not None:
        fields["fp"] = fingerprint(token)
    fields.update(details)
    if has_request_context():
        fields.setdefault("ip", request.remote_addr)
        fields.setdefault("path", request.path)
    log.log(level, "%s", _format(name, fields))
