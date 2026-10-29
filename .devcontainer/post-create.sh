#!/usr/bin/env bash
# One-time setup after the dev container is created: dependencies, config, database, test admin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/backend/.venv"
ENV_FILE="$ROOT/backend/instance/dev.env"

echo "==> Python dependencies"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$ROOT/backend/requirements.txt" pytest flake8

echo "==> Node dependencies"
(cd "$ROOT/frontend" && npm install --no-audit --no-fund)

echo "==> Dev configuration ($ENV_FILE)"
mkdir -p "$ROOT/backend/instance"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<EOF
# Dev settings for .devcontainer/homeacces.sh (not used on the Pi). Keep this file private.
# homeacces.sh sets HOMEACCES_COOKIE_SECURE per mode (dev: 0, prod: 1) and defaults
# HOMEACCES_PRIVD_CLIENT_USER to the user running it. The database defaults to backend/instance/homeacces.sqlite3.
HOMEACCES_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
HOMEACCES_PAM_ENABLED=1
HOMEACCES_PAM_GROUP=homeacces-pam
HOMEACCES_PAM_ADMIN_GROUP=sudo
HOMEACCES_PRIVD_SOCKET=/run/homeacces/privd.sock
HOMEACCES_TERMINAL_LISTEN=127.0.0.1:8001
EOF
    chmod 0600 "$ENV_FILE"
fi

echo "==> Database"
set -a; . "$ENV_FILE"; set +a
(cd "$ROOT/backend" && "$VENV/bin/flask" --app wsgi.py init-db)
# Local test admin "admin" / "admin1234" (only if the database has no users yet).
(cd "$ROOT/backend" && "$VENV/bin/python" - <<'EOF'
from app import create_app
from app.auth import hash_password
from app.db import get_db

with create_app().app_context():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        db.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin', ?, 'admin')",
                   (hash_password("admin1234"),))
        db.commit()
        print("    created local admin: admin / admin1234")
EOF
)
