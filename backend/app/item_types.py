"""Item types: user-defined kinds of items with their own typed fields.

Every type owns a table `item_type_<id>` with one row per item of that type (`item_id` references
items.id) and one column per field. Common data (title, description, done, owner) stays in `items`.

Field and table names end up in SQL statements, so they are never taken verbatim from a request
without matching FIELD_NAME_RE; table names are derived from the numeric type id only.
Anyone signed in can read the types; only admins can create, change or delete them.
"""
import json
import math
import re
from contextlib import contextmanager

from flask import Blueprint, abort, jsonify

from .activity import record
from .auth import json_body, require_auth
from .db import get_db

bp = Blueprint("item_types", __name__, url_prefix="/api/item-types")

ADMIN = ("admin",)
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RESERVED_NAMES = {"item_id"}
MAX_FIELDS = 50
MAX_STRING = 5000
# SQLite storage type of each field type; booleans are stored as 0/1.
SQL_TYPES = {"int": "INTEGER", "float": "REAL", "boolean": "INTEGER", "string": "TEXT"}


# ---------------------------------------------------------------- values

def validate_value(field_type, value, label):
    """Check a JSON value against a field type and return it in its Python form."""
    if field_type == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not -2**63 <= value < 2**63:
            abort(400, f"{label} must be an integer")
        return value
    if field_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            abort(400, f"{label} must be a number")
        return float(value)
    if field_type == "boolean":
        if not isinstance(value, bool):
            abort(400, f"{label} must be a boolean")
        return value
    if not isinstance(value, str) or len(value) > MAX_STRING:
        abort(400, f"{label} must be a string (max {MAX_STRING} characters)")
    return value


def _to_db(field_type, value):
    return int(value) if field_type == "boolean" else value


def _from_db(field_type, value):
    if value is None:
        return None
    if field_type == "boolean":
        return bool(value)
    return float(value) if field_type == "float" else value


def table_name(type_id):
    return f"item_type_{int(type_id)}"


def _quote(name):
    # Names are checked against FIELD_NAME_RE before reaching SQL; quoting is a second safeguard.
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------- types

def _serialize_field(row):
    return {"id": row["id"], "name": row["name"], "type": row["field_type"],
            "default": json.loads(row["default_value"])}


def _load_types(type_id=None):
    db = get_db()
    where, params = ("WHERE id = ?", (type_id,)) if type_id is not None else ("", ())
    types = [dict(r) for r in db.execute(
        f"SELECT id, name, created_at, updated_at FROM item_types {where} ORDER BY name", params)]
    if not types:
        return []
    by_id = {t["id"]: t for t in types}
    for t in types:
        t["table"] = table_name(t["id"])
        t["fields"] = []
        t["item_count"] = 0
    marks = ",".join("?" * len(by_id))
    for row in db.execute(f"SELECT * FROM item_type_fields WHERE type_id IN ({marks}) ORDER BY position, id",
                          list(by_id)):
        by_id[row["type_id"]]["fields"].append(_serialize_field(row))
    for row in db.execute(f"SELECT type_id, COUNT(*) AS n FROM items WHERE type_id IN ({marks}) GROUP BY type_id",
                          list(by_id)):
        by_id[row["type_id"]]["item_count"] = row["n"]
    return types


def get_type(type_id):
    """The type with its fields, or None."""
    types = _load_types(type_id)
    return types[0] if types else None


def _get_type_or_404(type_id):
    item_type = get_type(type_id)
    if item_type is None:
        abort(404, "item type not found")
    return item_type


