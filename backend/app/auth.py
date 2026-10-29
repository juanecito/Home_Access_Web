"""Authentication: password hashing, JWT issuing/verification, cookies and CSRF.

Two login methods:
* "local": accounts stored in SQLite (password hashed with scrypt).
* "pam":   system accounts, verified by the privileged helper homeacces-privd over a Unix socket.
           A matching row (auth_source='pam') is kept in `users` so PAM users own items too.
The method is recorded in the JWT as the `amr` claim; the web terminal requires amr="pam".

Browser clients receive two HttpOnly cookies (access + refresh JWT) and a readable
`csrf_token` cookie. Every state-changing request authenticated by cookie must echo
that value in the `X-CSRF-Token` header; it is checked against the `csrf` claim inside
the JWT, so a forged cookie cannot be used.

Non-browser clients (scripts, curl) can call POST /api/auth/token and send
`Authorization: Bearer <token>`; CSRF does not apply to them.
"""
import base64
import binascii
import hmac
import pwd
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

import jwt
from flask import Blueprint, abort, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from . import privd, settings
from .activity import record
from .db import get_db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
REFRESH_COOKIE_PATH = "/api/auth"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
METHODS = ("local", "pam")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
PAM_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
AVATAR_SIZE = 60
AVATAR_MAX_BYTES = 32 * 1024
# Used to keep login timing constant when the username does not exist.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


# ---------------------------------------------------------------- validation

def validate_username(username):
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        abort(400, "username must be 3-32 characters: letters, digits, '_', '.', '-'")
    return username


def validate_new_local_username(username, reveal_system=False):
    """For new local accounts: also refuse names of system accounts, so they can't be squatted.

    Anonymous callers get the same message as for a taken name, so that self-registration
    cannot be used to list the system's accounts; admins (`reveal_system`) get the reason."""
    validate_username(username)
    if current_app.config["PAM_ENABLED"]:
        try:
            pwd.getpwnam(username.lower())
        except KeyError:
            pass
        else:
            abort(409, "username is reserved for a system account" if reveal_system else "username already taken")
    return username


def validate_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        abort(400, "password must be 8-128 characters")
    return password


def hash_password(password):
    return generate_password_hash(password)


def public_user(row):
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "auth_source": row["auth_source"], "created_at": row["created_at"],
            "avatar": avatar_data_url(row["avatar"])}


