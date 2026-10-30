# HomeAcces — lightweight web app for Raspberry Pi (Debian 13)

A small full-stack site: **nginx** serves a **React** UI and proxies `/api` to a **Python 3 (Flask)**
API backed by **SQLite**. Authentication uses **JWT** stored in **HttpOnly cookies**, with CSRF
protection, refresh-token rotation and login throttling. Users can sign in with a HomeAcces account
or with their **Raspberry Pi system account (PAM)**; PAM sessions get a **web terminal** (xterm.js).
Every user also gets an **SSH menu**: saved SSH connections opened in the browser with the OpenSSH client.

```
browser ──HTTPS──> nginx ──┬── /                 → /var/www/homeacces (React build)
                           ├── /api/terminal/ws  → homeacces-privd 127.0.0.1:8001 (root) → PTY: runuser -l <user>
                           ├── /api/ssh/ws       → homeacces-ssh 127.0.0.1:8002 (homeacces) → PTY: ssh <saved connection>
                           └── /api/*            → gunicorn /run/homeacces-api.sock (Flask, user homeacces) → SQLite
                                                      │
                                                      └── Unix socket /run/homeacces/privd.sock → homeacces-privd (PAM)
```

Detailed technical documentation (architecture, backend, database, authentication, frontend,
deployment — with diagrams) is in [`doc/`](doc/README.md).

## Layout

```
backend/            Flask API
  app/__init__.py     app factory, error handling, CLI (init-db, create-admin)
  app/auth.py         passwords, JWT, cookies, CSRF, /api/auth/*
  app/items.py        /api/items CRUD
  app/item_types.py   /api/item-types: user-defined item types, one table per type
  app/users.py        /api/users admin CRUD
  app/admin.py        /api/admin/* settings and activity log (admin only)
  app/activity.py     activity log: SQLite table + append-only log file
  app/settings.py     runtime settings stored in SQLite (e.g. registration on/off)
  app/terminal.py     /api/terminal/ticket (single-use terminal tickets)
  app/ssh.py          /api/ssh/*: saved SSH connections, SSH tickets
  app/privd.py        client for the privileged helper's Unix socket
  app/schema.sql      database schema
  homeacces_privd/        privileged helper (root): PAM checks + terminal WebSocket/PTY
  homeacces_ssh/          SSH terminal service (user homeacces): OpenSSH client in a WebSocket/PTY
  webterm.py          WebSocket ↔ PTY bridge shared by both terminal services
  tests/              pytest suite
frontend/           React + Vite UI (login/register, items, SSH, account, admin: users/settings/activity)
deploy/             nginx site, systemd unit, env template, install scripts
```

## Install on the Raspberry Pi

```bash
git clone <this repo> homeacces && cd homeacces     # or copy the folder to the Pi
sudo ./deploy/install.sh
sudo ./deploy/create-admin.sh admin
```

To let a system user sign in with PAM and use the terminal:

```bash
sudo usermod -aG homeacces-pam pi
```

Open `https://<pi-hostname>.local/`. The certificate is self-signed, so the browser will warn once;
for a public site, replace `/etc/ssl/homeacces/homeacces.*` with a real certificate (e.g. certbot).

| What | Where |
|---|---|
| Config (secret key, TTLs, registration) | `/etc/homeacces/homeacces.env` → `sudo systemctl restart homeacces homeacces-privd homeacces-ssh` |
| Database | `/var/lib/homeacces/homeacces.sqlite3` |
| Backend code + venv | `/opt/homeacces` |
| UI files | `/var/www/homeacces` |
| PAM service | `/etc/pam.d/homeacces` |
| Logs | `journalctl -u homeacces -u homeacces-privd -u homeacces-ssh -f`, `/var/log/nginx/` |
| Saved SSH host keys (one `known_hosts` file per user) | `/var/lib/homeacces/ssh/known_hosts/<user id>` |
| Activity log (sign-ins, account and item changes) | Admin → Activity log in the UI, and `/var/log/homeacces/activity.log` (rotated weekly, kept 52 weeks) |

