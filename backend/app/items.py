"""CRUD API for items. Users see their own items; admins see everyone's.

An item may have a type (see item_types.py); its field values are returned as `fields`."""
from flask import Blueprint, abort, g, jsonify, request

from . import item_types
from .activity import record
from .auth import json_body, require_auth
from .db import get_db, row_to_dict

bp = Blueprint("items", __name__, url_prefix="/api/items")

ITEM_COLUMNS = "items.id, items.owner_id, users.username AS owner, items.title, items.description, " \
               "items.done, items.type_id, item_types.name AS type_name, items.created_at, items.updated_at"
ITEM_FROM = "items JOIN users ON users.id = items.owner_id LEFT JOIN item_types ON item_types.id = items.type_id"


def _is_admin():
    return g.user["role"] == "admin"


def _serialize_all(rows):
    items = [row_to_dict(r) for r in rows]
    values = item_types.load_values(items)
    for item in items:
        item["done"] = bool(item["done"])
        item["fields"] = values.get(item["id"], {}) if item["type_id"] is not None else None
    return items


def _serialize(row):
    return _serialize_all([row])[0]


def _other_owner(item):
    """The owner's name when an admin acts on someone else's item, for the activity log."""
    return item["owner"] if item["owner_id"] != g.user["id"] else None


def _validate(data):
    title = data.get("title")
    description = data.get("description", "")
    done = data.get("done", False)
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        abort(400, "title is required (max 200 characters)")
    if not isinstance(description, str) or len(description) > 5000:
        abort(400, "description must be a string (max 5000 characters)")
    if not isinstance(done, bool):
        abort(400, "done must be a boolean")
    return title.strip(), description, int(done)


def _get_item(item_id):
    row = get_db().execute(
        f"SELECT {ITEM_COLUMNS} FROM {ITEM_FROM} WHERE items.id = ?",
        (item_id,),
    ).fetchone()
    # 404 rather than 403 so other users' item ids are not disclosed.
    if row is None or (not _is_admin() and row["owner_id"] != g.user["id"]):
        abort(404, "item not found")
    return row


@bp.get("")
@require_auth()
def list_items():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        abort(400, "limit and offset must be integers")

    where, params = [], []
    if not _is_admin():
        where.append("items.owner_id = ?")
        params.append(g.user["id"])
    type_id = request.args.get("type_id", "").strip()
    if type_id == "none":
        where.append("items.type_id IS NULL")
    elif type_id:
        if not type_id.isdigit():
            abort(400, "type_id must be an integer or 'none'")
        where.append("items.type_id = ?")
        params.append(int(type_id))
    q = request.args.get("q", "").strip()
    if q:
        where.append("(items.title LIKE ? ESCAPE '\\' OR items.description LIKE ? ESCAPE '\\')")
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params += [pattern, pattern]
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM items {clause}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT {ITEM_COLUMNS} FROM {ITEM_FROM} {clause} "
        "ORDER BY items.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return jsonify(items=_serialize_all(rows), total=total, limit=limit, offset=offset)


@bp.post("")
@require_auth()
def create_item():
    data = json_body()
    title, description, done = _validate(data)
    item_type, values = None, None
    if data.get("type_id") is not None:
        type_id = data["type_id"]
        if isinstance(type_id, bool) or not isinstance(type_id, int):
            abort(400, "type_id must be an integer")
        item_type = item_types.get_type(type_id)
        if item_type is None:
            abort(400, "unknown item type")
        values = item_types.validate_values(item_type, data.get("fields"))
    elif data.get("fields"):
        abort(400, "fields require a type_id")
    db = get_db()
    cur = db.execute(
        "INSERT INTO items (owner_id, title, description, done, type_id) VALUES (?, ?, ?, ?, ?)",
        (g.user["id"], title, description, done, item_type and item_type["id"]),
    )
    if item_type is not None:
        item_types.save_values(db, item_type, cur.lastrowid, values)
    db.commit()
    record("item.create", item_id=cur.lastrowid, title=title[:80], type=item_type and item_type["name"])
    return jsonify(item=_serialize(_get_item(cur.lastrowid))), 201


@bp.get("/<int:item_id>")
@require_auth()
def get_item(item_id):
    return jsonify(item=_serialize(_get_item(item_id)))


@bp.put("/<int:item_id>")
@require_auth()
def update_item(item_id):
    item = _get_item(item_id)
    data = json_body()
    title, description, done = _validate(data)
    if "type_id" in data and data["type_id"] != item["type_id"]:
        abort(400, "the type of an item cannot be changed")
    item_type = values = None
    if item["type_id"] is not None:
        item_type = item_types.get_type(item["type_id"])
        current = item_types.load_values([row_to_dict(item)]).get(item_id)
        values = item_types.validate_values(item_type, data.get("fields"), current)
    elif data.get("fields"):
        abort(400, "this item has no type, so it has no fields")
    db = get_db()
    db.execute(
        "UPDATE items SET title = ?, description = ?, done = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (title, description, done, item_id),
    )
    if item_type is not None:
        item_types.save_values(db, item_type, item_id, values)
    db.commit()
    record("item.update", item_id=item_id, title=title[:80], owner=_other_owner(item))
    return jsonify(item=_serialize(_get_item(item_id)))


@bp.delete("/<int:item_id>")
@require_auth()
def delete_item(item_id):
    item = _get_item(item_id)
    db = get_db()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    record("item.delete", item_id=item_id, title=item["title"][:80], owner=_other_owner(item))
    return "", 204
