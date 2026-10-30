import os

import pytest

from app import create_app
from app.auth import hash_password
from app.db import get_db, init_db


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "x" * 48,
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "COOKIE_SECURE": False,
        "ACTIVITY_LOG": str(tmp_path / "activity.log"),
        "SSH_DIR": str(tmp_path / "ssh"),
        "ALLOW_REGISTRATION": True,  # off by default in production
    })
    with app.app_context():
        init_db()
        get_db().execute(
            "INSERT INTO users (username, password_hash, role) VALUES ('admin', ?, 'admin')",
            (hash_password("adminpass123"),),
        )
        get_db().commit()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client):
    return {"X-CSRF-Token": client.get_cookie("csrf_token").value}


def register(client, username="alice", password="alicepass123"):
    return client.post("/api/auth/register", json={"username": username, "password": password})


def login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_health(client):
    assert client.get("/api/health").json == {"status": "ok"}


def test_register_login_me_logout(client):
    r = register(client)
    assert r.status_code == 201
    assert r.json["user"]["role"] == "user"
    assert client.get_cookie("access_token").http_only

    assert client.get("/api/auth/me").json["user"]["username"] == "alice"
    assert register(client).status_code == 409

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    assert login(client, "alice", "wrong-password").status_code == 401
    assert login(client, "alice", "alicepass123").status_code == 200


def test_validation(client):
    assert register(client, "a", "alicepass123").status_code == 400
    assert register(client, "bob", "short").status_code == 400
    assert client.post("/api/auth/register", data="nope").status_code == 400


