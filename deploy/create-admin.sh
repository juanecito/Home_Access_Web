#!/usr/bin/env bash
# Create an admin user (or reset an existing user's password and promote them).
# Usage: sudo ./deploy/create-admin.sh <username>
set -euo pipefail

if [[ $EUID -ne 0 || $# -ne 1 ]]; then
    echo "Usage: sudo $0 <username>" >&2
    exit 1
fi

# The homeacces user reads the env file itself (it is root:homeacces 0640). Passing the settings
# as arguments would show HOMEACCES_SECRET_KEY to every local user in `ps` while the password is typed.
runuser -u homeacces -- bash -c 'set -a; . /etc/homeacces/homeacces.env; set +a; exec "$@"' _ \
    /opt/homeacces/venv/bin/flask --app /opt/homeacces/backend/wsgi.py create-admin "$1"
