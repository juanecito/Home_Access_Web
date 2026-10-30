#!/usr/bin/env bash
# Run HomeAcces inside the dev container (there is no systemd in Docker).
#
#   homeacces.sh start [dev]   Flask --debug :8000 + homeacces-privd :8001 + homeacces-ssh :8002 + Vite :5173
#                          (hot reload)
#   homeacces.sh start prod    gunicorn (Unix socket) + homeacces-privd :8001 + homeacces-ssh :8002 + nginx HTTPS
#                          :8443, using the real deploy/nginx config and a production build of the UI
#   homeacces.sh stop | restart [mode] | status | logs [api|privd|ssh|ui|nginx]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/backend/.venv"
ENV_FILE="$ROOT/backend/instance/dev.env"
RUN=/tmp/homeacces
SERVICES=(api privd ssh ui nginx)
mkdir -p "$RUN"

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE: run .devcontainer/post-create.sh first" >&2; exit 1; }

# Start a command in its own session (so the whole process group can be stopped), detached
# from this shell so it survives the end of postStartCommand.
spawn() {
    local name=$1; shift
    setsid nohup "$@" > "$RUN/$name.log" 2>&1 < /dev/null &
    echo $! > "$RUN/$name.pid"
}

running() {
    [[ -f "$RUN/$1.pid" ]] && sudo kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null
}

start_privd() {
    # Root, like on the Pi: needed for PAM password checks and runuser. No .pyc files, so root
    # does not leave root-owned __pycache__ directories in the mounted workspace.
    spawn privd sudo bash -c "set -a; . '$ENV_FILE'; set +a; export HOMEACCES_PRIVD_CLIENT_USER=\${HOMEACCES_PRIVD_CLIENT_USER:-$(id -un)}; cd '$ROOT/backend'; exec '$VENV/bin/python' -B -m homeacces_privd"
}

start_ssh() {
    # Unprivileged, like on the Pi (there it runs as the homeacces user).
    spawn ssh bash -c "cd '$ROOT/backend' && exec '$VENV/bin/python' -m homeacces_ssh"
}

start_dev() {
    export HOMEACCES_COOKIE_SECURE=0  # Vite serves plain HTTP
    set -a; . "$ENV_FILE"; set +a
    start_privd
    start_ssh
    spawn api bash -c "cd '$ROOT/backend' && exec '$VENV/bin/flask' --app wsgi.py run --host 127.0.0.1 --port 8000 --debug"
    spawn ui bash -c "cd '$ROOT/frontend' && exec npm run dev -- --host 0.0.0.0 --port 5173 --strictPort"
    echo "HomeAcces (dev) → http://localhost:5173"
}

start_prod() {
    echo "==> Building UI"
    (cd "$ROOT/frontend" && npm run build)
    sudo rsync -a --delete --chown=root:root --chmod=D755,F644 "$ROOT/frontend/dist/" /var/www/homeacces/

    if ! sudo test -f /etc/ssl/homeacces/homeacces.crt; then
        sudo install -d -m 0755 /etc/ssl/homeacces
        sudo openssl req -x509 -nodes -newkey rsa:2048 -days 365 -subj "/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
            -keyout /etc/ssl/homeacces/homeacces.key -out /etc/ssl/homeacces/homeacces.crt 2>/dev/null
    fi

    # The production site config, moved to unprivileged-looking ports for port forwarding.
    sudo install -m 0644 "$ROOT/deploy/nginx/homeacces-proxy.conf" /etc/nginx/snippets/homeacces-proxy.conf
    sudo install -m 0644 "$ROOT/deploy/nginx/homeacces-headers.conf" /etc/nginx/snippets/homeacces-headers.conf
    # No systemd here: gunicorn creates the API socket itself, under $RUN.
    sed -e "s|unix:/run/homeacces-api.sock|unix:$RUN/api.sock|" \
        -e 's/listen 80 /listen 8080 /; s/listen \[::\]:80 /listen [::]:8080 /' \
        -e 's/listen 443 /listen 8443 /; s/listen \[::\]:443 /listen [::]:8443 /' \
        -e 's|return 301 https://$host$request_uri;|return 301 https://$host:8443$request_uri;|' \
        "$ROOT/deploy/nginx/homeacces.conf" | sudo tee /etc/nginx/sites-enabled/homeacces > /dev/null
    sudo nginx -t -q

    export HOMEACCES_COOKIE_SECURE=1
    set -a; . "$ENV_FILE"; set +a
    start_privd
    start_ssh
    spawn api bash -c "cd '$ROOT/backend' && exec '$VENV/bin/gunicorn' --workers 2 --bind 'unix:$RUN/api.sock' --access-logfile - wsgi:app"
    spawn nginx sudo nginx -g 'daemon off;'
    echo "HomeAcces (prod) → https://localhost:8443 (self-signed certificate)"
}

stop() {
    for name in "${SERVICES[@]}"; do
        if running "$name"; then
            sudo kill -TERM -- "-$(cat "$RUN/$name.pid")" 2>/dev/null || true
        fi
        rm -f "$RUN/$name.pid"
    done
}

status() {
    for name in "${SERVICES[@]}"; do
        if running "$name"; then
            echo "$name: running (log: $RUN/$name.log)"
        else
            echo "$name: stopped"
        fi
    done
}

case "${1:-}" in
    start)
        stop
        "start_${2:-dev}"
        sleep 2
        status
        ;;
    stop) stop ;;
    restart) stop; "start_${2:-dev}" ;;
    status) status ;;
    logs)
        if [[ -n "${2:-}" ]]; then tail -n 100 -f "$RUN/$2.log"; else tail -n 20 "$RUN"/*.log; fi
        ;;
    *) sed -n '2,8p' "$0"; exit 1 ;;
esac
