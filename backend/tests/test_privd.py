"""Tests for the privileged helper. They run unprivileged: PAM and the account policy are
stubbed where root would be needed, and the login shell is replaced by /bin/sh."""
import asyncio
import contextlib
import json
import os
import pwd
import time
import uuid

import jwt
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from homeacces_privd import pam, server

SECRET = "s" * 48


@pytest.fixture
def settings(tmp_path):
    return server.Settings({
        "HOMEACCES_SECRET_KEY": SECRET,
        "HOMEACCES_PRIVD_SOCKET": str(tmp_path / "privd.sock"),
        "HOMEACCES_TERMINAL_LISTEN": "127.0.0.1:0",
        "HOMEACCES_PAM_GROUP": "homeacces-pam",
    })


def ticket(user="pi", ttype="terminal", exp_in=30, jti=None):
    now = int(time.time())
    return jwt.encode({"type": ttype, "sub": "1", "pam_user": user, "jti": jti or uuid.uuid4().hex,
                       "iat": now, "exp": now + exp_in}, SECRET, algorithm="HS256")


def test_real_pam_rejects_bad_password():
    # Exercises the ctypes binding against the system libpam without needing root.
    assert pam.authenticate("homeacces", "no-such-user-xyz", "wrong") is False


def test_ticket_rules(settings):
    term = server.TerminalServer(settings)
    tok = ticket()
    assert term.verify_ticket(tok)["pam_user"] == "pi"
    with pytest.raises(server.Denied, match="already used"):
        term.verify_ticket(tok)
    with pytest.raises(server.Denied):
        term.verify_ticket(ticket(ttype="access"))
    with pytest.raises(server.Denied):
        term.verify_ticket(ticket(exp_in=-5))
    with pytest.raises(server.Denied):
        term.verify_ticket(jwt.encode({"type": "terminal"}, "other-key" * 8, algorithm="HS256"))


def test_account_policy(settings):
    me = pwd.getpwuid(os.getuid()).pw_name
    with pytest.raises(server.Denied):
        server.account_info(settings, "root")  # uid below HOMEACCES_PAM_MIN_UID
    with pytest.raises(server.Denied):
        server.account_info(settings, "-rf")
    with pytest.raises(server.Denied):
        server.account_info(settings, "no-such-user-xyz")
    settings.pam_group = ""
    settings.min_uid = 0
    assert server.account_info(settings, me)["username"] == me


def test_unix_socket_protocol(settings, monkeypatch):
    monkeypatch.setattr(server.pam, "authenticate", lambda svc, u, p: p == "good")
    monkeypatch.setattr(server, "account_info", lambda s, u: {"username": u, "groups": ["homeacces-pam"]})

    async def scenario():
        proto = server.PrivdProtocol(settings)
        proto.allowed_uids.add(os.getuid())
        srv = await asyncio.start_unix_server(proto.handle, path=settings.socket_path)
        async with srv:
            async def ask(req):
                reader, writer = await asyncio.open_unix_connection(settings.socket_path)
                writer.write(json.dumps(req).encode() + b"\n")
                reply = json.loads(await reader.readline())
                writer.close()
                return reply
            assert (await ask({"op": "pam_auth", "username": "pi", "password": "good"}))["ok"] is True
            assert (await ask({"op": "pam_auth", "username": "pi", "password": "bad"}))["ok"] is False
            assert (await ask({"op": "nope"}))["ok"] is False
            proto.allowed_uids = {0}
            assert (await ask({"op": "pam_auth", "username": "pi", "password": "good"}))["error"] == "forbidden"

    asyncio.run(scenario())


def test_terminal_session(settings, monkeypatch):
    monkeypatch.setattr(server, "account_info", lambda s, u: {"username": u, "groups": []})
    monkeypatch.setattr(server.pam, "account_ok", lambda svc, u: True)
    monkeypatch.setattr(server, "shell_command", lambda u: ["setsid", "--ctty", "/bin/sh"])

    async def scenario():
        term = server.TerminalServer(settings)
        async with server.serve(term.handle, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}/api/terminal/ws"
            origin = f"http://127.0.0.1:{port}"

            # Cross-site origin is refused.
            async with connect(url, origin="https://evil.example") as ws:
                with contextlib.suppress(ConnectionClosed):
                    await ws.send(json.dumps({"type": "auth", "ticket": ticket()}))
                await ws.wait_closed()
                assert ws.close_code == 1008 and ws.close_reason == "bad origin"

            async with connect(url, origin=origin) as ws:
                await ws.send(json.dumps({"type": "auth", "ticket": ticket(), "cols": 100, "rows": 30}))
                await ws.send(json.dumps({"type": "input", "data": "stty size; tty; echo MARK$((6*7))\n"}))
                out = b""
                while b"MARK42" not in out:
                    out += await asyncio.wait_for(ws.recv(), 5)
                assert b"30 100" in out  # initial window size applied
                assert b"/dev/pts/" in out  # running on a PTY
                assert term.sessions == {"pi": 1}

                await ws.send(json.dumps({"type": "input", "data": "exit\n"}))
                await asyncio.wait_for(ws.wait_closed(), 5)
            assert ws.close_code == 1000
            for _ in range(50):  # the server finishes cleanup just after sending the close frame
                if not term.sessions:
                    break
                await asyncio.sleep(0.05)
            assert term.sessions == {}

    asyncio.run(scenario())


def test_disconnect_kills_shell(settings, monkeypatch):
    monkeypatch.setattr(server, "account_info", lambda s, u: {"username": u, "groups": []})
    monkeypatch.setattr(server.pam, "account_ok", lambda svc, u: True)
    monkeypatch.setattr(server, "shell_command", lambda u: ["setsid", "--ctty", "/bin/sh"])

    async def scenario():
        term = server.TerminalServer(settings)
        async with server.serve(term.handle, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/api/terminal/ws", origin=f"http://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "auth", "ticket": ticket()}))
                await ws.send(json.dumps({"type": "input", "data": "echo PID=$$\n"}))
                out = b""
                while b"PID=" not in out.replace(b"echo PID=", b""):
                    out += await asyncio.wait_for(ws.recv(), 5)
                pid = int(out.rsplit(b"PID=", 1)[1].split()[0])
            # Client went away without "exit": the PTY hangup must end the shell.
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
                await asyncio.sleep(0.05)
            pytest.fail(f"shell {pid} still running after disconnect")

    asyncio.run(scenario())


def test_pending_handshakes_are_capped(settings, monkeypatch):
    monkeypatch.setattr(server.TerminalServer, "max_pending", 2)

    async def scenario():
        term = server.TerminalServer(settings)
        async with server.serve(term.handle, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}/api/terminal/ws"
            origin = f"http://127.0.0.1:{port}"
            # Two clients connect and never authenticate; a third is turned away at once.
            async with connect(url, origin=origin), connect(url, origin=origin):
                for _ in range(50):
                    if term.pending == 2:
                        break
                    await asyncio.sleep(0.02)
                async with connect(url, origin=origin) as ws:
                    await asyncio.wait_for(ws.wait_closed(), 5)
                    assert ws.close_code == 1013
            for _ in range(50):
                if term.pending == 0:
                    break
                await asyncio.sleep(0.05)
            assert term.pending == 0

    asyncio.run(scenario())
