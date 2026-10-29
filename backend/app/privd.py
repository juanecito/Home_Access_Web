"""Client for the privileged helper's Unix socket (see homeacces_privd/server.py)."""
import json
import socket

from flask import abort, current_app


def call(op, **params):
    """Send one request, return the decoded reply. 503 if the helper is unreachable."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(15)
            sock.connect(current_app.config["PRIVD_SOCKET"])
            sock.sendall(json.dumps({"op": op, **params}).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf)
    except (OSError, ValueError):
        current_app.logger.exception("homeacces-privd request failed")
        abort(503, "system authentication service unavailable")
    if not isinstance(reply, dict):
        abort(503, "system authentication service unavailable")
    return reply