def _validate_type(data):
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
        abort(400, "name is required (max 64 characters)")
    fields = data.get("fields", [])
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        abort(400, f"fields must be a list (max {MAX_FIELDS})")

    result, seen = [], set()
    for i, field in enumerate(fields, 1):
        if not isinstance(field, dict):
            abort(400, f"field {i} must be an object")
        fname, ftype = field.get("name"), field.get("type")
        if not isinstance(fname, str) or not FIELD_NAME_RE.match(fname) or fname in RESERVED_NAMES:
            abort(400, f"field {i}: name must be 1-40 characters: lowercase letters, digits and '_', "
                       "starting with a letter (and not 'item_id')")
        if fname in seen:
            abort(400, f"field name '{fname}' is used twice")
        seen.add(fname)
        if ftype not in SQL_TYPES:
            abort(400, f"field '{fname}': type must be one of {', '.join(SQL_TYPES)}")
        if "default" not in field:
            abort(400, f"field '{fname}': a default value is required")
        default = validate_value(ftype, field["default"], f"default value of '{fname}'")
        field_id = field.get("id")
        if field_id is not None and (isinstance(field_id, bool) or not isinstance(field_id, int)):
            abort(400, f"field '{fname}': id must be an integer")
        result.append({"id": field_id, "name": fname, "type": ftype, "default": default})
    return name.strip(), result


def _check_name_free(name, type_id=None):
    row = get_db().execute("SELECT id FROM item_types WHERE name = ?", (name,)).fetchone()
    if row is not None and row["id"] != type_id:
        abort(409, "an item type with this name already exists")


@contextmanager
def _transaction(db):
    """Run DDL and DML atomically: sqlite3 does not open a transaction before DDL by itself."""
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _insert_field(db, type_id, field, position):
    db.execute(
        "INSERT INTO item_type_fields (type_id, name, field_type, default_value, position) VALUES (?, ?, ?, ?, ?)",
        (type_id, field["name"], field["type"], json.dumps(field["default"]), position),
    )


def _add_column(db, type_id, field):
    table = table_name(type_id)
    db.execute(f"ALTER TABLE {table} ADD COLUMN {_quote(field['name'])} {SQL_TYPES[field['type']]}")
    # Items that already exist get the default value.
    db.execute(f"UPDATE {table} SET {_quote(field['name'])} = ?", (_to_db(field["type"], field["default"]),))


@bp.get("")
@require_auth()
def list_types():
    return jsonify(item_types=_load_types())


@bp.get("/<int:type_id>")
@require_auth()
def get_item_type(type_id):
    return jsonify(item_type=_get_type_or_404(type_id))


@bp.post("")
@require_auth(ADMIN)
def create_type():
    name, fields = _validate_type(json_body())
    _check_name_free(name)
    db = get_db()
    with _transaction(db):
        type_id = db.execute("INSERT INTO item_types (name) VALUES (?)", (name,)).lastrowid
        columns = "".join(f", {_quote(f['name'])} {SQL_TYPES[f['type']]}" for f in fields)
        db.execute(f"CREATE TABLE {table_name(type_id)} ("
                   f"item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE{columns}) STRICT")
        for position, field in enumerate(fields):
            _insert_field(db, type_id, field, position)
    record("item_type.create", type_id=type_id, name=name, fields=[f["name"] for f in fields])
    return jsonify(item_type=get_type(type_id)), 201