`install.sh` creates `/etc/homeacces/homeacces.env` with `deploy/gen-env.sh`. You can also run it yourself
(`./deploy/gen-env.sh --help` lists all options):

```bash
sudo ./deploy/gen-env.sh --no-registration --idle 15   # first time: new secret key, custom settings
sudo ./deploy/gen-env.sh --force --no-pam              # rebuild from defaults, keep the secret key, back up the old file
sudo ./deploy/gen-env.sh --force --no-ssh              # SSH menu off by default (admins can still switch it on)
sudo ./deploy/gen-env.sh --force --rotate-secret       # new secret key (signs everyone out)
sudo ./deploy/gen-env.sh --update                      # only add settings introduced by newer versions
./deploy/gen-env.sh --print                            # preview without writing
```

`install.sh` builds the UI as the owner of the source folder (not root), using `npm ci`.
Building the React app on a Pi 3 or older is slow. You can run `npm ci && npm run build` in
`frontend/` on another machine, copy `frontend/dist` to the Pi, and run
`sudo SKIP_FRONTEND_BUILD=1 ./deploy/install.sh`. Re-run `install.sh` to deploy updates.

Backup: `sqlite3 /var/lib/homeacces/homeacces.sqlite3 ".backup /root/homeacces-$(date +%F).sqlite3"`

## Dev container (Docker, Debian 13)

`.devcontainer/` runs the whole site in a Debian 13 container: Python, Node, nginx, PAM and the
web terminals (local and SSH), as on the Pi.

**VS Code:** install the *Dev Containers* extension, then run **Dev Containers: Reopen in Container**.
On first start it installs the dependencies, creates the database and starts the site. Open
**http://localhost:5173**.

| Account | Login as | Password | Gets |
|---|---|---|---|
| `admin` | HomeAcces account | `admin1234` | web admin |
| `pi` | System account (PAM) | `raspberry` | web admin + terminal |

Both accounts exist only inside the container.

Manage the services from a terminal in the container (Docker has no systemd):

```bash
.devcontainer/homeacces.sh start dev    # Flask --debug + Vite hot reload → :5173 (default)
.devcontainer/homeacces.sh start prod   # production build behind the real nginx config → https://localhost:8443
.devcontainer/homeacces.sh status | stop | logs [api|privd|ssh|ui|nginx]
cd backend && .venv/bin/python -m pytest
```

