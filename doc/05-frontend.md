# 5. Frontend (React single-page application)

[← Authentication](04-authentication.md) · [Index](README.md) · Next: [Deployment →](06-deployment.md)

The UI lives in `frontend/`. It is a **React 18** single-page application built with **Vite**.
In production, the build output (`frontend/dist/`) is plain static files served by nginx from
`/var/www/homeacces`; there is no Node.js process on the Pi at runtime.

| Dependency | Use |
|---|---|
| `react`, `react-dom` 18.3.1 | UI rendering |
| `react-router-dom` 6.30.6 | Client-side routing (`BrowserRouter`) |
| `@xterm/xterm` 5.5.0, `@xterm/addon-fit` 0.10.0 | Terminal emulator (web terminal and SSH) |
| `vite` 8.3.5, `@vitejs/plugin-react` 6.1.2 | Dev server with hot reload, production bundler (needs Node.js ≥ 20.19; Debian 13 ships 20.19.2) |

No UI framework and no state library: one global stylesheet (`src/styles.css`) and one React
context for authentication.

## 5.1 File structure

```
frontend/
├── index.html               <div id="root"> + <script type="module" src="/src/main.jsx">
├── vite.config.js           dev proxy /api → :8000, /api/terminal/ws → :8001, /api/ssh/ws → :8002;
│                            build writes dist/licenses.md
├── package.json
└── src/
    ├── main.jsx             mounts <StrictMode><BrowserRouter><AuthProvider><App/>
    ├── App.jsx              top bar + routes + <Protected> guard, SSH sessions provider
    ├── AuthContext.jsx      user / session / methods state, login/register/logout
    ├── api.js               fetch wrapper: cookies, CSRF header, auto-refresh, ApiError
    ├── styles.css           all styles
    ├── Avatar.jsx           <img> with default silhouette
    ├── AvatarSettings.jsx   upload → crop/resize to 60×60 in a <canvas>
    ├── PasswordInput.jsx    password field with show/hide button
    ├── TerminalView.jsx     reusable xterm.js ↔ WebSocket terminal (local and SSH)
    └── pages/
        ├── Login.jsx        sign in (local / system account) and register
        ├── Account.jsx      password change + avatar
        ├── Terminal.jsx     local shell: TerminalView on /api/terminal/ws
        ├── ssh/
        │   ├── SshSection.jsx     tab bar (Connections + one tab per session), routes, destination()
        │   ├── SshConnections.jsx saved connections: list, add, edit, delete, forget host key, connect
        │   └── SshSessions.jsx    open sessions context; SshTerminals: one TerminalView per session
        ├── items/
        │   ├── ItemsSection.jsx   tabs: Items | Item types
        │   ├── Items.jsx          list, search, filter, pagination, create/edit
        │   ├── ItemTypes.jsx      type editor (admins edit, others read)
        │   └── fields.jsx         typed field inputs and conversions
        └── admin/
            ├── Admin.jsx          tabs: Users | Settings | Activity log
            ├── Users.jsx          user table, create, edit dialog, delete
            ├── Settings.jsx       registration on/off, SSH terminal on/off
            └── Activity.jsx       filtered, paginated activity log
```

## 5.2 Component tree

```mermaid
flowchart TB
    M["main.jsx"] --> SM["StrictMode"] --> BR["BrowserRouter"] --> AP["AuthProvider<br/>(AuthContext)"] --> APP["App"]
    APP --> TOP["header.topbar<br/>NavLinks + Avatar + Log out"]
    APP --> SP["SshSessionsProvider<br/>(open SSH sessions)"]
    APP --> RT["Routes"]
    APP --> ST["SshTerminals<br/>(always mounted when signed in)"] --> TV
    RT --> LG["/login → Login"]
    RT --> IS["/items/* → Protected → ItemsSection"]
    IS --> IT["index → Items"]
    IS --> TY["types → ItemTypes"]
    IT --> IF["ItemForm"] --> FI["FieldInput (fields.jsx)"]
    TY --> TF["TypeForm"]
    RT --> AC["/account → Protected → Account"]
    AC --> PI["PasswordInput"]
    AC --> AV["AvatarSettings → Avatar"]
    RT --> TE["/terminal → Protected pam → Terminal"]
    TE --> TV["TerminalView<br/>(xterm.js)"]
    RT --> SS["/ssh/* → Protected → SshSection"]
    SS --> TB["nav.tabs: Connections | one tab per session"]
    SS --> SC["index → SshConnections"] --> PF["ProfileForm"]
    SS --> SSE["t/:sid → SessionTab<br/>(redirects unknown sessions)"]
    RT --> AD["/admin/* → Protected admin → Admin"]
    AD --> US["users → Users → EditUser (dialog)"]
    AD --> SE["settings → Settings"]
    AD --> AL["activity → Activity"]
```