def _image_info(data):
    """Return (mime type, width, height) of a PNG or JPEG image, or None for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        return "image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 <= len(data) and data[i] == 0xFF:
            marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
            # Start-of-frame markers (not DHT C4, JPG C8, DAC CC) carry the dimensions.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return "image/jpeg", int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + length
    return None


def avatar_data_url(data):
    if not data:
        return None
    info = _image_info(data)
    return f"data:{info[0] if info else 'image/png'};base64,{base64.b64encode(data).decode()}"


def validate_avatar(value):
    """Accept a base64 data URL (or bare base64) of a 60x60 PNG or JPEG; return the raw bytes."""
    if not isinstance(value, str):
        abort(400, "avatar must be a base64-encoded image")
    if value.startswith("data:"):
        value = value.partition(",")[2]
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        abort(400, "avatar must be a base64-encoded image")
    if len(data) > AVATAR_MAX_BYTES:
        abort(400, f"avatar must be at most {AVATAR_MAX_BYTES // 1024} KB")
    info = _image_info(data)
    if info is None:
        abort(400, "avatar must be a PNG or JPEG image")
    if info[1:] != (AVATAR_SIZE, AVATAR_SIZE):
        abort(400, f"avatar must be {AVATAR_SIZE}x{AVATAR_SIZE} pixels")
    return data


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object body")
    return data


# ---------------------------------------------------------------- tokens

def _now():
    return datetime.now(timezone.utc)


def encode_token(claims):
    cfg = current_app.config
    return jwt.encode(claims, cfg["SECRET_KEY"], algorithm=cfg["JWT_ALGORITHM"])


def _decode(token, expected_type):
    cfg = current_app.config
    try:
        claims = jwt.decode(
            token,
            cfg["SECRET_KEY"],
            algorithms=[cfg["JWT_ALGORITHM"]],
            options={"require": ["exp", "iat", "sub", "type", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        abort(401, "token_expired")
    except jwt.InvalidTokenError:
        abort(401, "invalid_token")
    if claims.get("type") != expected_type or claims.get("amr", "local") not in METHODS:
        abort(401, "invalid_token")
    return claims


def issue_access_token(user, csrf, amr, sid):
    now = _now()
    return encode_token({
        "sub": str(user["id"]),
        "role": user["role"],
        "type": "access",
        "amr": amr,
        "csrf": csrf,
        "sid": sid,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + current_app.config["ACCESS_TOKEN_TTL"],
    })


def _insert_session_row(user, session_id, exp):
    jti = uuid.uuid4().hex
    db = get_db()
    db.execute(
        "INSERT INTO refresh_tokens (jti, user_id, session_id, expires_at) VALUES (?, ?, ?, ?)",
        (jti, user["id"], session_id or jti, int(exp.timestamp())),
    )
    db.commit()
    return jti


def issue_refresh_token(user, csrf, amr, session_id):
    now = _now()
    exp = now + current_app.config["REFRESH_TOKEN_TTL"]
    jti = _insert_session_row(user, session_id, exp)
    return encode_token({"sub": str(user["id"]), "type": "refresh", "amr": amr, "csrf": csrf,
                         "jti": jti, "iat": now, "exp": exp})


def revoke_all_refresh_tokens(user_id):
    db = get_db()
    db.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
    db.commit()


def _session_active(session_id):
    return get_db().execute(
        "SELECT 1 FROM refresh_tokens WHERE session_id = ? AND revoked = 0 AND expires_at > ? LIMIT 1",
        (session_id, int(time.time())),
    ).fetchone() is not None


# ---------------------------------------------------------------- cookies

def _set_cookie(resp, name, value, max_age, path="/", httponly=True):
    resp.set_cookie(
        name, value, max_age=max_age, path=path, httponly=httponly,
        secure=current_app.config["COOKIE_SECURE"], samesite="Strict",
    )


def set_auth_cookies(resp, user, amr, session_id=None):
    """Issue access + refresh cookies. A new sign-in starts a session; a refresh continues one."""
    cfg = current_app.config
    csrf = secrets.token_urlsafe(32)
    session_id = session_id or uuid.uuid4().hex
    refresh_age = int(cfg["REFRESH_TOKEN_TTL"].total_seconds())
    _set_cookie(resp, ACCESS_COOKIE, issue_access_token(user, csrf, amr, session_id),
                int(cfg["ACCESS_TOKEN_TTL"].total_seconds()))
    _set_cookie(resp, REFRESH_COOKIE, issue_refresh_token(user, csrf, amr, session_id), refresh_age,
                path=REFRESH_COOKIE_PATH)
    _set_cookie(resp, CSRF_COOKIE, csrf, refresh_age, httponly=False)
    return resp


def clear_auth_cookies(resp):
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_COOKIE_PATH), (CSRF_COOKIE, "/")):
        resp.delete_cookie(name, path=path, secure=current_app.config["COOKIE_SECURE"], samesite="Strict")
    return resp


def _session_response(user, amr, status=200, session_id=None):
    body = jsonify(user=public_user(user), session={"method": amr})
    return set_auth_cookies(body, user, amr, session_id), status


def _check_csrf(claims):
    sent = request.headers.get(CSRF_HEADER, "")
    expected = claims.get("csrf") or ""
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        abort(403, "csrf_failed")


# ---------------------------------------------------------------- decorator

def _load_user(user_id):
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def require_auth(roles=None):
    """Protect a view. Sets g.user (user row) and g.claims (access token claims)."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                token, from_cookie = header[7:].strip(), False
            else:
                token, from_cookie = request.cookies.get(ACCESS_COOKIE), True
            if not token:
                abort(401, "authentication_required")

            claims = _decode(token, "access")
            if from_cookie and request.method not in SAFE_METHODS:
                _check_csrf(claims)
            # Logout, password changes and theft detection revoke the session: its access
            # tokens stop working at once instead of at expiry. (Tokens issued before
            # sessions existed carry no sid and simply run out.)
            if claims.get("sid") is not None and not _session_active(claims["sid"]):
                abort(401, "invalid_token")

            user = _load_user(int(claims["sub"]))
            if user is None:
                abort(401, "invalid_token")
            if roles and user["role"] not in roles:
                abort(403, "forbidden")
            g.user = user
            g.claims = claims
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------- login throttling

def _throttle_key(username):
    return f"{request.remote_addr}|{username.lower()}"


def _too_many_failures(key):
    cfg = current_app.config
    since = int(time.time()) - cfg["LOGIN_WINDOW_SECONDS"]
    count = get_db().execute(
        "SELECT COUNT(*) FROM login_failures WHERE key = ? AND ts >= ?", (key, since)
    ).fetchone()[0]
    return count >= cfg["LOGIN_MAX_FAILURES"]


