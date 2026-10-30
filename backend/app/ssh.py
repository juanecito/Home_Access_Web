"""SSH menu: each user's saved SSH connections and tickets to open them in the browser.

The SSH client runs in homeacces-ssh (see homeacces_ssh/server.py), unprivileged. As for the local
terminal (terminal.py), the browser POSTs here for a 30-second single-use ticket naming one of its
profiles and sends it as the first WebSocket message to /api/ssh/ws.

Passwords are never stored: OpenSSH asks for them (and for unknown host keys) inside the terminal.
A profile may hold a private key; it is write-only for the browser. Host keys accepted by a user are
kept in a known_hosts file per user under SSH_DIR, so users do not trust each other's servers.
"""
import ipaddress
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, jsonify

from . import settings
from .activity import record
from .auth import encode_token, json_body, require_auth
from .db import get_db

bp = Blueprint("ssh", __name__, url_prefix="/api/ssh")

MAX_PROFILES = 100
MAX_KEY_LENGTH = 16 * 1024
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62})(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62}))*\.?$")
# Conservative: no '%' or '$' (OpenSSH token/variable expansion), never a leading '-'.
REMOTE_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,63}$")
KEY_RE = re.compile(r"^-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----\n.+\n-----END [A-Z0-9 ]*PRIVATE KEY-----\n$", re.S)


def known_hosts_path(user_id):
    return os.path.join(current_app.config["SSH_DIR"], "known_hosts", str(int(user_id)))


def remove_known_hosts(user_id):
    """Forget all host keys of a user (when the user is deleted)."""
    try:
        os.unlink(known_hosts_path(user_id))
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- validation

def _validate_host(host):
    if not isinstance(host, str):
        abort(400, "host is required")
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        if "%" not in host:  # IPv6 zone ids ("fe80::1%eth0") would be read as OpenSSH tokens
            return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if len(host) > 253 or not HOSTNAME_RE.match(host):
        abort(400, "host must be a host name or an IP address")
    return host.lower()


def _validate(data, existing=None):
    """Return the profile columns from a request body. `private_key` absent keeps the existing key."""
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
        abort(400, "name is required (max 64 characters)")
    port = data.get("port", 22)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        abort(400, "port must be an integer from 1 to 65535")
    username = data.get("username")
    if not isinstance(username, str) or not REMOTE_USER_RE.match(username.strip()):
        abort(400, "remote user must be 1-64 characters: letters, digits, '_', '.', '@', '-'")

    key = existing["private_key"] if existing is not None else None
    if "private_key" in data:
        key = data["private_key"] or None
        if key is not None:
            if not isinstance(key, str) or len(key) > MAX_KEY_LENGTH:
                abort(400, "private key must be text (max 16 KB)")
            key = key.replace("\r\n", "\n").strip() + "\n"
            if not KEY_RE.match(key):
                abort(400, "private key must be in OpenSSH or PEM format (-----BEGIN … PRIVATE KEY-----)")
    return {"name": name.strip(), "host": _validate_host(data.get("host")), "port": port,
            "username": username.strip(), "private_key": key}