## 5.3 Routing and guards

| Path | Component | Guard | Shown in nav when |
|---|---|---|---|
| `/login` | `Login` | redirects to `/` if already signed in | — |
| `/` | → `/items` | | |
| `/items` | `Items` | signed in | always |
| `/items/types` | `ItemTypes` | signed in | (tab) |
| `/account` | `Account` | signed in | always (avatar + name) |
| `/terminal` | `Terminal` | signed in **with PAM** | `session.method === 'pam'` |
| `/ssh` | `SshConnections` | signed in | always |
| `/ssh/t/:sid` | session tab (terminal drawn by `SshTerminals`) | signed in | (Connect button, tab bar) |
| `/admin/users`, `/admin/settings`, `/admin/activity` | `Users`, `Settings`, `Activity` | `role === 'admin'` | admin |
| `/users` | → `/admin/users` | | legacy link |
| `*` | → `/` | | |

```mermaid
flowchart TD
    R["Protected (props: admin, pam)"] --> U{"user?"}
    U -- no --> L["Navigate /login"]
    U -- yes --> A{"admin required<br/>and role ≠ admin?"}
    A -- yes --> H["Navigate /"]
    A -- no --> P{"pam required and<br/>session.method ≠ pam?"}
    P -- yes --> H
    P -- no --> C["render children"]
```

These guards are for **user experience only**. Every permission is enforced again by the API
(`require_auth`), so hiding a link is never the only protection.

Because the app uses `BrowserRouter` (real paths, not `#/`), nginx must return `index.html` for
unknown paths — that is the `try_files $uri /index.html` rule.

## 5.4 Authentication state (`AuthContext.jsx`)

```mermaid
sequenceDiagram
    autonumber
    participant AP as AuthProvider (mount)
    participant API as api.js
    participant S as Flask

    AP->>API: GET /api/auth/methods
    API->>S: …
    S-->>AP: {local, pam, registration} → setMethods
    AP->>API: GET /api/auth/me
    alt access cookie valid
        S-->>AP: {user, session}
    else 401
        AP->>API: POST /api/auth/refresh
        alt refresh cookie valid
            S-->>AP: {user, session} (new cookies)
        else
            AP->>AP: user = null
        end
    end
    AP->>AP: loading = false → App renders routes
```

The context exposes:

| Value | Meaning |
|---|---|
| `user` | `{id, username, role, auth_source, created_at, avatar}` or `null` |
| `session` | `{method: 'local' \| 'pam'}` — how *this* session signed in |
| `methods` | `{local, pam, registration}` — what the login page should offer |
| `loading` | `true` until the initial session check finishes (App shows "Loading…") |
| `login(u, p, method)`, `register(u, p)`, `logout()` | call the API and update state |
| `reloadUser()` | re-fetch `/me` (after avatar change or self-rename) |

It also listens for the `session-expired` window event (emitted by `api.js`) to drop the user.

## 5.5 API client (`api.js`)

```mermaid
flowchart TD
    A["api.get/post/put/del(path, body)"] --> B["send(): fetch with<br/>credentials: same-origin,<br/>JSON body, Accept JSON"]
    B --> C{"method ≠ GET/HEAD?"}
    C -- yes --> D["add X-CSRF-Token = cookie csrf_token"]
    C -- no --> E
    D --> E["response"]
    E --> F{"401 and path not<br/>/api/auth/… ?"}
    F -- yes --> G["refreshSession()<br/>(one shared in-flight promise)"]
    G --> H{"refresh ok?"}
    H -- yes --> B2["retry the original request once"] --> I
    H -- no --> J["dispatch 'session-expired'"] --> I
    F -- no --> I{"status"}
    I -- 204 --> N["return null"]
    I -- "2xx" --> K["return JSON"]
    I -- "4xx/5xx" --> X["throw ApiError(status, data.error)"]
```