def _record_failure(key):
    db = get_db()
    now = int(time.time())
    db.execute("INSERT INTO login_failures (key, ts) VALUES (?, ?)", (key, now))
    # Opportunistic cleanup of stale rows.
    db.execute("DELETE FROM login_failures WHERE ts < ?", (now - current_app.config["LOGIN_WINDOW_SECONDS"],))
    db.execute("DELETE FROM refresh_tokens WHERE expires_at < ?", (now,))
    db.commit()


# ---------------------------------------------------------------- PAM

def _sync_pam_user(username, groups):
    """Create or update the local row of a PAM user; the role follows the system groups."""
    admin_group = current_app.config["PAM_ADMIN_GROUP"]
    role = "admin" if admin_group and admin_group in groups else "user"
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is not None and row["auth_source"] != "pam":
        abort(409, "this username belongs to a local HomeAcces account")
    if row is None:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, role, auth_source) VALUES (?, '!', ?, 'pam')",
            (username, role),
        )
        user_id = cur.lastrowid
    else:
        user_id = row["id"]
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    db.commit()
    return _load_user(user_id)


def _pam_verify(username, password):
    """Returns the synced user row, or None if PAM rejected the credentials."""
    if not PAM_USERNAME_RE.match(username):
        return None
    reply = privd.call("pam_auth", username=username, password=password)
    if not reply.get("ok") or reply.get("username") != username:
        return None
    return _sync_pam_user(username, reply.get("groups", []))


def _local_verify(username, password):
    user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    local = user is not None and user["auth_source"] == "local"
    valid = check_password_hash(user["password_hash"] if local else _DUMMY_HASH, password)
    return user if local and valid else None


def _authenticate(data, client):
    """Validates credentials from a login body and logs the attempt. Returns (user row, method).

    `client` is "browser" (cookie session) or "api" (bearer token), for the activity log.
    """
    username = data.get("username")
    password = data.get("password")
    method = data.get("method", "local")
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        abort(400, "username and password are required")
    if method not in METHODS:
        abort(400, "method must be 'local' or 'pam'")
    if method == "pam" and not current_app.config["PAM_ENABLED"]:
        abort(400, "PAM login is disabled")

    key = _throttle_key(f"{method}:{username}")
    if _too_many_failures(key):
        record("auth.login", username=username, success=False, method=method, client=client, reason="throttled")
        abort(429, "too many failed attempts, try again later")

    user = (_pam_verify if method == "pam" else _local_verify)(username, password)
    if user is None:
        _record_failure(key)
        record("auth.login", username=username, success=False, method=method, client=client,
               reason="invalid credentials")
        abort(401, "invalid username or password")

    db = get_db()
    db.execute("DELETE FROM login_failures WHERE key = ?", (key,))
    db.commit()
    record("auth.login", user=user, method=method, client=client)
    return user, method


# ---------------------------------------------------------------- routes

@bp.get("/methods")
def methods():
    cfg = current_app.config
    return jsonify(local=True, pam=cfg["PAM_ENABLED"], registration=settings.allow_registration())


@bp.post("/register")
def register():
    if not settings.allow_registration():
        abort(403, "registration is disabled")
    data = json_body()
    username = validate_new_local_username(data.get("username"))
    password = validate_password(data.get("password"))

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        abort(409, "username already taken")
    cur = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'user')",
        (username, hash_password(password)),
    )
    db.commit()
    user = _load_user(cur.lastrowid)
    record("auth.register", user=user)
    return _session_response(user, "local", 201)


@bp.post("/login")
def login():
    user, method = _authenticate(json_body(), "browser")
    return _session_response(user, method)


@bp.post("/token")
def token():
    """For API clients: returns a bearer access token in the body, sets no cookies."""
    user, method = _authenticate(json_body(), "api")
    cfg = current_app.config
    # A session row (never handed out as a refresh token) lets password changes and
    # "revoke all" end bearer tokens too.
    session_id = uuid.uuid4().hex
    _insert_session_row(user, session_id, _now() + cfg["ACCESS_TOKEN_TTL"])
    return jsonify(
        access_token=issue_access_token(user, csrf=None, amr=method, sid=session_id),
        token_type="Bearer",
        expires_in=int(cfg["ACCESS_TOKEN_TTL"].total_seconds()),
    )


