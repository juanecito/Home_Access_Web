"""WebSocket ↔ PTY bridge shared by the web terminals (homeacces_privd: local shell, homeacces_ssh: SSH client).

Protocol (one WebSocket per terminal):
  client → server: JSON text {type: "auth", ticket, cols, rows} first, then
                   {type: "input", data} and {type: "resize", cols, rows}
  server → client: binary frames with raw terminal output; close reason on exit.

The ticket is a short-lived single-use JWT issued by the API (cookie + CSRF protected), so the
WebSocket cannot be hijacked cross-site. Subclasses decide what a ticket allows to run.
"""
import asyncio
import fcntl
import json
import logging
import os
import struct
import termios
import time
from urllib.parse import urlsplit

import jwt
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("homeacces-webterm")


class Denied(Exception):
    pass


def set_winsize(fd, rows, cols):
    rows = min(max(int(rows), 2), 500)
    cols = min(max(int(cols), 2), 1000)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class TerminalServer:
    """Accepts terminal WebSockets on `path` for tickets of type `ticket_type`.

    Subclasses implement session_key() (what the session limits count) and prepare()
    (checks the ticket's rights and returns the command to run in the PTY)."""

    path = None
    ticket_type = None
    ticket_claims = ()  # claims required in addition to exp, iat, jti, sub, type
    # Connections still in the handshake (no valid ticket yet); each may wait up to 10 s.
    max_pending = 32

    def __init__(self, secret_key, idle_seconds, max_per_user, max_total):
        self.secret_key = secret_key
        self.idle_seconds = idle_seconds
        self.max_per_user = max_per_user
        self.max_total = max_total
        self.used_tickets = {}  # jti -> exp; tickets are single-use
        self.sessions = {}  # session key -> open session count
        self.pending = 0  # connections that have not authenticated yet

    def session_key(self, claims):
        raise NotImplementedError

    async def prepare(self, claims):
        """Return (argv, env, cleanup): the command for the PTY and a callable run when it ends (or None)."""
        raise NotImplementedError

    def verify_ticket(self, token):
        try:
            claims = jwt.decode(
                token, self.secret_key, algorithms=["HS256"],
                options={"require": ["exp", "iat", "jti", "sub", "type", *self.ticket_claims]},
            )
        except jwt.InvalidTokenError:
            raise Denied("invalid ticket") from None
        if claims["type"] != self.ticket_type:
            raise Denied("invalid ticket")
        now = time.time()
        self.used_tickets = {j: e for j, e in self.used_tickets.items() if e > now}
        if claims["jti"] in self.used_tickets:
            raise Denied("ticket already used")
        self.used_tickets[claims["jti"]] = claims["exp"]
        return claims

    def _check_origin(self, ws):
        origin = ws.request.headers.get("Origin", "")
        host = ws.request.headers.get("Host", "")
        if not origin or not host or urlsplit(origin).netloc != host:
            raise Denied("bad origin")

    async def handle(self, ws):
        client = ws.request.headers.get("X-Real-IP", "?")
        if self.pending >= self.max_pending:
            log.warning("terminal refused from %s: too many pending handshakes", client)
            await ws.close(1013, "server busy, try again later")
            return
        self.pending += 1
        try:
            if ws.request.path != self.path:
                raise Denied("not found")
            self._check_origin(ws)
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if hello.get("type") != "auth":
                raise Denied("expected auth message")
            claims = self.verify_ticket(hello.get("ticket", ""))
            key = self.session_key(claims)
            if self.sessions.get(key, 0) >= self.max_per_user:
                raise Denied("too many open terminals")
            if sum(self.sessions.values()) >= self.max_total:
                raise Denied("server terminal limit reached")
            argv, env, cleanup = await self.prepare(claims)
        except (Denied, ValueError, TimeoutError, ConnectionClosed) as exc:
            log.info("terminal refused from %s: %s", client, exc)
            await ws.close(1008, str(exc) if isinstance(exc, Denied) else "bad request")
            return
        finally:
            self.pending -= 1

        self.sessions[key] = self.sessions.get(key, 0) + 1
        log.info("%s session opened for %s from=%s", self.ticket_type, key, client)
        try:
            await self._run_session(ws, argv, env, hello.get("cols", 80), hello.get("rows", 24))
        finally:
            self.sessions[key] -= 1
            if not self.sessions[key]:
                del self.sessions[key]
            if cleanup is not None:
                cleanup()
            log.info("%s session closed for %s from=%s", self.ticket_type, key, client)

    async def _run_session(self, ws, argv, env, cols, rows):
        loop = asyncio.get_running_loop()
        master, slave = os.openpty()
        try:
            set_winsize(master, rows, cols)
            proc = await asyncio.create_subprocess_exec(*argv, stdin=slave, stdout=slave, stderr=slave, env=env)
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(master, False)

        output = asyncio.Queue(maxsize=64)
        state = {"reading": True, "last_input": time.monotonic()}

        def on_readable():
            try:
                data = os.read(master, 65536)
            except BlockingIOError:
                return
            except OSError:  # EIO: the program exited and the slave side is closed
                data = b""
            if not data:
                loop.remove_reader(master)
                state["reading"] = False
                output.put_nowait(None)
                return
            output.put_nowait(data)
            if output.full():  # backpressure: stop reading until the client catches up
                loop.remove_reader(master)
                state["reading"] = False

        async def pump_output():
            while (data := await output.get()) is not None:
                await ws.send(data)
                if not state["reading"] and output.empty():
                    loop.add_reader(master, on_readable)
                    state["reading"] = True

        async def pump_input():
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "input" and isinstance(msg.get("data"), str):
                    state["last_input"] = time.monotonic()
                    await write_all(msg["data"].encode("utf-8"))
                elif msg.get("type") == "resize":
                    set_winsize(master, msg.get("rows", 24), msg.get("cols", 80))

        async def write_all(data):
            while data:
                try:
                    data = data[os.write(master, data):]
                except BlockingIOError:
                    await asyncio.sleep(0.01)

        async def idle_watchdog():
            while True:
                remaining = self.idle_seconds - (time.monotonic() - state["last_input"])
                if remaining <= 0:
                    return "idle timeout"
                await asyncio.sleep(min(remaining, 30))

        loop.add_reader(master, on_readable)
        tasks = [asyncio.create_task(t) for t in (pump_output(), pump_input(), idle_watchdog())]
        reason = "session ended"
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and isinstance(task.result(), str):
                    reason = task.result()
        except Exception as exc:
            log.warning("terminal session error: %s", exc)
        finally:
            for task in tasks:
                task.cancel()
            if state["reading"]:
                loop.remove_reader(master)
            os.close(master)  # hangs up the PTY: the program receives SIGHUP
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            try:
                await ws.close(1000, reason)
            except Exception:
                pass