Pages catch `ApiError` and display `err.message`, which is the server's `error` text
(e.g. "username already taken").

## 5.6 Pages

### Login (`pages/Login.jsx`)

```
┌──────────── Sign in ────────────┐
│ [HomeAcces account|System account]  ← only if methods.pam
│ Username  [______________]      │
│ Password  [__________] (👁)     │
│ [ Sign in ]                     │
│ No account? Register            │  ← only if methods.registration (off by default)
└─────────────────────────────────┘
```

Switches between *login* and *register* modes; registration always creates a local account.

### Items (`pages/items/Items.jsx`)

- Loads `/api/item-types` once and `/api/items?limit=20&offset=…&q=…&type_id=…` on every change.
- Search box (title/description), type filter (all / no type / a specific type), pagination (20 per page).
- `ItemForm` for create and edit: choosing a type renders one `FieldInput` per field.
  The type can only be chosen at creation.
- `fields.jsx` converts between API values and form values (numbers are edited as text and
  converted with `toJson`; invalid numbers become `null` and are rejected by the server).

### Item types (`pages/items/ItemTypes.jsx`)

Everyone can see the list; admins get the `TypeForm` to add/edit/delete types. Existing fields
are sent back with their `id` so the server keeps their data; removing a row in the form drops
that column.

### Account (`pages/Account.jsx`)

Local accounts: change password (other sessions are signed out; after 5 wrong current passwords
the API answers 429 for 15 minutes). PAM accounts: an explanation
that the password is changed with `passwd`. Both: `AvatarSettings` — the image is cropped to a
centred square and scaled to 60×60 **in the browser** (`<canvas>`), then sent as a data URL.

### SSH (`pages/ssh/*`)

`SshConnections` loads `GET /api/ssh/profiles` and lists the user's connections as
`user@host[:port]`, with a *key* badge when a private key is saved. Each has **Connect**
(opens a new session tab, disabled when `enabled` is false), **Edit**, **Forget host key**
(`DELETE …/host-key`, after a confirmation) and **Delete**. `ProfileForm` (add and edit) has name,
host, port, remote user and an optional private key, pasted or loaded with **Load key file…**
(read in the browser, ≤ 16 KB). The saved key is never shown: an empty field keeps it, and
*Remove the saved key* sends `private_key: null`. Passwords are not part of the form.

Several sessions can be open at once, to different connections or several to the same one.
`SshSessionsProvider` (in `App`, keyed by the user so logging out closes them all) holds the list
`{id, profile, label, status}`; **Connect** adds one and navigates to `/ssh/t/<id>`. `SshSection`
shows a tab bar: **Connections**, then one tab per session with a status dot (connecting /
connected / closed) and a **×** button (or middle click) that closes it, after a confirmation
while it is connected. A second session to the same connection is labelled *Name (2)*, and so on.

`SshTerminals` is rendered by `App` below the routes and renders one `TerminalView` per session
(`wsPath="/api/ssh/ws"`, ticket from `POST /api/ssh/ticket {profile_id}`); only the one of the
current `/ssh/t/<id>` is shown. The others stay mounted and connected, also while the user is on
another page. Session ids live only in the browser: after a reload `/ssh/t/<id>` redirects to
`/ssh`, and the page asks before unloading while a session is connected. homeacces-ssh refuses
sessions beyond `HOMEACCES_SSH_MAX_PER_USER` (default 4); the refusal is shown in that tab's terminal.

### Admin (`pages/admin/*`)

| Tab | Calls | Features |
|---|---|---|
| Users | `GET/POST /api/users`, `PUT/DELETE /api/users/<id>` | Table with avatar, role, source, last login; create form; `<dialog>` editor (local accounts only); delete with confirmation |
| Settings | `GET/PUT /api/admin/settings` | Toggle self-registration and the SSH terminal; shows log file and retention |
| Activity log | `GET /api/admin/activity` | Filters user / event (exact or `prefix.`) / result; 50 per page, Newer / Older |

