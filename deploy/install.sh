#!/usr/bin/env bash
# Install or update HomeAcces on Debian 13 (Raspberry Pi OS / Debian trixie).
# Run from the project root:  sudo ./deploy/install.sh
#
# Set SKIP_FRONTEND_BUILD=1 to use an existing frontend/dist (e.g. built on a faster machine).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/opt/homeacces
WEB_DIR=/var/www/homeacces
ENV_FILE=/etc/homeacces/homeacces.env
SSL_DIR=/etc/ssl/homeacces

echo "==> Installing system packages"
apt-get update
apt-get install -y nginx python3 python3-venv sqlite3 openssl rsync
if [[ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
    apt-get install -y nodejs npm
fi

echo "==> Creating service user"
id -u homeacces >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin homeacces
# Members of this group may sign in with their system account (PAM) and use the web terminal.
getent group homeacces-pam >/dev/null || groupadd --system homeacces-pam
install -d -o homeacces -g homeacces -m 0750 /var/lib/homeacces /var/log/homeacces

echo "==> Installing backend to $APP_DIR"
install -d -m 0755 "$APP_DIR"
# Code is root-owned: homeacces-privd runs it as root, so no other user may be able to modify it.
rsync -a --delete --chown=root:root --chmod=D755,F644 \
    --exclude '__pycache__' --exclude 'instance' --exclude 'tests' --exclude '.venv' \
    "$SRC/backend/" "$APP_DIR/backend/"
[[ -d "$APP_DIR/venv" ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/backend/requirements.txt"

echo "==> Writing configuration"
if [[ -f "$ENV_FILE" ]]; then
    "$SRC/deploy/gen-env.sh" --update --output "$ENV_FILE"  # add settings from newer versions
else
    "$SRC/deploy/gen-env.sh" --output "$ENV_FILE"
fi

echo "==> Initialising database"
# Sourced by the homeacces user, not passed as arguments: those are visible to all users in `ps`.
runuser -u homeacces -- bash -c 'set -a; . "$1"; set +a; shift; exec "$@"' _ "$ENV_FILE" \
    "$APP_DIR/venv/bin/flask" --app "$APP_DIR/backend/wsgi.py" init-db

echo "==> Building frontend"
if [[ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
    # npm runs install scripts of dependencies, so build as the owner of the source tree, not as
    # root. `npm ci` installs exactly what package-lock.json lists.
    BUILD_USER="$(stat -c %U "$SRC/frontend")"
    if [[ "$BUILD_USER" == root ]]; then
        echo "    warning: $SRC/frontend is owned by root, so npm runs as root;" >&2
        echo "    clone the project as a normal user to avoid that" >&2
        (cd "$SRC/frontend" && npm ci --no-audit --no-fund && npm run build)
    else
        # Output of earlier root builds would block the build user.
        for dir in "$SRC/frontend/node_modules" "$SRC/frontend/dist"; do
            if [[ -e "$dir" ]]; then chown -R "$BUILD_USER:" "$dir"; fi
        done
        runuser -u "$BUILD_USER" -- env HOME="$(getent passwd "$BUILD_USER" | cut -d: -f6)" \
            bash -c 'cd "$1" && npm ci --no-audit --no-fund && npm run build' _ "$SRC/frontend"
    fi
fi
[[ -f "$SRC/frontend/dist/index.html" ]] || { echo "frontend/dist is missing" >&2; exit 1; }
install -d -m 0755 "$WEB_DIR"
rsync -a --delete --chown=root:root --chmod=D755,F644 "$SRC/frontend/dist/" "$WEB_DIR/"

echo "==> TLS certificate"
if [[ ! -f "$SSL_DIR/homeacces.crt" ]]; then
    install -d -m 0755 "$SSL_DIR"
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -keyout "$SSL_DIR/homeacces.key" -out "$SSL_DIR/homeacces.crt" \
        -subj "/CN=$(hostname)" -addext "subjectAltName=DNS:$(hostname),DNS:$(hostname).local"
    chmod 0600 "$SSL_DIR/homeacces.key"
    echo "    generated self-signed certificate in $SSL_DIR"
fi

echo "==> Configuring nginx"
install -m 0644 "$SRC/deploy/nginx/homeacces-proxy.conf" /etc/nginx/snippets/homeacces-proxy.conf
install -m 0644 "$SRC/deploy/nginx/homeacces-headers.conf" /etc/nginx/snippets/homeacces-headers.conf
install -m 0644 "$SRC/deploy/nginx/homeacces.conf" /etc/nginx/sites-available/homeacces
ln -sf /etc/nginx/sites-available/homeacces /etc/nginx/sites-enabled/homeacces
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload-or-restart nginx
systemctl enable nginx

echo "==> Configuring PAM"
install -m 0644 "$SRC/deploy/pam.d/homeacces" /etc/pam.d/homeacces

echo "==> Configuring logrotate"
install -m 0644 "$SRC/deploy/logrotate/homeacces" /etc/logrotate.d/homeacces

echo "==> Configuring systemd services"
install -m 0644 "$SRC/deploy/systemd/homeacces.socket" /etc/systemd/system/homeacces.socket
install -m 0644 "$SRC/deploy/systemd/homeacces.service" /etc/systemd/system/homeacces.service
install -m 0644 "$SRC/deploy/systemd/homeacces-privd.service" /etc/systemd/system/homeacces-privd.service
systemctl daemon-reload
systemctl enable homeacces.socket homeacces-privd homeacces
# The API gets its socket from homeacces.socket: stop it first so the socket can be (re)created.
systemctl stop homeacces
systemctl restart homeacces.socket homeacces-privd
systemctl start homeacces

echo
echo "HomeAcces is running at https://$(hostname).local/"
echo "Create the first admin with:"
echo "    sudo ./deploy/create-admin.sh <username>"
echo "Allow a system user to sign in with PAM and use the web terminal:"
echo "    sudo usermod -aG homeacces-pam <system-user>"
