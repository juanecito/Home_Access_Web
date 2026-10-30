"""homeacces-ssh: the SSH terminal of the SSH menu (runs as the unprivileged homeacces user,
see deploy/systemd/homeacces-ssh.service).

WebSocket on 127.0.0.1:8002, path /api/ssh/ws (proxied by nginx): runs the OpenSSH client in a PTY
for one of the user's saved connections (table ssh_profiles), after verifying a single-use ticket
issued by the API (POST /api/ssh/ticket). The WebSocket ↔ PTY bridge is shared with the local
terminal of homeacces-privd (see webterm.py).

The client is locked down so that a web user gets an SSH session and nothing else on this machine:
no ssh_config (-F /dev/null), no escape character (so no ~C command line), no port, agent or X11
forwarding, no local commands, no agent, only the profile's own key, and one known_hosts file per
web user (unknown host keys are confirmed by the user in the terminal, changed ones are refused).
"""
import asyncio
import logging
import os
import shutil
import signal
import sqlite3
import tempfile

from websockets.asyncio.server import serve

import webterm
from webterm import Denied

log = logging.getLogger("homeacces-ssh")

WS_PATH = "/api/ssh/ws"
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Settings:
    def __init__(self, env=os.environ):
        self.secret_key = env.get("HOMEACCES_SECRET_KEY", "")
        if len(self.secret_key) < 32:
            raise SystemExit("HOMEACCES_SECRET_KEY must be set (at least 32 characters)")
        # Same defaults as the API (app/config.py).
        self.database = env.get("HOMEACCES_DATABASE", os.path.join(BASE_DIR, "instance", "homeacces.sqlite3"))
        self.ssh_dir = env.get("HOMEACCES_SSH_DIR", os.path.join(os.path.dirname(self.database), "ssh"))
        if any(c in self.ssh_dir for c in ' \t"%'):
            raise SystemExit("HOMEACCES_SSH_DIR must not contain spaces, quotes or '%'")
        listen = env.get("HOMEACCES_SSH_LISTEN", "127.0.0.1:8002")
        self.listen_host, _, port = listen.rpartition(":")
        self.listen_port = int(port)
        self.idle_seconds = int(env.get("HOMEACCES_TERMINAL_IDLE_MIN", "30")) * 60
        self.max_per_user = int(env.get("HOMEACCES_SSH_MAX_PER_USER", "4"))
        self.max_total = int(env.get("HOMEACCES_SSH_MAX_TOTAL", "16"))
        self.ssh_binary = shutil.which("ssh") or "/usr/bin/ssh"
        self.setsid = shutil.which("setsid") or "/usr/bin/setsid"

    def known_hosts(self, user_id):
        return os.path.join(self.ssh_dir, "known_hosts", str(int(user_id)))


def ssh_command(settings, user_id, profile, key_path=None):
    """The OpenSSH client command line for a profile row. Only validated values reach it
    (see app/ssh.py), and the destination comes after "--"."""
    options = [
        ("UserKnownHostsFile", f'"{settings.known_hosts(user_id)}"'),
        ("GlobalKnownHostsFile", "none"),
        ("StrictHostKeyChecking", "ask"),
        ("IdentitiesOnly", "yes"),
        ("IdentityAgent", "none"),
        ("IdentityFile", f'"{key_path}"' if key_path else "none"),
        ("ClearAllForwardings", "yes"),
        ("ForwardAgent", "no"),
        ("ForwardX11", "no"),
        ("PermitLocalCommand", "no"),
        ("ControlMaster", "no"),
        ("ControlPath", "none"),
        ("ConnectTimeout", "15"),
        ("ServerAliveInterval", "30"),
    ]
    argv = [settings.ssh_binary, "-F", "/dev/null", "-e", "none"]
    for name, value in options:
        argv += ["-o", f"{name}={value}"]
    return argv + ["-p", str(profile["port"]), "-l", profile["username"], "--", profile["host"]]


class SshServer(webterm.TerminalServer):
    path = WS_PATH
    ticket_type = "ssh"
    ticket_claims = ("profile_id",)

    def __init__(self, settings, key_dir):
        super().__init__(settings.secret_key, settings.idle_seconds, settings.max_per_user, settings.max_total)
        self.settings = settings
        self.key_dir = key_dir  # private keys are written here (mode 0600) while their session runs

    def session_key(self, claims):
        return int(claims["sub"])

    def load_profile(self, user_id, profile_id):
        conn = sqlite3.connect(self.settings.database, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT p.* FROM ssh_profiles p JOIN users u ON u.id = p.owner_id "
                "WHERE p.id = ? AND p.owner_id = ?", (profile_id, user_id)).fetchone()
        finally:
            conn.close()

    async def prepare(self, claims):
        user_id = int(claims["sub"])
        profile = await asyncio.to_thread(self.load_profile, user_id, claims["profile_id"])
        if profile is None:
            raise Denied("SSH connection not found")
        key_path = cleanup = None
        if profile["private_key"]:
            fd, key_path = tempfile.mkstemp(prefix="key-", dir=self.key_dir)  # mode 0600
            with os.fdopen(fd, "w") as f:
                f.write(profile["private_key"])
            cleanup = lambda: os.unlink(key_path)  # noqa: E731
        env = {"TERM": "xterm-256color", "LANG": os.environ.get("LANG", "C.UTF-8")}
        log.info("ssh user_id=%s to %s@%s:%s", user_id, profile["username"], profile["host"], profile["port"])
        # The PTY must be ssh's controlling terminal: it reads passwords and host key answers from /dev/tty.
        argv = [self.settings.setsid, "--ctty", *ssh_command(self.settings, user_id, profile, key_path)]
        return argv, env, cleanup


# ---------------------------------------------------------------- entry point

async def main():
    settings = Settings()
    os.makedirs(os.path.join(settings.ssh_dir, "known_hosts"), mode=0o700, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="homeacces-ssh-") as key_dir:
        server = SshServer(settings, key_dir)
        stop = asyncio.get_running_loop().create_future()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))

        async with serve(server.handle, settings.listen_host, settings.listen_port,
                         max_size=256 * 1024, ping_interval=30, ping_timeout=30):
            log.info("listening on ws://%s:%d%s (client %s)", settings.listen_host, settings.listen_port,
                     WS_PATH, settings.ssh_binary)
            await stop
