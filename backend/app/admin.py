"""Admin API: runtime settings and the activity log."""
from flask import Blueprint, abort, current_app, jsonify, request

from . import settings
from .activity import record, serialize
from .auth import json_body, require_auth
from .db import get_db

bp = Blueprint("admin", __name__, url_prefix="/api/admin")

ADMIN = ("admin",)


@bp.get("/settings")
@require_auth(ADMIN)
def get_settings():
    return jsonify(allow_registration=settings.allow_registration(),
                   activity_log_file=current_app.config["ACTIVITY_LOG"] or None,
                   activity_retention_days=current_app.config["ACTIVITY_RETENTION_DAYS"])


@bp.put("/settings")
@require_auth(ADMIN)
def update_settings():
    data = json_body()
    if "allow_registration" in data:
        value = data["allow_registration"]
        if not isinstance(value, bool):
            abort(400, "allow_registration must be a boolean")
        if value != settings.allow_registration():
            settings.set_bool("allow_registration", value)
            record("settings.update", allow_registration=value)
    return get_settings()


@bp.get("/activity")
@require_auth(ADMIN)
def list_activity():
    """Newest first. Filters: user (exact username), event (exact, or a prefix such as "auth."),
    result ("success" / "failure")."""
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        abort(400, "limit and offset must be integers")

    where, params = [], []
    user = request.args.get("user", "").strip()
    if user:
        where.append("username = ? COLLATE NOCASE")
        params.append(user)
    event = request.args.get("event", "").strip()
    if event.endswith("."):
        where.append("substr(event, 1, ?) = ?")
        params += [len(event), event]
    elif event:
        where.append("event = ?")
        params.append(event)
    result = request.args.get("result", "")
    if result in ("success", "failure"):
        where.append("success = ?")
        params.append(int(result == "success"))
    elif result:
        abort(400, "result must be 'success' or 'failure'")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM activity_log {clause}", params).fetchone()[0]
    rows = db.execute(f"SELECT * FROM activity_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                      params + [limit, offset]).fetchall()
    events = [r[0] for r in db.execute("SELECT DISTINCT event FROM activity_log ORDER BY event")]
    return jsonify(entries=[serialize(r) for r in rows], total=total, limit=limit, offset=offset, events=events)