Without VS Code, use the [devcontainer CLI](https://github.com/devcontainers/cli):
`devcontainer up --workspace-folder .` then `devcontainer exec --workspace-folder . bash`.

> **`homeacces.sh` only works inside the dev container.** Run on the host, it still prints
> `HomeAcces (dev) → http://localhost:5173`, but `status` shows every service stopped and
> `.devcontainer/homeacces.sh logs` shows why:
>
> - `api`: `backend/.venv/bin/flask: No such file or directory`, because `post-create.sh` has not run
> - `ui`: `npm: not found`, because Node is only installed in the container image
> - `privd`: `sudo: a terminal is required to read the password`, because the services are started
>   detached and sudo is only passwordless in the container
>
> Fix: open a shell in the container (**Dev Containers: Reopen in Container**, or the
> `devcontainer` CLI commands above) and run `homeacces.sh` from there. To develop on the host
> without Docker, follow [Local development](#local-development) instead.

## Local development

```bash
# API
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
export HOMEACCES_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))') HOMEACCES_COOKIE_SECURE=0
.venv/bin/flask --app wsgi.py init-db
.venv/bin/flask --app wsgi.py create-admin admin
.venv/bin/flask --app wsgi.py run --port 8000 --debug
.venv/bin/python -m pytest

# Optional, for PAM login + terminal (needs root; set HOMEACCES_PAM_ENABLED=1 for the API too):
sudo -E .venv/bin/python -m homeacces_privd     # with HOMEACCES_PRIVD_SOCKET pointing somewhere writable

# Optional, for the SSH menu (as your own user, same HOMEACCES_SECRET_KEY as the API):
.venv/bin/python -m homeacces_ssh               # WebSocket on 127.0.0.1:8002

# UI (second terminal) — Vite proxies /api to :8000 (and the terminal WebSockets to :8001 / :8002)
cd frontend && npm install && npm run dev     # http://localhost:5173
```

## API

All bodies are JSON. Errors look like `{"error": "...", "status": 4xx}`.

### Authentication

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/auth/register` | `username`, `password` | Creates a `user`, sets cookies. Off by default; admins switch it on/off in Admin → Settings (initial value: `HOMEACCES_ALLOW_REGISTRATION`) |
| GET | `/api/auth/methods` | – | `{local, pam, registration}`: which login options are enabled |
| POST | `/api/auth/login` | `username`, `password`, `method` (`local` default, or `pam`) | Sets cookies |
| POST | `/api/auth/refresh` | – | Rotates the refresh token, new access token |
| POST | `/api/auth/logout` | – | Revokes the refresh token, clears cookies |
| GET | `/api/auth/me` | – | Current user |
| PUT | `/api/auth/password` | `current_password`, `new_password` | Signs out other sessions and bearer tokens; 5 wrong current passwords in 15 min → 429 |
| POST | `/api/auth/token` | `username`, `password`, `method` | For scripts: returns `access_token` (Bearer), no cookies |

**Browser (cookies):** login sets `access_token` (HttpOnly, 15 min), `refresh_token` (HttpOnly,
7 days, path `/api/auth`) and `csrf_token` (readable). Every POST/PUT/DELETE must send the
`csrf_token` value in the `X-CSRF-Token` header. The UI handles this automatically.

**Scripts (Bearer):** no CSRF header is needed.

```bash
TOKEN=$(curl -sk https://pi.local/api/auth/token -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -sk https://pi.local/api/items -H "Authorization: Bearer $TOKEN"
```

### Items — users see their own, admins see all

| Method | Path | Body / query |
|---|---|---|
| GET | `/api/items` | `?q=search&type_id=<id>\|none&limit=50&offset=0` → `{items, total, limit, offset}` |
| POST | `/api/items` | `title` (required), `description`, `done`, `type_id`, `fields` (`{name: value}`; missing fields get their default) |
| GET | `/api/items/<id>` | – |
| PUT | `/api/items/<id>` | `title` (required), `description`, `done`, `fields` (fields not sent keep their value; the type cannot change) |
| DELETE | `/api/items/<id>` | – → 204 |

Items carry `type_id`, `type_name` and `fields` (`null` for items without a type).

### Item types — everyone can read, admins can change

Each type stores its field values in its own table `item_type_<id>` (`item_id` + one column per field).
Field types: `int`, `float`, `boolean`, `string`. Field names: lowercase letters, digits and `_`, starting with a letter.

| Method | Path | Body |
|---|---|---|
| GET | `/api/item-types` | – → `{item_types: [{id, name, table, fields: [{id, name, type, default}], item_count}]}` |
| GET | `/api/item-types/<id>` | – |
| POST | `/api/item-types` | `name`, `fields: [{name, type, default}]` |
| PUT | `/api/item-types/<id>` | `name`, `fields`: fields with the `id` of an existing field keep their data (rename / new default allowed, type change not); fields left out are dropped with their data; fields without `id` are added |
| DELETE | `/api/item-types/<id>` | – → 204 (409 while items still use the type) |

### Users — admin only

| Method | Path | Body |
|---|---|---|
| GET | `/api/users` | – |
| GET | `/api/users/<id>` | – |
| POST | `/api/users` | `username`, `password`, `role` (`user`/`admin`) |
| PUT | `/api/users/<id>` | `username`, `role` and/or `password` (all optional; local accounts only) |
| DELETE | `/api/users/<id>` | – → 204 (cannot delete yourself; deletes their items) |

`GET /api/users` also returns `last_login` (from the activity log) for each user.

### Admin — admin only

| Method | Path | Body / query |
|---|---|---|
| GET | `/api/admin/settings` | – → `{allow_registration, ssh_enabled, activity_log_file, activity_retention_days}` |
| PUT | `/api/admin/settings` | `allow_registration` and/or `ssh_enabled` (booleans) |
| GET | `/api/admin/activity` | `?user=&event=&result=success\|failure&limit=50&offset=0` → `{entries, total, events}`. `event` is exact (`auth.login`) or a prefix ending in `.` (`auth.`) |

Recorded events: `auth.login` (success and failure, with `method`, `client` and `reason`), `auth.logout`,
`auth.register`, `auth.password_change`, `auth.token_reuse`, `auth.session_revoked`, `terminal.open`,
`user.create` / `user.update` / `user.delete`, `settings.update`, `item.create` / `item.update` /
`item.delete`, `item_type.create` / `item_type.update` / `item_type.delete`, `ssh.profile_create` /
`ssh.profile_update` / `ssh.profile_delete`, `ssh.host_key_forget`, `ssh.open`. Each entry is also appended to `HOMEACCES_ACTIVITY_LOG` as one line, e.g.:

```
2026-10-29T21:40:02.118Z event=auth.login result=failure user="alice" ip="192.168.1.20" method="local" client="browser" reason="invalid credentials" ua="Mozilla/5.0 ..."
```

### Web terminal — PAM sessions only

| Method | Path | Notes |
|---|---|---|
| POST | `/api/terminal/ticket` | → `{ticket, expires_in: 30}`. Requires a session signed in with `method: "pam"` |
| WS | `/api/terminal/ws` | First message `{"type":"auth","ticket","cols","rows"}`, then `{"type":"input","data"}` / `{"type":"resize","cols","rows"}`. Output arrives as binary frames |

The shell runs as the signed-in system user (`runuser --login`), with the same rights that user
has over SSH. It closes after 30 minutes without keyboard input (`HOMEACCES_TERMINAL_IDLE_MIN`).

### SSH — every signed-in user, own connections only

Each user keeps a private list of SSH connections (not visible to anyone else, admins included).
Passwords are never stored: OpenSSH asks for them, for key passphrases and for unknown host keys
in the terminal.

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/ssh/profiles` | – → `{profiles: [{id, name, host, port, username, has_key, created_at, updated_at}], enabled}` |
| POST | `/api/ssh/profiles` | `name`, `host`, `port` (default 22), `username`, `private_key` (optional, OpenSSH/PEM) → 201. Max 100 per user |
| PUT | `/api/ssh/profiles/<id>` | same fields; `private_key` left out keeps the saved key, `null` or `""` removes it |
| DELETE | `/api/ssh/profiles/<id>` | – → 204 |
| DELETE | `/api/ssh/profiles/<id>/host-key` | – → `{removed}`: forgets the server's saved host key (e.g. after a reinstall) |
| POST | `/api/ssh/ticket` | `profile_id` → `{ticket, expires_in: 30}`; 403 when an admin disabled the SSH menu |
| WS | `/api/ssh/ws` | Same protocol as `/api/terminal/ws` |

The private key is write-only: the API only reports `has_key`. SSH sessions close after
`HOMEACCES_TERMINAL_IDLE_MIN` minutes without keyboard input too.

### Health

`GET /api/health` → `{"status": "ok"}`

## Security notes

**PAM and the terminal** — this gives shell access from the browser, so:

- The web API never runs as root. Root-only work (checking system passwords, starting shells) lives
  in `homeacces-privd`, which listens only on 127.0.0.1 and on a Unix socket that only the `homeacces`
  user may use (checked with `SO_PEERCRED`). Its code in `/opt/homeacces` is root-owned.
- PAM login is limited to members of `homeacces-pam` with uid ≥ 1000. root and service accounts are
  always refused. The helper enforces this on every login, every token refresh (every 15 minutes)
  and every terminal start, so removing a user from the group or locking the account ends access.
- The terminal needs a session that signed in **with PAM**. A HomeAcces-only account never gets one,
  even if it has the same name. Local accounts cannot be registered under system usernames.
- The WebSocket is opened with a 30-second, single-use ticket from a CSRF-protected POST, and the
  `Origin` must match the host. This blocks cross-site WebSocket hijacking.
- PAM users in `sudo` (`HOMEACCES_PAM_ADMIN_GROUP`) are web admins. That group can already become
  root, so this grants nothing new.
- xterm.js needs `style-src 'unsafe-inline'` in the CSP, which nginx allows. Scripts are still `'self'` only.
- Turn the feature off with `HOMEACCES_PAM_ENABLED=0` and `sudo systemctl disable --now homeacces-privd`.

**SSH menu** — every user can open SSH connections from the Pi, so:

- `homeacces-ssh` runs the OpenSSH client as the unprivileged `homeacces` user (hardened systemd
  unit), never as root. Connections come from the Pi's address, so they can reach hosts on its network.
- `ssh` is started without a config file and without escape character, port/agent/X11 forwarding,
  local commands, agent or connection sharing; it only offers the connection's own key. Host, port
  and remote user are validated so they cannot be read as options.
- Each web user has their own `known_hosts` file: new host keys are confirmed in the terminal, changed
  ones are refused (until the user forgets the old key), and users never trust each other's servers.
- Saved private keys are stored **unencrypted in the database** (written to a private 0600 temp file
  only while a session runs). Prefer keys protected by a passphrase, which is asked for in the terminal.
- Admins can switch the SSH menu off in Admin → Settings (default: `HOMEACCES_SSH_ENABLED`), or stop
  it entirely with `sudo systemctl disable --now homeacces-ssh`.

**General**

- Passwords hashed with scrypt (Werkzeug). JWTs are HS256, signed with `HOMEACCES_SECRET_KEY`.
- Cookies are `HttpOnly`, `Secure`, `SameSite=Strict`. The CSRF token is bound to the JWT, so a
  forged `csrf_token` cookie is rejected.
- Refresh tokens are stored in SQLite and grouped into sessions. Access tokens name their session, so
  logout, password changes and revocations end them at once. Reusing a rotated refresh token more than
  10 seconds after rotation revokes all of that user's sessions (the grace period covers two tabs
  refreshing together).
- Login throttling: 5 failures per IP+username in 15 minutes (app), plus 10 req/min per IP on
  login, register, token and password change (nginx). Wrong current passwords on password change are
  throttled per account too.
- Self-registration is off by default. When it is on, a system username is reported as "username
  already taken", so registration cannot be used to list the Pi's accounts.
- gunicorn listens on the Unix socket `/run/homeacces-api.sock` (from `homeacces.socket`), which only
  nginx (`www-data`) can connect to, so local users cannot bypass nginx or spoof their IP. The systemd
  unit runs it as the unprivileged `homeacces` user with a read-only filesystem except `/var/lib/homeacces`.
- Scripts that run Flask commands (`install.sh`, `create-admin.sh`) let the `homeacces` user read the
  env file itself; the secret key never appears on a command line (visible to all users in `ps`).
- Each terminal service accepts at most 32 connections that have not authenticated yet; nginx allows 8
  terminal connections (local and SSH together) per IP.
- nginx sends HSTS, CSP (`default-src 'self'`), `X-Frame-Options: DENY` and `nosniff`.

## Adapting it

To add a new resource, copy `backend/app/items.py` (blueprint + `@require_auth()`), add its table
to `schema.sql`, register the blueprint in `app/__init__.py`, and add a page in `frontend/src/pages/`.

## License

[MIT](LICENSE) © 2026 Juan Maria Gomez Lopez <juan-maria.gomez-lopez@tutamail.com>
