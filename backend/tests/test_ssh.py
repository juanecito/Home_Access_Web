"""Tests for homeacces-ssh. The SSH client is replaced by a small script where a server would be
needed; one test runs the real OpenSSH client to check that it accepts the command line."""
import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import stat
import time
import uuid

import jwt
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from homeacces_ssh import server
from webterm import Denied

SECRET = "s" * 48
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"


@pytest.fixture
def settings(tmp_path):
    db = sqlite3.connect(tmp_path / "db.sqlite3")
    with open(os.path.join(os.path.dirname(__file__), "..", "app", "schema.sql")) as f:
        db.executescript(f.read())
    db.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'alice', 'x'), (2, 'bob', 'x')")
    db.execute("INSERT INTO ssh_profiles (id, owner_id, name, host, port, username, private_key) VALUES "
               "(1, 1, 'pi', 'pi.local', 2222, 'pi', NULL), (2, 1, 'key', '127.0.0.1', 1, 'root', ?)", (KEY,))
    db.commit()
    db.close()
    s = server.Settings({"HOMEACCES_SECRET_KEY": SECRET, "HOMEACCES_DATABASE": str(tmp_path / "db.sqlite3")})
    os.makedirs(os.path.join(s.ssh_dir, "known_hosts"))
    return s


def ticket(user_id=1, profile_id=1, ttype="ssh"):
    now = int(time.time())
    return jwt.encode({"type": ttype, "sub": str(user_id), "profile_id": profile_id, "jti": uuid.uuid4().hex,
                       "iat": now, "exp": now + 30}, SECRET, algorithm="HS256")


def test_ssh_command_is_locked_down(settings):
    argv = server.ssh_command(settings, 7, {"host": "pi.local", "port": 2222, "username": "pi"})
    assert argv[1:5] == ["-F", "/dev/null", "-e", "none"]
    assert argv[-6:] == ["-p", "2222", "-l", "pi", "--", "pi.local"]
    opts = dict(a.split("=", 1) for a in argv[argv.index("-o"):-6] if a != "-o")
    assert opts["UserKnownHostsFile"] == f'"{settings.ssh_dir}/known_hosts/7"'
    assert opts["IdentityFile"] == "none" and opts["IdentityAgent"] == "none"
    assert opts["ClearAllForwardings"] == "yes" and opts["PermitLocalCommand"] == "no"


def test_prepare(settings, tmp_path):
    srv = server.SshServer(settings, str(tmp_path))
    with pytest.raises(Denied):
        srv.verify_ticket(ticket(ttype="terminal"))
    with pytest.raises(Denied, match="not found"):  # profile of another user
        asyncio.run(srv.prepare(srv.verify_ticket(ticket(user_id=2))))

    argv, env, cleanup = asyncio.run(srv.prepare(srv.verify_ticket(ticket())))
    assert cleanup is None and "IdentityFile=none" in argv and env["TERM"] == "xterm-256color"

    # The key is written to a private file for the session, then removed.
    argv, env, cleanup = asyncio.run(srv.prepare(srv.verify_ticket(ticket(profile_id=2))))
    key_path = argv[argv.index("-o", argv.index("IdentityAgent=none")) + 1].split("=", 1)[1].strip('"')
    assert os.path.dirname(key_path) == str(tmp_path)
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
    with open(key_path) as f:
        assert f.read() == KEY
    cleanup()
    assert not os.path.exists(key_path)


async def run_session(srv, profile_id=1):
    async with server.serve(srv.handle, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}/api/ssh/ws", origin=f"http://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"type": "auth", "ticket": ticket(profile_id=profile_id),
                                      "cols": 100, "rows": 30}))
            yield ws


def test_session_runs_client_in_pty(settings, tmp_path):
    fake = tmp_path / "fake-ssh"
    # Reads from /dev/tty like OpenSSH does for passwords: the PTY must be its controlling terminal.
    fake.write_text('#!/bin/sh\necho "ARGS $*"; stty size; read line </dev/tty; echo "GOT $line"\n')
    fake.chmod(0o755)
    settings.ssh_binary = str(fake)

    async def scenario():
        srv = server.SshServer(settings, str(tmp_path))
        async for ws in run_session(srv):
            await ws.send(json.dumps({"type": "input", "data": "hello\n"}))
            out = b""
            while b"GOT hello" not in out:
                out += await asyncio.wait_for(ws.recv(), 5)
            assert b"-- pi.local" in out and b"30 100" in out
            assert srv.sessions == {1: 1}
            await asyncio.wait_for(ws.wait_closed(), 5)
            assert ws.close_code == 1000

    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("ssh") is None, reason="OpenSSH client not installed")
def test_real_openssh_accepts_command_line(settings, tmp_path):
    # Profile 2 points at 127.0.0.1:1 with a key: OpenSSH must get as far as connecting.
    key_dir = tmp_path / "keys"
    key_dir.mkdir()

    async def scenario():
        srv = server.SshServer(settings, str(key_dir))
        async for ws in run_session(srv, profile_id=2):
            out = b""
            with contextlib.suppress(ConnectionClosed):
                while True:
                    out += await asyncio.wait_for(ws.recv(), 20)
            assert b"Connection refused" in out, out
            assert b"Bad configuration" not in out and b"unknown" not in out.lower(), out
        for _ in range(50):  # the key file is removed just after the close frame
            if not os.listdir(key_dir):
                break
            await asyncio.sleep(0.05)
        assert os.listdir(key_dir) == []

    asyncio.run(scenario())
