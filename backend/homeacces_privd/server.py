"""homeacces-privd: the privileged helper (runs as root, see deploy/systemd/homeacces-privd.service).

It keeps root-only work out of the unprivileged web API:

* Unix socket (HOMEACCES_PRIVD_SOCKET, mode 0660, group homeacces): PAM password authentication and
  account checks, answered with the user's groups. Only the `homeacces` user (or root) may connect.
* WebSocket on 127.0.0.1:8001, path /api/terminal/ws (proxied by nginx): opens a login shell
  for a PAM user in a PTY, as that user, after verifying a single-use ticket issued by the API
  (POST /api/terminal/ticket). The shell has exactly the rights the user has over SSH.
  The WebSocket ↔ PTY bridge itself is shared with homeacces_ssh (see webterm.py).

Policy (enforced here, not in the API): the account must have uid >= HOMEACCES_PAM_MIN_UID and be a
member of HOMEACCES_PAM_GROUP.
"""
import asyncio
import grp
import json
import logging
import os
import pwd
import re
import shutil
import signal
import socket
import struct

from websockets.asyncio.server import serve

import webterm
from webterm import Denied

from . import pam

log = logging.getLogger("homeacces-privd")

PAM_SERVICE = "homeacces"
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
WS_PATH = "/api/terminal/ws"


class Settings:
    def __init__(self, env=os.environ):
        self.secret_key = env.get("HOMEACCES_SECRET_KEY", "")
        if len(self.secret_key) < 32:
            raise SystemExit("HOMEACCES_SECRET_KEY must be set (at least 32 characters)")
        self.socket_path = env.get("HOMEACCES_PRIVD_SOCKET", "/run/homeacces/privd.sock")
        self.client_user = env.get("HOMEACCES_PRIVD_CLIENT_USER", "homeacces")
        self.pam_group = env.get("HOMEACCES_PAM_GROUP", "homeacces-pam")
        self.min_uid = int(env.get("HOMEACCES_PAM_MIN_UID", "1000"))
        listen = env.get("HOMEACCES_TERMINAL_LISTEN", "127.0.0.1:8001")
        self.listen_host, _, port = listen.rpartition(":")
        self.listen_port = int(port)
        self.idle_seconds = int(env.get("HOMEACCES_TERMINAL_IDLE_MIN", "30")) * 60
        self.max_per_user = int(env.get("HOMEACCES_TERMINAL_MAX_PER_USER", "4"))
        self.max_total = int(env.get("HOMEACCES_TERMINAL_MAX_TOTAL", "16"))


# ---------------------------------------------------------------- account policy

def account_info(settings, username):
    """Return {"username", "groups"} if the account may use HomeAcces, else raise Denied."""
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise Denied("invalid username")
    try:
        pw = pwd.getpwnam(username)
    except KeyError:
        raise Denied("unknown user") from None
    if pw.pw_uid < settings.min_uid:
        raise Denied("system accounts are not allowed")
    groups = set()
    for gid in os.getgrouplist(username, pw.pw_gid):
        try:
            groups.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            pass
    if settings.pam_group and settings.pam_group not in groups:
        raise Denied(f"not a member of {settings.pam_group}")
    return {"username": username, "groups": sorted(groups)}


# ---------------------------------------------------------------- unix socket (API → privd)

class PrivdProtocol:
    def __init__(self, settings):
        self.settings = settings
        self.allowed_uids = {0}
        try:
            self.allowed_uids.add(pwd.getpwnam(settings.client_user).pw_uid)
        except KeyError:
            log.warning("client user %s does not exist; only root may use the socket", settings.client_user)
        self.pam_slots = asyncio.Semaphore(4)

    def _peer_uid(self, writer):
        sock = writer.get_extra_info("socket")
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", creds)[1]

    async def handle(self, reader, writer):
        try:
            if self._peer_uid(writer) not in self.allowed_uids:
                resp = {"ok": False, "error": "forbidden"}
            else:
                line = await asyncio.wait_for(reader.readline(), 5)
                resp = await self._dispatch(json.loads(line))
        except Exception as exc:  # malformed request, timeout, oversized line
            log.warning("bad privd request: %s", exc)
            resp = {"ok": False, "error": "bad request"}
        try:
            writer.write(json.dumps(resp).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()

    async def _dispatch(self, req):
        op, username = req.get("op"), req.get("username")
        if op == "pam_auth":
            password = req.get("password")
            if not isinstance(password, str) or not isinstance(username, str):
                return {"ok": False, "error": "bad request"}
            async with self.pam_slots:
                # Authenticate before applying policy so timing does not reveal which accounts exist.
                ok = USERNAME_RE.match(username) and await asyncio.to_thread(
                    pam.authenticate, PAM_SERVICE, username, password)
            return self._with_policy(username, ok, "pam_auth")
        if op == "pam_account":
            ok = isinstance(username, str) and USERNAME_RE.match(username) and await asyncio.to_thread(
                pam.account_ok, PAM_SERVICE, username)
            return self._with_policy(username, ok, "pam_account")
        return {"ok": False, "error": "unknown op"}

    def _with_policy(self, username, pam_ok, op):
        if not pam_ok:
            log.info("%s failed for %r", op, username)
            return {"ok": False, "error": "denied"}
        try:
            info = account_info(self.settings, username)
        except Denied as exc:
            log.info("%s denied for %r: %s", op, username, exc)
            return {"ok": False, "error": "denied"}
        return {"ok": True, **info}


# ---------------------------------------------------------------- terminal (browser → privd)

def shell_command(username):
    """Login shell for `username` on the current (PTY) stdin as controlling terminal."""
    setsid = shutil.which("setsid") or "/usr/bin/setsid"
    runuser = shutil.which("runuser", path="/usr/sbin:/usr/bin:/sbin:/bin") or "/usr/sbin/runuser"
    return [setsid, "--ctty", runuser, "--login", username]


class TerminalServer(webterm.TerminalServer):
    path = WS_PATH
    ticket_type = "terminal"
    ticket_claims = ("pam_user",)

    def __init__(self, settings):
        super().__init__(settings.secret_key, settings.idle_seconds, settings.max_per_user, settings.max_total)
        self.settings = settings

    def session_key(self, claims):
        return claims["pam_user"]

    async def prepare(self, claims):
        username = claims["pam_user"]
        account_info(self.settings, username)
        if not await asyncio.to_thread(pam.account_ok, PAM_SERVICE, username):
            raise Denied("account not valid")
        env = {"TERM": "xterm-256color", "LANG": os.environ.get("LANG", "C.UTF-8")}
        return shell_command(username), env, None


# ---------------------------------------------------------------- entry point

async def main():
    settings = Settings()

    if os.path.exists(settings.socket_path):
        os.unlink(settings.socket_path)
    os.makedirs(os.path.dirname(settings.socket_path), exist_ok=True)
    protocol = PrivdProtocol(settings)
    unix_server = await asyncio.start_unix_server(protocol.handle, path=settings.socket_path, limit=16384)
    try:
        gid = grp.getgrnam(settings.client_user).gr_gid
    except KeyError:
        gid = -1
    if os.geteuid() == 0:
        os.chown(settings.socket_path, 0, gid)
    os.chmod(settings.socket_path, 0o660)

    terminals = TerminalServer(settings)
    stop = asyncio.get_running_loop().create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))

    async with unix_server, serve(terminals.handle, settings.listen_host, settings.listen_port,
                                  max_size=256 * 1024, ping_interval=30, ping_timeout=30):
        log.info("listening on %s and ws://%s:%d%s", settings.socket_path,
                 settings.listen_host, settings.listen_port, WS_PATH)
        await stop