@bp.post("/refresh")
def refresh():
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        abort(401, "authentication_required")
    claims = _decode(token, "refresh")
    _check_csrf(claims)

    db = get_db()
    now = int(time.time())
    # Rotate atomically: of several concurrent requests (any worker), exactly one wins.
    rotated = db.execute("UPDATE refresh_tokens SET revoked = 2, rotated_at = ? WHERE jti = ? AND revoked = 0",
                         (now, claims["jti"])).rowcount
    db.commit()
    row = db.execute("SELECT * FROM refresh_tokens WHERE jti = ?", (claims["jti"],)).fetchone()
    if row is None:
        abort(401, "invalid_token")
    if not rotated:
        # Another tab may have rotated this token a moment ago: allow that briefly.
        grace = current_app.config["REFRESH_REUSE_GRACE_SECONDS"]
        if row["revoked"] != 2 or row["rotated_at"] is None or now - row["rotated_at"] >= grace:
            # A rotated or revoked token was reused: assume theft and log the user out everywhere.
            revoke_all_refresh_tokens(row["user_id"])
            record("auth.token_reuse", user=_load_user(row["user_id"]), success=False,
                   reason="rotated refresh token reused; all sessions revoked")
            abort(401, "invalid_token")

    user = _load_user(row["user_id"])
    if user is None:
        abort(401, "invalid_token")

    amr = claims.get("amr", "local")
    if amr == "pam":
        # Re-check the system account on every refresh: locked, expired or removed from
        # the allowed group means the web session ends too.
        reply = privd.call("pam_account", username=user["username"])
        if not current_app.config["PAM_ENABLED"] or not reply.get("ok"):
            revoke_all_refresh_tokens(user["id"])
            record("auth.session_revoked", user=user, success=False, reason="system account no longer allowed")
            abort(401, "invalid_token")
        user = _sync_pam_user(user["username"], reply.get("groups", []))
    return _session_response(user, amr, session_id=row["session_id"] or row["jti"])


@bp.post("/logout")
def logout():
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        try:
            claims = jwt.decode(
                token, current_app.config["SECRET_KEY"],
                algorithms=[current_app.config["JWT_ALGORITHM"]], options={"verify_exp": False},
            )
            # End the whole session (all its rotated tokens), which also invalidates its access tokens.
            db = get_db()
            db.execute("UPDATE refresh_tokens SET revoked = 1 WHERE jti = ? OR session_id = "
                       "(SELECT session_id FROM refresh_tokens WHERE jti = ?)", (claims.get("jti"), claims.get("jti")))
            db.commit()
            user = _load_user(int(claims["sub"]))
            if user is not None:
                record("auth.logout", user=user)
        except (jwt.InvalidTokenError, KeyError, ValueError):
            pass
    return clear_auth_cookies(jsonify(ok=True))


@bp.get("/me")
@require_auth()
def me():
    return jsonify(user=public_user(g.user), session={"method": g.claims.get("amr", "local")})


@bp.put("/password")
@require_auth()
def change_password():
    if g.user["auth_source"] != "local":
        abort(400, "system accounts change their password on the system (passwd)")
    data = json_body()
    # Throttled per account: a stolen session must not be usable to guess the real password.
    key = f"pwchange:{g.user['id']}"
    if _too_many_failures(key):
        record("auth.password_change", success=False, reason="throttled")
        abort(429, "too many failed attempts, try again later")
    current = data.get("current_password")
    if not isinstance(current, str) or not check_password_hash(g.user["password_hash"], current):
        _record_failure(key)
        record("auth.password_change", success=False, reason="current password is incorrect")
        abort(400, "current password is incorrect")
    new = validate_password(data.get("new_password"))

    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), g.user["id"]))
    db.execute("DELETE FROM login_failures WHERE key = ?", (key,))
    db.commit()
    revoke_all_refresh_tokens(g.user["id"])
    record("auth.password_change")
    # Re-issue cookies for this session so the user stays logged in here only.
    return set_auth_cookies(jsonify(ok=True), g.user, "local")


@bp.put("/avatar")
@require_auth()
def set_avatar():
    data = validate_avatar(json_body().get("avatar"))
    db = get_db()
    db.execute("UPDATE users SET avatar = ? WHERE id = ?", (data, g.user["id"]))
    db.commit()
    record("auth.avatar_change")
    return jsonify(user=public_user(_load_user(g.user["id"])))


@bp.delete("/avatar")
@require_auth()
def delete_avatar():
    db = get_db()
    db.execute("UPDATE users SET avatar = NULL WHERE id = ?", (g.user["id"],))
    db.commit()
    record("auth.avatar_change", removed=True)
    return jsonify(user=public_user(_load_user(g.user["id"])))