## 5.7 Web terminals (`TerminalView.jsx`)

Both terminals use the same component. `Terminal.jsx` (local shell) and `SshTerminals` only pass
props: `title`, `wsPath` (`/api/terminal/ws` or `/api/ssh/ws`), `getTicket` (an async function that
POSTs to `/api/terminal/ticket` or `/api/ssh/ticket`), optional `actions`, and for SSH tabs `active`
(an inactive view is hidden but stays connected; it is refitted, repainted and focused when shown
again) and `onStatus` (reports `connecting` / `connected` / `closed` to the tab bar). The protocol is the same for both services (see `backend/webterm.py`).

```mermaid
sequenceDiagram
    autonumber
    participant C as TerminalView
    participant X as xterm.js
    participant API as api.js
    participant W as WebSocket

    C->>X: new Terminal() + FitAddon, open(host div)
    C->>X: fit (min 120 cols, scroll horizontally if narrower)
    C->>API: getTicket() → POST /api/terminal/ticket or /api/ssh/ticket
    API-->>C: {ticket}
    C->>W: new WebSocket(wss://host + wsPath), binaryType=arraybuffer
    W-->>C: onopen
    C->>W: {"type":"auth", ticket, cols, rows}
    C->>C: status = connected, focus
    loop
        X->>C: onData(keys)
        C->>W: {"type":"input", data}
        W-->>C: binary frame
        C->>X: term.write(Uint8Array)
        Note over C: ResizeObserver → fit → onResize → {"type":"resize",cols,rows}
    end
    W-->>C: onclose(reason)
    C->>X: print "[reason]", status = closed → Reconnect button
```

The `useEffect` depends on a `session` counter: clicking **Reconnect** increments it, which
disposes the old terminal and socket and builds new ones (with a new ticket). Cleanup on unmount
closes the socket (the service then hangs up the shell or `ssh`).

## 5.8 Development vs production

```mermaid
flowchart LR
    subgraph Dev["Development (npm run dev)"]
        B1["Browser :5173"] --> V["Vite dev server<br/>HMR, JSX on the fly"]
        V -- "/api" --> F1["Flask --debug :8000"]
        V -- "/api/terminal/ws (ws)" --> P1["privd :8001"]
        V -- "/api/ssh/ws (ws)" --> S1["homeacces-ssh :8002"]
    end
    subgraph Prod["Production (npm run build)"]
        SRC["src/*.jsx"] --> VB["vite build"] --> DIST["dist/index.html<br/>dist/assets/index-[hash].js|css<br/>dist/licenses.md"]
        DIST -- "install.sh rsync" --> WWW["/var/www/homeacces"]
        B2["Browser :443"] --> NG["nginx"] --> WWW
        NG -- "/api" --> G["gunicorn<br/>/run/homeacces-api.sock"]
    end
```

Same origin in both cases (the dev server proxies `/api`), so cookies, `SameSite=Strict` and the
CSRF flow behave identically. In dev, `HOMEACCES_COOKIE_SECURE=0` because Vite serves plain HTTP.

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173 (needs the API on :8000)
npm run build     # → dist/
npm run preview   # serve dist/ locally
```

The minifier removes license comments from the bundle, so the build writes the licenses of all
bundled dependencies (React, React Router, xterm.js, …) to `dist/licenses.md` instead
(`build.license` in `vite.config.js`). nginx serves it at `/licenses.md`.

## 5.9 Adding a page

1. Create `src/pages/MyPage.jsx`; call the API with `api.get/post/put/del` and show `err.message` on failure.
2. Add `<Route path="/mypage" element={<Protected><MyPage /></Protected>} />` in `App.jsx`
   (use `admin` or `pam` props if needed) and a `<NavLink>` in the top bar.
3. Add styles to `styles.css`. Avoid inline `<script>` or external resources — the CSP only
   allows same-origin scripts, styles, images (plus `data:` images) and connections.