@bp.put("/<int:type_id>")
@require_auth(ADMIN)
def update_type(type_id):
    """Replace name and field list. Fields carrying the `id` of an existing field keep their data
    (and may be renamed or get a new default, but not change type); fields left out are dropped
    together with their data; fields without `id` are added."""
    current = _get_type_or_404(type_id)
    name, fields = _validate_type(json_body())
    _check_name_free(name, type_id)
    existing = {f["id"]: f for f in current["fields"]}
    for field in fields:
        old = existing.get(field["id"]) if field["id"] is not None else None
        if field["id"] is not None and old is None:
            abort(400, f"field '{field['name']}': unknown field id {field['id']}")
        if old is not None and old["type"] != field["type"]:
            abort(400, f"field '{old['name']}': the type of an existing field cannot be changed")
    kept = {f["id"] for f in fields if f["id"] is not None}
    removed = [f for f in current["fields"] if f["id"] not in kept]
    renamed = [f for f in fields if f["id"] is not None and existing[f["id"]]["name"] != f["name"]]
    table = table_name(type_id)

    db = get_db()
    with _transaction(db):
        db.execute("UPDATE item_types SET name = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                   "WHERE id = ?", (name, type_id))
        for field in removed:
            db.execute(f"ALTER TABLE {table} DROP COLUMN {_quote(field['name'])}")
            db.execute("DELETE FROM item_type_fields WHERE id = ?", (field["id"],))
        # Rename through temporary names (which FIELD_NAME_RE never produces) so swapping two names works.
        for field in renamed:
            tmp = f"_tmp_{field['id']}"
            db.execute(f"ALTER TABLE {table} RENAME COLUMN {_quote(existing[field['id']]['name'])} TO {_quote(tmp)}")
            # Free the old name in the field list too, in case a new field takes it.
            db.execute("UPDATE item_type_fields SET name = ? WHERE id = ?", (tmp, field["id"]))
        for field in renamed:
            db.execute(f"ALTER TABLE {table} RENAME COLUMN {_quote('_tmp_' + str(field['id']))} "
                       f"TO {_quote(field['name'])}")
        for position, field in enumerate(fields):
            if field["id"] is None:
                _add_column(db, type_id, field)
                _insert_field(db, type_id, field, position)
            else:
                db.execute("UPDATE item_type_fields SET name = ?, default_value = ?, position = ? WHERE id = ?",
                           (field["name"], json.dumps(field["default"]), position, field["id"]))
    record("item_type.update", type_id=type_id, name=name, fields=[f["name"] for f in fields],
           removed=[f["name"] for f in removed] or None)
    return jsonify(item_type=get_type(type_id))


@bp.delete("/<int:type_id>")
@require_auth(ADMIN)
def delete_type(type_id):
    item_type = _get_type_or_404(type_id)
    if item_type["item_count"]:
        abort(409, f"{item_type['item_count']} item(s) still use this type; delete them first")
    db = get_db()
    with _transaction(db):
        db.execute(f"DROP TABLE IF EXISTS {table_name(type_id)}")
        db.execute("DELETE FROM item_types WHERE id = ?", (type_id,))
    record("item_type.delete", type_id=type_id, name=item_type["name"])
    return "", 204


# ---------------------------------------------------------------- field values of items

def validate_values(item_type, data, current=None):
    """Field values for an item of `item_type` from the request object `data` (may be None).
    Missing fields keep their `current` value, or get the field's default for a new item."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        abort(400, "fields must be an object")
    known = {f["name"] for f in item_type["fields"]}
    unknown = sorted(set(data) - known)
    if unknown:
        abort(400, f"unknown field(s) for type '{item_type['name']}': {', '.join(unknown)}")
    values = {}
    for field in item_type["fields"]:
        name = field["name"]
        if name in data:
            values[name] = validate_value(field["type"], data[name], f"field '{name}'")
        elif current is not None and current.get(name) is not None:
            values[name] = current[name]
        else:
            values[name] = field["default"]
    return values


def save_values(db, item_type, item_id, values):
    columns = ["item_id"] + [f["name"] for f in item_type["fields"]]
    params = [item_id] + [_to_db(f["type"], values[f["name"]]) for f in item_type["fields"]]
    db.execute(f"INSERT OR REPLACE INTO {table_name(item_type['id'])} ({', '.join(map(_quote, columns))}) "
               f"VALUES ({', '.join('?' * len(columns))})", params)


def load_values(items):
    """Map item id -> {field name: value} for items (dicts with id and type_id) that have a type."""
    by_type = {}
    for item in items:
        if item["type_id"] is not None:
            by_type.setdefault(item["type_id"], []).append(item["id"])
    db = get_db()
    result = {}
    for type_id, item_ids in by_type.items():
        fields = db.execute("SELECT name, field_type FROM item_type_fields WHERE type_id = ? ORDER BY position, id",
                            (type_id,)).fetchall()
        marks = ",".join("?" * len(item_ids))
        for row in db.execute(f"SELECT * FROM {table_name(type_id)} WHERE item_id IN ({marks})", item_ids):
            result[row["item_id"]] = {f["name"]: _from_db(f["field_type"], row[f["name"]]) for f in fields}
    return result