def _serialize(row):
    return {"id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
            "username": row["username"], "has_key": row["private_key"] is not None,
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _get_profile(profile_id):
    # Profiles are personal (they may hold a key): admins cannot see other users' profiles either.
    row = get_db().execute("SELECT * FROM ssh_profiles WHERE id = ? AND owner_id = ?",
                           (profile_id, g.user["id"])).fetchone()
    if row is None:
        abort(404, "SSH connection not found")
    return row


def _save(sql, params):
    db = get_db()
    try:
        cur = db.execute(sql, params)
    except sqlite3.IntegrityError:
        abort(409, "you already have an SSH connection with this name")
    db.commit()
    return cur


# ---------------------------------------------------------------- profiles

@bp.get("/profiles")
@require_auth()
def list_profiles():
    rows = get_db().execute("SELECT * FROM ssh_profiles WHERE owner_id = ? ORDER BY name COLLATE NOCASE",
                            (g.user["id"],)).fetchall()
    return jsonify(profiles=[_serialize(r) for r in rows], enabled=settings.ssh_enabled())


@bp.post("/profiles")
@require_auth()
def create_profile():
    p = _validate(json_body())
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM ssh_profiles WHERE owner_id = ?", (g.user["id"],)).fetchone()[0] \
            >= MAX_PROFILES:
        abort(400, f"at most {MAX_PROFILES} SSH connections per user")
    cur = _save("INSERT INTO ssh_profiles (owner_id, name, host, port, username, private_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (g.user["id"], p["name"], p["host"], p["port"], p["username"], p["private_key"]))
    record("ssh.profile_create", profile_id=cur.lastrowid, name=p["name"], host=p["host"], port=p["port"],
           remote_user=p["username"], key=p["private_key"] is not None)
    return jsonify(profile=_serialize(_get_profile(cur.lastrowid))), 201


@bp.put("/profiles/<int:profile_id>")
@require_auth()
def update_profile(profile_id):
    existing = _get_profile(profile_id)
    p = _validate(json_body(), existing)
    _save("UPDATE ssh_profiles SET name = ?, host = ?, port = ?, username = ?, private_key = ?, "
          "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
          (p["name"], p["host"], p["port"], p["username"], p["private_key"], profile_id))
    record("ssh.profile_update", profile_id=profile_id, name=p["name"], host=p["host"], port=p["port"],
           remote_user=p["username"], key=p["private_key"] is not None)
    return jsonify(profile=_serialize(_get_profile(profile_id)))


@bp.delete("/profiles/<int:profile_id>")
@require_auth()
def delete_profile(profile_id):
    profile = _get_profile(profile_id)
    db = get_db()
    db.execute("DELETE FROM ssh_profiles WHERE id = ?", (profile_id,))
    db.commit()
    record("ssh.profile_delete", profile_id=profile_id, name=profile["name"], host=profile["host"])
    return "", 204


@bp.delete("/profiles/<int:profile_id>/host-key")
@require_auth()
def forget_host_key(profile_id):
    """Remove the saved host key of the profile's server, e.g. after the server was reinstalled."""
    profile = _get_profile(profile_id)
    # How OpenSSH writes the host in known_hosts (it lowercases names; we store them lowercase).
    token = profile["host"] if profile["port"] == 22 else f"[{profile['host']}]:{profile['port']}"
    path = known_hosts_path(g.user["id"])
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    kept = []
    for line in lines:
        fields = line.split()
        if fields and fields[0].startswith("@"):  # @cert-authority / @revoked marker
            fields = fields[1:]
        if not fields or token not in fields[0].lower().split(","):
            kept.append(line)
    removed = len(lines) - len(kept)
    if removed:
        tmp = f"{path}.{uuid.uuid4().hex}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            f.writelines(kept)
        os.replace(tmp, path)
    record("ssh.host_key_forget", profile_id=profile_id, host=profile["host"], port=profile["port"],
           removed=removed)
    return jsonify(removed=removed)


# ---------------------------------------------------------------- tickets

@bp.post("/ticket")
@require_auth()
def ticket():
    if not settings.ssh_enabled():
        abort(403, "the SSH terminal is disabled by an administrator")
    profile_id = json_body().get("profile_id")
    if isinstance(profile_id, bool) or not isinstance(profile_id, int):
        abort(400, "profile_id must be an integer")
    profile = _get_profile(profile_id)

    now = datetime.now(timezone.utc)
    ttl = current_app.config["TERMINAL_TICKET_TTL"]
    token = encode_token({
        "type": "ssh",
        "sub": str(g.user["id"]),
        "profile_id": profile["id"],
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ttl,
    })
    record("ssh.open", profile_id=profile["id"], host=profile["host"], port=profile["port"],
           remote_user=profile["username"])
    return jsonify(ticket=token, expires_in=int(ttl.total_seconds()))