def test_csrf_required_for_cookie_writes(client):
    register(client)
    r = client.post("/api/items", json={"title": "x"})
    assert r.status_code == 403 and r.json["error"] == "csrf_failed"
    r = client.post("/api/items", json={"title": "x"}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    assert client.post("/api/items", json={"title": "x"}, headers=csrf(client)).status_code == 201


def test_items_crud(client):
    register(client)
    h = csrf(client)
    r = client.post("/api/items", json={"title": "Buy milk", "description": "2L"}, headers=h)
    assert r.status_code == 201
    item_id = r.json["item"]["id"]
    assert r.json["item"]["done"] is False

    assert client.get(f"/api/items/{item_id}").json["item"]["title"] == "Buy milk"

    r = client.put(f"/api/items/{item_id}", json={"title": "Buy milk", "done": True}, headers=h)
    assert r.json["item"]["done"] is True

    listing = client.get("/api/items?q=milk").json
    assert listing["total"] == 1
    assert client.get("/api/items?q=zzz").json["total"] == 0

    assert client.delete(f"/api/items/{item_id}", headers=h).status_code == 204
    assert client.get(f"/api/items/{item_id}").status_code == 404


def test_items_are_isolated_between_users(app):
    alice, bob, admin = app.test_client(), app.test_client(), app.test_client()
    register(alice, "alice")
    register(bob, "bob")
    login(admin, "admin", "adminpass123")

    item_id = alice.post("/api/items", json={"title": "secret"}, headers=csrf(alice)).json["item"]["id"]
    assert bob.get(f"/api/items/{item_id}").status_code == 404
    assert bob.delete(f"/api/items/{item_id}", headers=csrf(bob)).status_code == 404
    assert bob.get("/api/items").json["total"] == 0
    assert admin.get(f"/api/items/{item_id}").status_code == 200


def test_bearer_token(client):
    register(client)
    other = client.application.test_client()
    tok = other.post("/api/auth/token", json={"username": "alice", "password": "alicepass123"}).json
    assert other.get_cookie("access_token") is None
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    # No CSRF header needed with a bearer token.
    assert other.post("/api/items", json={"title": "from script"}, headers=h).status_code == 201
    assert other.get("/api/items", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(client):
    client.application.config["REFRESH_REUSE_GRACE_SECONDS"] = 0
    register(client)
    old_refresh = client.get_cookie("refresh_token", path="/api/auth").value
    old_csrf = client.get_cookie("csrf_token").value

    r = client.post("/api/auth/refresh", headers=csrf(client))
    assert r.status_code == 200
    assert client.get_cookie("refresh_token", path="/api/auth").value != old_refresh

    # Replaying the rotated token revokes every session of the user.
    attacker = client.application.test_client()
    attacker.set_cookie("refresh_token", old_refresh, path="/api/auth")
    assert attacker.post("/api/auth/refresh", headers={"X-CSRF-Token": old_csrf}).status_code == 401
    assert client.post("/api/auth/refresh", headers=csrf(client)).status_code == 401
    # The revoked session's access token stops working too, not only at expiry.
    assert client.get("/api/auth/me").status_code == 401


def test_concurrent_refresh_within_grace_period(client):
    register(client)
    old_refresh = client.get_cookie("refresh_token", path="/api/auth").value
    old_csrf = client.get_cookie("csrf_token").value
    assert client.post("/api/auth/refresh", headers=csrf(client)).status_code == 200

    # A second tab sends the same token a moment later: not treated as theft.
    tab = client.application.test_client()
    tab.set_cookie("refresh_token", old_refresh, path="/api/auth")
    assert tab.post("/api/auth/refresh", headers={"X-CSRF-Token": old_csrf}).status_code == 200
    assert client.post("/api/auth/refresh", headers=csrf(client)).status_code == 200
    assert tab.get("/api/auth/me").status_code == 200


def test_logout_revokes_access_token(client):
    register(client)
    access = client.get_cookie("access_token").value
    client.post("/api/auth/logout")
    stolen = client.application.test_client()
    stolen.set_cookie("access_token", access)
    assert stolen.get("/api/auth/me").status_code == 401


def test_password_change_revokes_other_sessions_and_bearer_tokens(app):
    alice, other = app.test_client(), app.test_client()
    register(alice)
    login(other, "alice", "alicepass123")
    token = app.test_client().post("/api/auth/token",
                                   json={"username": "alice", "password": "alicepass123"}).json["access_token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert app.test_client().get("/api/auth/me", headers=bearer).status_code == 200

    r = alice.put("/api/auth/password", json={"current_password": "alicepass123", "new_password": "newpass1234"},
                  headers=csrf(alice))
    assert r.status_code == 200
    assert alice.get("/api/auth/me").status_code == 200  # this session got new cookies
    assert other.get("/api/auth/me").status_code == 401
    assert app.test_client().get("/api/auth/me", headers=bearer).status_code == 401


def test_password_change_is_throttled(client):
    register(client)
    h = csrf(client)
    body = {"current_password": "guess-wrong", "new_password": "newpass1234"}
    for _ in range(5):
        assert client.put("/api/auth/password", json=body, headers=h).status_code == 400
    body["current_password"] = "alicepass123"
    assert client.put("/api/auth/password", json=body, headers=h).status_code == 429


def test_login_throttling(client):
    register(client)
    for _ in range(5):
        assert login(client, "alice", "bad-password").status_code == 401
    assert login(client, "alice", "alicepass123").status_code == 429


def test_change_password(client):
    register(client)
    h = csrf(client)
    bad = client.put("/api/auth/password", json={"current_password": "nope", "new_password": "newpass1234"}, headers=h)
    assert bad.status_code == 400
    ok = client.put("/api/auth/password",
                    json={"current_password": "alicepass123", "new_password": "newpass1234"}, headers=h)
    assert ok.status_code == 200
    assert login(client, "alice", "newpass1234").status_code == 200


def test_user_admin(app):
    admin, alice = app.test_client(), app.test_client()
    login(admin, "admin", "adminpass123")
    register(alice)
    assert alice.get("/api/users").status_code == 403

    h = csrf(admin)
    users = admin.get("/api/users").json["users"]
    assert {u["username"] for u in users} == {"admin", "alice"}
    assert all("password_hash" not in u for u in users)

    r = admin.post("/api/users", json={"username": "carol", "password": "carolpass123", "role": "admin"}, headers=h)
    assert r.status_code == 201
    carol_id = r.json["user"]["id"]
    assert admin.put(f"/api/users/{carol_id}", json={"role": "user"}, headers=h).json["user"]["role"] == "user"
    assert admin.put("/api/users/1", json={"role": "user"}, headers=h).status_code == 400  # last admin
    assert admin.delete("/api/users/1", headers=h).status_code == 400  # self
    assert admin.delete(f"/api/users/{carol_id}", headers=h).status_code == 204


def test_admin_renames_user(app):
    admin = app.test_client()
    login(admin, "admin", "adminpass123")
    h = csrf(admin)
    r = admin.post("/api/users", json={"username": "carol", "password": "carolpass123"}, headers=h)
    uid = r.json["user"]["id"]
    admin.post("/api/users", json={"username": "dave", "password": "davepass123"}, headers=h)

    assert admin.put(f"/api/users/{uid}", json={"username": "dave"}, headers=h).status_code == 409
    assert admin.put(f"/api/users/{uid}", json={"username": "x"}, headers=h).status_code == 400
    r = admin.put(f"/api/users/{uid}", json={"username": "caroline"}, headers=h)
    assert r.json["user"]["username"] == "caroline"
    assert login(app.test_client(), "caroline", "carolpass123").status_code == 200
    users = {u["username"]: u for u in admin.get("/api/users").json["users"]}
    assert users["caroline"]["last_login"] is not None and users["dave"]["last_login"] is None


def test_registration_toggle(app):
    admin, anon = app.test_client(), app.test_client()
    login(admin, "admin", "adminpass123")
    h = csrf(admin)
    assert anon.get("/api/admin/settings").status_code == 401
    register(anon, "eve")
    assert anon.put("/api/admin/settings", json={"allow_registration": False}, headers=csrf(anon)).status_code == 403

    assert admin.get("/api/admin/settings").json["allow_registration"] is True
    assert admin.put("/api/admin/settings", json={"allow_registration": "no"}, headers=h).status_code == 400
    r = admin.put("/api/admin/settings", json={"allow_registration": False}, headers=h)
    assert r.json["allow_registration"] is False
    assert app.test_client().get("/api/auth/methods").json["registration"] is False
    assert register(app.test_client(), "frank").status_code == 403
    # Admins can still create accounts.
    r = admin.post("/api/users", json={"username": "frank", "password": "frankpass123"}, headers=h)
    assert r.status_code == 201

    admin.put("/api/admin/settings", json={"allow_registration": True}, headers=h)
    assert register(app.test_client(), "grace").status_code == 201


def test_activity_log(app):
    admin, alice = app.test_client(), app.test_client()
    register(alice)
    assert login(app.test_client(), "alice", "wrong-password").status_code == 401
    assert login(app.test_client(), "nobody\nevent=forged", "whatever1").status_code == 401
    alice.post("/api/items", json={"title": "milk"}, headers=csrf(alice))
    alice.post("/api/auth/logout")
    login(admin, "admin", "adminpass123")
    assert alice.get("/api/admin/activity").status_code == 401  # logged out

    data = admin.get("/api/admin/activity").json
    events = [(e["event"], e["username"], e["success"]) for e in data["entries"]]
    assert events == [
        ("auth.login", "admin", True),
        ("auth.logout", "alice", True),
        ("item.create", "alice", True),
        ("auth.login", "nobody\nevent=forged", False),
        ("auth.login", "alice", False),
        ("auth.register", "alice", True),
    ]
    assert data["entries"][4]["details"] == {"method": "local", "client": "browser", "reason": "invalid credentials"}
    assert {"auth.login", "item.create"} <= set(data["events"])

    q = admin.get("/api/admin/activity?user=ALICE&event=auth.&result=failure").json
    assert q["total"] == 1 and q["entries"][0]["event"] == "auth.login"
    assert admin.get("/api/admin/activity?result=maybe").status_code == 400
    assert admin.get("/api/admin/activity?limit=2").json["total"] == 6

    lines = open(app.config["ACTIVITY_LOG"], encoding="utf-8").read().splitlines()
    assert len(lines) == 6
    assert ' event=auth.login result=failure user="alice" ip="127.0.0.1" method="local"' in lines[1]
    # Hostile usernames stay on one line, quoted.
    assert 'user="nobody\\nevent=forged"' in lines[2]

    # Entries survive the deletion of the user they belong to.
    alice_id = [u["id"] for u in admin.get("/api/users").json["users"] if u["username"] == "alice"][0]
    admin.delete(f"/api/users/{alice_id}", headers=csrf(admin))
    entries = admin.get("/api/admin/activity?user=alice").json["entries"]
    assert entries and all(e["user_id"] is None for e in entries)
    deleted = admin.get("/api/admin/activity?event=user.delete").json["entries"][0]
    assert deleted["username"] == "admin" and deleted["details"]["target"] == "alice"


# ---------------------------------------------------------------- PAM + terminal tickets

class FakePrivd:
    """Stands in for homeacces-privd: one system account 'pi' (password 'raspberry')."""

    def __init__(self):
        self.groups = ["pi", "homeacces-pam", "sudo"]
        self.account_ok = True
        self.calls = []

    def __call__(self, op, **params):
        self.calls.append(op)
        user = params.get("username")
        if op == "pam_auth" and user == "pi" and params.get("password") == "raspberry":
            return {"ok": True, "username": "pi", "groups": self.groups}
        if op == "pam_account" and user == "pi" and self.account_ok:
            return {"ok": True, "username": "pi", "groups": self.groups}
        return {"ok": False, "error": "denied"}


@pytest.fixture
def privd(app, monkeypatch):
    import app.privd as privd_module
    fake = FakePrivd()
    monkeypatch.setattr(privd_module, "call", fake)
    app.config["PAM_ENABLED"] = True
    return fake


def pam_login(client, username="pi", password="raspberry"):
    return client.post("/api/auth/login", json={"username": username, "password": password, "method": "pam"})


def test_pam_disabled_by_default(client):
    assert client.get("/api/auth/methods").json["pam"] is False
    assert pam_login(client).status_code == 400
    register(client)
    assert client.post("/api/terminal/ticket", headers=csrf(client)).status_code == 404


def test_pam_login_creates_user_with_role_from_groups(client, privd):
    r = pam_login(client)
    assert r.status_code == 200
    assert r.json["session"] == {"method": "pam"}
    assert r.json["user"]["auth_source"] == "pam"
    assert r.json["user"]["role"] == "admin"  # member of sudo
    assert client.get("/api/auth/me").json["session"]["method"] == "pam"

    assert pam_login(client, password="wrong").status_code == 401
    # A PAM account cannot be used with the local method (its password_hash is unusable).
    assert login(client, "pi", "raspberry").status_code == 401

    privd.groups = ["pi", "homeacces-pam"]
    assert pam_login(client).json["user"]["role"] == "user"


def test_pam_refresh_rechecks_account(client, privd):
    pam_login(client)
    assert client.post("/api/auth/refresh", headers=csrf(client)).status_code == 200
    assert privd.calls[-1] == "pam_account"

    privd.account_ok = False
    assert client.post("/api/auth/refresh", headers=csrf(client)).status_code == 401


def test_pam_cannot_take_over_local_account(app, privd):
    alice = app.test_client()
    with app.app_context():
        get_db().execute("INSERT INTO users (username, password_hash) VALUES ('pi', ?)", (hash_password("x" * 10),))
        get_db().commit()
    assert pam_login(alice).status_code == 409


def test_system_usernames_reserved_for_local_registration(client, privd, monkeypatch):
    import app.auth as auth_module

    class FakePwd:
        @staticmethod
        def getpwnam(name):
            if name == "piuser":
                return object()
            raise KeyError(name)

    monkeypatch.setattr(auth_module, "pwd", FakePwd)
    r = register(client, "piuser")
    # Anonymous callers cannot tell a system account from a taken name.
    assert r.status_code == 409 and r.json["error"] == "username already taken"
    assert register(client, "alice").status_code == 201


def test_terminal_ticket_requires_pam_session(app, privd):
    import jwt as pyjwt

    local, pam = app.test_client(), app.test_client()
    register(local)
    assert local.post("/api/terminal/ticket", headers=csrf(local)).status_code == 403

    pam_login(pam)
    assert pam.post("/api/terminal/ticket").status_code == 403  # missing CSRF header
    r = pam.post("/api/terminal/ticket", headers=csrf(pam))
    assert r.status_code == 200
    claims = pyjwt.decode(r.json["ticket"], app.config["SECRET_KEY"], algorithms=["HS256"])
    assert claims["type"] == "terminal" and claims["pam_user"] == "pi"
    # A ticket is not an access token.
    assert pam.get("/api/items", headers={"Authorization": f"Bearer {r.json['ticket']}"}).status_code == 401


def test_pam_users_managed_by_system(app, privd):
    pam = app.test_client()
    uid = pam_login(pam).json["user"]["id"]
    h = csrf(pam)  # pi is admin through sudo
    assert pam.put(f"/api/users/{uid}", json={"role": "user"}, headers=h).status_code == 400
    assert pam.put("/api/auth/password", json={"current_password": "raspberry", "new_password": "newpass1234"},
                   headers=h).status_code == 400


def test_migrates_old_database(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, "
                 "password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL DEFAULT '')")
    conn.execute("INSERT INTO users (username, password_hash) VALUES ('old', 'h')")
    conn.execute("CREATE TABLE refresh_tokens (jti TEXT PRIMARY KEY, user_id INTEGER NOT NULL, "
                 "expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)")
    conn.commit()
    conn.close()
    app = create_app({"TESTING": True, "SECRET_KEY": "x" * 48, "DATABASE": str(path)})
    with app.app_context():
        init_db()
        assert tuple(get_db().execute("SELECT auth_source, avatar FROM users").fetchone()) == ("local", None)
        columns = {r["name"] for r in get_db().execute("PRAGMA table_info(refresh_tokens)")}
        assert {"session_id", "rotated_at"} <= columns


def _png(width=60, height=60):
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _jpeg_header(width=60, height=60):
    import struct
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_avatar(client):
    import base64
    register(client)
    h = csrf(client)
    assert client.get("/api/auth/me").json["user"]["avatar"] is None

    url = "data:image/png;base64," + base64.b64encode(_png()).decode()
    r = client.put("/api/auth/avatar", json={"avatar": url}, headers=h)
    assert r.status_code == 200 and r.json["user"]["avatar"] == url
    assert client.get("/api/auth/me").json["user"]["avatar"] == url

    jpeg = base64.b64encode(_jpeg_header()).decode()
    r = client.put("/api/auth/avatar", json={"avatar": jpeg}, headers=h)
    assert r.json["user"]["avatar"].startswith("data:image/jpeg;base64,")

    for bad in (base64.b64encode(_png(64, 64)).decode(), base64.b64encode(_jpeg_header(60, 61)).decode(),
                base64.b64encode(b"GIF89a....").decode(), "not base64!", 42):
        assert client.put("/api/auth/avatar", json={"avatar": bad}, headers=h).status_code == 400
    too_big = base64.b64encode(_png()[:-12] + b"\x00" * 40000).decode()
    assert client.put("/api/auth/avatar", json={"avatar": too_big}, headers=h).status_code in (400, 413)

    assert client.delete("/api/auth/avatar", headers=h).json["user"]["avatar"] is None


def test_item_types(app):
    admin, alice = app.test_client(), app.test_client()
    login(admin, "admin", "adminpass123")
    register(alice)
    h = csrf(admin)

    book = {"name": "Book", "fields": [
        {"name": "pages", "type": "int", "default": 0},
        {"name": "price", "type": "float", "default": 9.5},
        {"name": "read", "type": "boolean", "default": False},
        {"name": "author", "type": "string", "default": ""},
    ]}
    # Only admins define types.
    assert alice.post("/api/item-types", json=book, headers=csrf(alice)).status_code == 403
    r = admin.post("/api/item-types", json=book, headers=h)
    assert r.status_code == 201
    book_type = r.json["item_type"]
    assert book_type["table"] == f"item_type_{book_type['id']}"
    assert [f["name"] for f in book_type["fields"]] == ["pages", "price", "read", "author"]
    assert admin.post("/api/item-types", json={**book, "name": "book"}, headers=h).status_code == 409

    for bad in (
        {"name": "X", "fields": [{"name": "Bad Name", "type": "int", "default": 0}]},
        {"name": "X", "fields": [{"name": "item_id", "type": "int", "default": 0}]},
        {"name": "X", "fields": [{"name": "a", "type": "date", "default": 0}]},
        {"name": "X", "fields": [{"name": "a", "type": "int", "default": "1"}]},
        {"name": "X", "fields": [{"name": "a", "type": "int", "default": True}]},
        {"name": "X", "fields": [{"name": "a", "type": "int", "default": 1}] * 2},
    ):
        assert admin.post("/api/item-types", json=bad, headers=h).status_code == 400, bad

    with app.app_context():
        columns = {r["name"]: r["type"] for r in get_db().execute(f"PRAGMA table_info({book_type['table']})")}
    assert columns == {"item_id": "INTEGER", "pages": "INTEGER", "price": "REAL", "read": "INTEGER", "author": "TEXT"}

    # Everyone can use the types; missing fields get their default.
    assert [t["name"] for t in alice.get("/api/item-types").json["item_types"]] == ["Book"]
    r = alice.post("/api/items", json={"title": "Dune", "type_id": book_type["id"],
                                       "fields": {"pages": 412, "author": "Herbert"}}, headers=csrf(alice))
    assert r.status_code == 201
    item = r.json["item"]
    assert item["type_name"] == "Book"
    assert item["fields"] == {"pages": 412, "price": 9.5, "read": False, "author": "Herbert"}
    assert alice.post("/api/items", json={"title": "x", "type_id": book_type["id"], "fields": {"pages": "many"}},
                      headers=csrf(alice)).status_code == 400
    assert alice.post("/api/items", json={"title": "x", "type_id": book_type["id"], "fields": {"nope": 1}},
                      headers=csrf(alice)).status_code == 400
    assert alice.post("/api/items", json={"title": "x", "type_id": 999}, headers=csrf(alice)).status_code == 400
    plain = alice.post("/api/items", json={"title": "plain"}, headers=csrf(alice)).json["item"]
    assert plain["type_id"] is None and plain["fields"] is None

    # Updating keeps fields that are not sent; the type is fixed.
    r = alice.put(f"/api/items/{item['id']}", json={"title": "Dune", "done": True, "fields": {"read": True}},
                  headers=csrf(alice))
    assert r.json["item"]["fields"] == {"pages": 412, "price": 9.5, "read": True, "author": "Herbert"}
    assert alice.put(f"/api/items/{item['id']}", json={"title": "Dune", "type_id": None},
                     headers=csrf(alice)).status_code == 400

    listing = alice.get(f"/api/items?type_id={book_type['id']}").json
    assert listing["total"] == 1 and listing["items"][0]["fields"]["author"] == "Herbert"
    assert alice.get("/api/items?type_id=none").json["items"][0]["title"] == "plain"

    # Edit the type: rename a field, drop one, add one (existing items get its default), change a default.
    fields = {f["name"]: f for f in book_type["fields"]}
    r = admin.put(f"/api/item-types/{book_type['id']}", json={"name": "Books", "fields": [
        {"id": fields["pages"]["id"], "name": "page_count", "type": "int", "default": 100},
        {"id": fields["read"]["id"], "name": "read", "type": "boolean", "default": False},
        {"id": fields["author"]["id"], "name": "author", "type": "string", "default": ""},
        {"name": "rating", "type": "float", "default": 3.0},
    ]}, headers=h)
    assert r.status_code == 200, r.json
    assert r.json["item_type"]["name"] == "Books"
    assert r.json["item_type"]["item_count"] == 1
    assert alice.get(f"/api/items/{item['id']}").json["item"]["fields"] == {
        "page_count": 412, "read": True, "author": "Herbert", "rating": 3.0}

    # Swapping two names keeps each column's data.
    fields = {f["name"]: f for f in r.json["item_type"]["fields"]}
    r = admin.put(f"/api/item-types/{book_type['id']}", json={"name": "Books", "fields": [
        {"id": fields["author"]["id"], "name": "read", "type": "string", "default": ""},
        {"id": fields["read"]["id"], "name": "author", "type": "boolean", "default": False},
    ]}, headers=h)
    assert r.status_code == 200, r.json
    assert alice.get(f"/api/items/{item['id']}").json["item"]["fields"] == {"read": "Herbert", "author": True}
    # A field's type cannot change.
    assert admin.put(f"/api/item-types/{book_type['id']}", json={"name": "Books", "fields": [
        {"id": fields["author"]["id"], "name": "read", "type": "int", "default": 0},
    ]}, headers=h).status_code == 400

    # A type in use cannot be deleted; deleting its items frees it and drops its table.
    assert admin.delete(f"/api/item-types/{book_type['id']}", headers=h).status_code == 409
    assert alice.delete(f"/api/items/{item['id']}", headers=csrf(alice)).status_code == 204
    assert admin.delete(f"/api/item-types/{book_type['id']}", headers=h).status_code == 204
    with app.app_context():
        assert get_db().execute("SELECT name FROM sqlite_master WHERE name = ?",
                                (book_type["table"],)).fetchone() is None


# ---------------------------------------------------------------- SSH menu

KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----"


def test_ssh_profiles_crud(client):
    register(client)
    h = csrf(client)
    assert client.get("/api/ssh/profiles").json == {"profiles": [], "enabled": True}

    r = client.post("/api/ssh/profiles", json={"name": "Pi", "host": "Pi.Local", "username": "pi"}, headers=h)
    assert r.status_code == 201
    profile = r.json["profile"]
    assert profile["host"] == "pi.local" and profile["port"] == 22 and profile["has_key"] is False
    pid = profile["id"]
    dup = client.post("/api/ssh/profiles", json={"name": "Pi", "host": "x", "username": "pi"}, headers=h)
    assert dup.status_code == 409

    # The key is write-only: only has_key is ever returned. Omitting it keeps it, null removes it.
    body = {"name": "Pi", "host": "[2001:DB8::1]", "port": 2222, "username": "pi", "private_key": KEY + "\r\n"}
    r = client.put(f"/api/ssh/profiles/{pid}", json=body, headers=h)
    assert r.json["profile"]["has_key"] is True and r.json["profile"]["host"] == "2001:db8::1"
    assert KEY not in r.get_data(as_text=True) and KEY not in client.get("/api/ssh/profiles").get_data(as_text=True)
    del body["private_key"]
    assert client.put(f"/api/ssh/profiles/{pid}", json=body, headers=h).json["profile"]["has_key"] is True
    body["private_key"] = None
    assert client.put(f"/api/ssh/profiles/{pid}", json=body, headers=h).json["profile"]["has_key"] is False

    assert client.delete(f"/api/ssh/profiles/{pid}", headers=h).status_code == 204
    assert client.get("/api/ssh/profiles").json["profiles"] == []


@pytest.mark.parametrize("change", [
    {"host": "-oProxyCommand=touch /tmp/x"}, {"host": "a b"}, {"host": "fe80::1%eth0"}, {"host": "user@host"},
    {"host": ""}, {"port": 0}, {"port": 70000}, {"port": "22"}, {"port": True},
    {"username": "-oProxyCommand=x"}, {"username": "%u"}, {"username": "a$b"}, {"username": ""},
    {"name": ""}, {"name": "x" * 65}, {"private_key": "not a key"}, {"private_key": KEY + "\n" + "A" * 17000},
])
def test_ssh_profile_validation(client, change):
    register(client)
    body = {"name": "ok", "host": "example.org", "port": 22, "username": "pi", **change}
    assert client.post("/api/ssh/profiles", json=body, headers=csrf(client)).status_code == 400


def test_ssh_profiles_are_private(app):
    alice, admin = app.test_client(), app.test_client()
    register(alice)
    login(admin, "admin", "adminpass123")
    pid = alice.post("/api/ssh/profiles", json={"name": "a", "host": "h", "username": "u"},
                     headers=csrf(alice)).json["profile"]["id"]
    # Not even admins can see, use or change someone else's connections.
    assert admin.get("/api/ssh/profiles").json["profiles"] == []
    assert admin.post("/api/ssh/ticket", json={"profile_id": pid}, headers=csrf(admin)).status_code == 404
    body = {"name": "a", "host": "evil", "username": "u"}
    assert admin.put(f"/api/ssh/profiles/{pid}", json=body, headers=csrf(admin)).status_code == 404
    assert admin.delete(f"/api/ssh/profiles/{pid}", headers=csrf(admin)).status_code == 404


def test_ssh_ticket(app):
    import jwt as pyjwt

    alice, admin = app.test_client(), app.test_client()
    register(alice)  # local accounts get SSH too (unlike the PAM-only local terminal)
    pid = alice.post("/api/ssh/profiles", json={"name": "a", "host": "h", "username": "u"},
                     headers=csrf(alice)).json["profile"]["id"]
    assert alice.post("/api/ssh/ticket", json={"profile_id": pid}).status_code == 403  # missing CSRF header
    assert alice.post("/api/ssh/ticket", json={"profile_id": "1"}, headers=csrf(alice)).status_code == 400
    r = alice.post("/api/ssh/ticket", json={"profile_id": pid}, headers=csrf(alice))
    assert r.status_code == 200
    claims = pyjwt.decode(r.json["ticket"], app.config["SECRET_KEY"], algorithms=["HS256"])
    assert claims["type"] == "ssh" and claims["profile_id"] == pid
    assert alice.get("/api/items", headers={"Authorization": f"Bearer {r.json['ticket']}"}).status_code == 401

    # Admins can switch the SSH menu off for everyone.
    login(admin, "admin", "adminpass123")
    assert alice.put("/api/admin/settings", json={"ssh_enabled": False}, headers=csrf(alice)).status_code == 403
    r = admin.put("/api/admin/settings", json={"ssh_enabled": False}, headers=csrf(admin))
    assert r.json["ssh_enabled"] is False
    assert alice.get("/api/ssh/profiles").json["enabled"] is False
    assert alice.post("/api/ssh/ticket", json={"profile_id": pid}, headers=csrf(alice)).status_code == 403
    events = [e["event"] for e in admin.get("/api/admin/activity?event=ssh.").json["entries"]]
    assert events == ["ssh.open", "ssh.profile_create"]


def test_ssh_forget_host_key(app):
    from app.ssh import known_hosts_path

    alice, admin = app.test_client(), app.test_client()
    register(alice)
    h = csrf(alice)
    p22 = alice.post("/api/ssh/profiles", json={"name": "a", "host": "pi.local", "username": "u"},
                     headers=h).json["profile"]
    p2222 = alice.post("/api/ssh/profiles", json={"name": "b", "host": "pi.local", "port": 2222, "username": "u"},
                       headers=h).json["profile"]
    assert alice.delete(f"/api/ssh/profiles/{p22['id']}/host-key", headers=h).json == {"removed": 0}

    alice_id = alice.get("/api/auth/me").json["user"]["id"]
    with app.app_context():
        path = known_hosts_path(alice_id)
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("# comment\npi.local ssh-ed25519 AAAA1\n[pi.local]:2222 ssh-ed25519 AAAA2\n"
                "other,PI.LOCAL ecdsa-sha2-nistp256 AAAA3\n@revoked pi.local ssh-rsa AAAA4\n")
    assert alice.delete(f"/api/ssh/profiles/{p22['id']}/host-key", headers=h).json == {"removed": 3}
    with open(path) as f:
        assert f.read() == "# comment\n[pi.local]:2222 ssh-ed25519 AAAA2\n"
    assert alice.delete(f"/api/ssh/profiles/{p2222['id']}/host-key", headers=h).json == {"removed": 1}

    # Deleting the user forgets its host keys.
    login(admin, "admin", "adminpass123")
    assert admin.delete(f"/api/users/{alice_id}", headers=csrf(admin)).status_code == 204
    assert not os.path.exists(path)
