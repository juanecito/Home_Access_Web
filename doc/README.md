# HomeAcces — technical documentation

This folder explains how the HomeAcces website works, from the browser down to the SQLite file
and the Linux system accounts.

| # | Document | What it covers |
|---|---|---|
| 1 | [Architecture overview](01-architecture.md) | Components, processes, ports, trust boundaries, request routing |
| 2 | [Backend (Flask API)](02-backend.md) | App factory, blueprints, request lifecycle, every endpoint, activity log, item types, privileged helper, SSH service |
| 3 | [Database (SQLite)](03-database.md) | Schema, ER diagram, dynamic `item_type_<id>` tables, migrations, maintenance |
| 4 | [Authentication & security](04-authentication.md) | Local and PAM login, JWT, cookies, CSRF, refresh rotation, throttling, web terminal and SSH tickets |
| 5 | [Frontend (React)](05-frontend.md) | Component tree, routing, API client, auth state, pages, web terminal, SSH menu |
| 6 | [Deployment & operations](06-deployment.md) | Install on the server, files on disk, nginx, systemd, configuration, dev container, updates, backups, troubleshooting |

## The project in one picture

```mermaid
flowchart LR
    B["Browser<br/>React SPA + xterm.js"]
    subgraph Server["Server (Debian 13)"]
        N["nginx :443<br/>TLS, headers, rate limit"]
        S[("/var/www/homeacces<br/>static React build")]
        A["gunicorn + Flask<br/>/run/homeacces-api.sock<br/>user: homeacces"]
        D[("SQLite<br/>/var/lib/homeacces")]
        P["homeacces-privd<br/>user: root<br/>WebSocket :8001 + Unix socket"]
        OS["Linux PAM<br/>+ login shells"]
        SSHS["homeacces-ssh<br/>user: homeacces<br/>WebSocket :8002"]
    end
    R["Remote SSH servers"]
    B -- "HTTPS" --> N
    N -- "/ , /assets" --> S
    N -- "/api/*" --> A
    N -- "/api/terminal/ws" --> P
    N -- "/api/ssh/ws" --> SSHS
    A --> D
    A -- "privd.sock<br/>(PAM checks)" --> P
    P --> OS
    SSHS -- "saved connections" --> D
    SSHS -- "OpenSSH client" --> R
```

## Tech stack

| Layer | Technology | Version (pinned) |
|---|---|---|
| Reverse proxy / static files | nginx | Debian 13 package |
| API | Python 3, Flask | Flask 3.1.3 |
| WSGI server | gunicorn (2 workers) | 23.0.0 |
| Tokens | PyJWT (HS256) | 2.15.0 |
| Terminal transport | `websockets` (asyncio) | 14.1 |
| Database | SQLite 3 (WAL mode) | Debian 13 package |
| UI | React + React Router | 18.3.1 / 6.30.6 |
| Build tool | Vite (Rolldown) | 8.3.5 |
| Terminal emulator | xterm.js + fit addon | 5.5.0 / 0.10.0 |
| System login | Linux-PAM through `ctypes` | Debian 13 package |
| SSH client | OpenSSH (`openssh-client`) | Debian 13 package |

## Reading the diagrams

The diagrams are written in [Mermaid](https://mermaid.js.org/). They render directly on GitHub,
GitLab, and in VS Code (built-in Markdown preview with the *Markdown Preview Mermaid Support*
extension). Elsewhere, paste a block into <https://mermaid.live> to see it.

## Source map

```
backend/
  wsgi.py                 entry point for gunicorn / flask CLI  → see 02-backend.md
  app/                    the Flask API (runs as user homeacces) → 02, 03, 04
  homeacces_privd/        the root helper: PAM + terminal        → 02 (§ privileged helper), 04
  homeacces_ssh/          the SSH terminal service (homeacces)   → 02 (§ SSH service), 04
  webterm.py              WebSocket ↔ PTY bridge of both         → 02
  tests/                  pytest suite
frontend/
  src/                    React SPA                              → 05-frontend.md
deploy/                   install scripts, nginx, systemd, PAM   → 06-deployment.md
.devcontainer/            Docker dev environment (Debian 13)     → 06-deployment.md
```

## License

HomeAcces is released under the [MIT License](../LICENSE).
Copyright © 2026 Juan Maria Gomez Lopez <juan-maria.gomez-lopez@tutamail.com>.
