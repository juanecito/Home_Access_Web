"""User administration API (admin only)."""
from flask import Blueprint, abort, g, jsonify

from . import ssh
from .activity import record
from .auth import (hash_password, json_body, public_user, require_auth, revoke_all_refresh_tokens,
                   validate_new_local_username, validate_password)
from .db import get_db

bp = Blueprint("users", __name__, url_prefix="/api/users")

ADMIN = ("admin",)
ROLES = ("user", "admin")


def _get_user(user_id):
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        abort(404, "user not found")
    return row


def _validate_role(role):
    if role not in ROLES:
        abort(400, f"role must be one of: {', '.join(ROLES)}")
    return role


def _admin_count():
    return get_db().execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]


@bp.get("")
@require_auth(ADMIN)
def list_users():
    rows = get_db().execute(
        "SELECT users.*, (SELECT MAX(ts) FROM activity_log a WHERE a.user_id = users.id "
        "AND a.event = 'auth.login' AND a.success = 1) AS last_login FROM users ORDER BY id"
    ).fetchall()
    return jsonify(users=[{**public_user(r), "last_login": r["last_login"]} for r in rows])


@bp.get("/<int:user_id>")
@require_auth(ADMIN)
def get_user(user_id):
    return jsonify(user=public_user(_get_user(user_id)))


@bp.post("")
@require_auth(ADMIN)
def create_user():
    data = json_body()
    username = validate_new_local_username(data.get("username"), reveal_system=True)
    password = validate_password(data.get("password"))
    role = _validate_role(data.get("role", "user"))

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        abort(409, "username already taken")
    cur = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, hash_password(password), role),
    )
    db.commit()
    record("user.create", target=username, target_id=cur.lastrowid, role=role)
    return jsonify(user=public_user(_get_user(cur.lastrowid))), 201


@bp.put("/<int:user_id>")
@require_auth(ADMIN)
def update_user(user_id):
    """Update username, role and/or password of a local user. All fields are optional."""
    user = _get_user(user_id)
    data = json_body()
    if user["auth_source"] != "local" and data.keys() & {"username", "role", "password"}:
        abort(400, "system (PAM) accounts get their name, password and role from the system")
    db = get_db()
    changed = []

    if "username" in data and data["username"] != user["username"]:
        username = validate_new_local_username(data["username"], reveal_system=True)
        if db.execute("SELECT 1 FROM users WHERE username = ? AND id != ?", (username, user_id)).fetchone():
            abort(409, "username already taken")
        db.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
        changed.append("username")

    if "role" in data:
        role = _validate_role(data["role"])
        if user["role"] == "admin" and role != "admin" and _admin_count() <= 1:
            abort(400, "cannot demote the last admin")
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if role != user["role"]:
            changed.append("role")

    if "password" in data:
        password = validate_password(data["password"])
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        db.commit()
        revoke_all_refresh_tokens(user_id)
        changed.append("password")

    db.commit()
    if changed:
        updated = _get_user(user_id)
        record("user.update", target=user["username"], target_id=user_id, changed=changed,
               new_username=updated["username"] if "username" in changed else None,
               role=updated["role"] if "role" in changed else None)
    return jsonify(user=public_user(_get_user(user_id)))


# Deleting a PAM user removes its HomeAcces data only; the row is recreated at its next login.
@bp.delete("/<int:user_id>")
@require_auth(ADMIN)
def delete_user(user_id):
    user = _get_user(user_id)
    if user_id == g.user["id"]:
        abort(400, "you cannot delete your own account")
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    ssh.remove_known_hosts(user_id)
    record("user.delete", target=user["username"], target_id=user_id)
    return "", 204
