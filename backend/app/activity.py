"""Audit trail of user access and activity.

Every event is stored in the `activity_log` table (shown in the admin UI) and appended as one
line to the file HOMEACCES_ACTIVITY_LOG, e.g.:

  2026-10-29T21:40:02.118Z event=auth.login result=success user="alice" ip="192.168.1.20" method="local"

The file is opened per event in append mode, so several gunicorn workers can share it and
logrotate needs no signal. String values are JSON-quoted, so a hostile username cannot forge lines.
"""
import json
from datetime import datetime, timedelta, timezone

from flask import current_app, g, has_request_context, request

from .db import get_db

PRUNE_EVERY = 500  # inserts between deletions of entries older than the retention period


def _timestamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def record(event, *, user=None, username=None, success=True, **details):
    """Log `event` for `user` (default: the authenticated user of the request).

    `username` names the account when there is no user row, e.g. a failed login.
    """
    if user is None and has_request_context():
        user = g.get("user")
    if user is not None and username is None:
        username = user["username"]
    username = username[:64] if isinstance(username, str) else None
    details = {k: v for k, v in details.items() if v is not None}
    ip = ua = None
    if has_request_context():
        ip = request.remote_addr
        ua = (request.user_agent.string or "")[:200] or None
    ts = _timestamp(datetime.now(timezone.utc))

    db = get_db()
    cur = db.execute(
        "INSERT INTO activity_log (ts, user_id, username, event, success, ip, user_agent, details) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, user["id"] if user is not None else None, username, event, int(success), ip, ua,
         json.dumps(details) if details else None),
    )
    if cur.lastrowid % PRUNE_EVERY == 0:
        _prune(db)
    db.commit()

    _append_to_file(ts, {"event": event, "result": "success" if success else "failure", "user": username,
                         "ip": ip, **details, "ua": ua})


def _prune(db):
    days = current_app.config["ACTIVITY_RETENTION_DAYS"]
    if days > 0:
        cutoff = _timestamp(datetime.now(timezone.utc) - timedelta(days=days))
        db.execute("DELETE FROM activity_log WHERE ts < ?", (cutoff,))


UNQUOTED = ("event", "result")  # fixed vocabulary, never user input


def _append_to_file(ts, fields):
    path = current_app.config["ACTIVITY_LOG"]
    if not path:
        return
    parts = [ts] + [f"{k}={v if k in UNQUOTED else json.dumps(v)}" for k, v in fields.items() if v is not None]
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(" ".join(parts) + "\n")
    except OSError:
        current_app.logger.exception("cannot write activity log %s", path)


def serialize(row):
    return {"id": row["id"], "ts": row["ts"], "user_id": row["user_id"], "username": row["username"],
            "event": row["event"], "success": bool(row["success"]), "ip": row["ip"],
            "user_agent": row["user_agent"], "details": json.loads(row["details"]) if row["details"] else {}}
