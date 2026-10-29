"""Web terminal tickets. The terminal itself is served by homeacces-privd (see homeacces_privd/server.py).

The browser POSTs here (cookie + CSRF protected), gets a 30-second single-use ticket, and sends
it as the first WebSocket message to /api/terminal/ws. This keeps the WebSocket immune to
cross-site hijacking and lets the root helper trust nothing but a signed, short-lived claim.
"""
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, jsonify

from .activity import record
from .auth import encode_token, require_auth

bp = Blueprint("terminal", __name__, url_prefix="/api/terminal")


@bp.post("/ticket")
@require_auth()
def ticket():
    if not current_app.config["PAM_ENABLED"]:
        abort(404, "terminal is disabled")
    if g.claims.get("amr") != "pam" or g.user["auth_source"] != "pam":
        abort(403, "the terminal requires signing in with a system (PAM) account")

    now = datetime.now(timezone.utc)
    ttl = current_app.config["TERMINAL_TICKET_TTL"]
    token = encode_token({
        "type": "terminal",
        "sub": str(g.user["id"]),
        "pam_user": g.user["username"],
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ttl,
    })
    record("terminal.open")
    return jsonify(ticket=token, expires_in=int(ttl.total_seconds()))
