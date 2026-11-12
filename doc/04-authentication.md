# 4. Authentication and security

[← Database](03-database.md) · [Index](README.md) · Next: [Frontend →](05-frontend.md)

Code: `backend/app/auth.py`, `backend/app/api_tokens.py`, `backend/app/terminal.py`, `backend/app/ssh.py`, `backend/app/privd.py`,
`backend/homeacces_privd/`, `backend/homeacces_ssh/`, `backend/webterm.py`, `frontend/src/api.js`,
`frontend/src/AuthContext.jsx`.

## 4.1 Overview

```mermaid
flowchart LR
    subgraph Methods["Sign-in methods"]
        L["local<br/>username + password in SQLite<br/>(scrypt)"]
        P["pam<br/>Linux system account<br/>(via homeacces-privd)"]
    end
    subgraph Clients["Client types"]
        BR["Browser<br/>POST /api/auth/login<br/>→ HttpOnly cookies + CSRF"]
        SC["Script<br/>POST /api/auth/token<br/>→ Bearer token in body"]
        PAT["Script<br/>personal access token hat_…<br/>(created on the Account page)"]
    end
    L --> BR & SC
    P --> BR & SC
    BR --> AMR["JWT claim amr = 'local' | 'pam'"]
    SC --> AMR
    PAT --> AMRT["amr = 'token'<br/>no terminals, no token or<br/>password management"]
    AMR --> T{"amr == 'pam'?"}
    T -- yes --> TERM["Web terminal allowed"]
    T -- no --> NOTERM["No terminal"]
    AMR --> SSHM["SSH menu: any method<br/>(unless an admin disabled it)"]
```

| Concept | Implementation |
|---|---|
| Password storage | Werkzeug `generate_password_hash` (scrypt); constant-time dummy check for unknown users |
| Session tokens | JWT HS256 signed with `HOMEACCES_SECRET_KEY` |
| Access token | 15 min, cookie `access_token` (HttpOnly, path `/`) or `Authorization: Bearer` |
| Refresh token | 7 days, cookie `refresh_token` (HttpOnly, path `/api/auth` only), stored in DB by `jti` |
| Session | All tokens of one sign-in share a `session_id` (access tokens carry it as `sid`); revoking it ends the access tokens immediately |
| CSRF | Double-submit token **bound into the JWT**: readable cookie `csrf_token` must be echoed in `X-CSRF-Token` and match the `csrf` claim |
| Cookie flags | `HttpOnly` (except CSRF), `Secure`, `SameSite=Strict` |
| Brute force | App: 5 failures / 15 min per IP+method+username → 429, and 5 wrong current passwords / 15 min per account on password change. nginx: 10 req/min per IP on login/register/token/password |
| Roles | `user`, `admin` — checked by `require_auth(roles)` on every request from the DB row (not from the token) |
| Personal access tokens | Opaque `hat_…` secrets (256 bits) a user creates for scripts; only their SHA-256 is stored, see §4.10 |

## 4.2 Tokens

```mermaid
classDiagram
    class AccessToken {
        sub : user id (string)
        role : user | admin
        type : "access"
        amr : local | pam
        csrf : random 32 bytes, null for /token
        sid : session id, checked on every request
        jti : uuid4
        iat
        exp : iat + 15 min
    }
    class RefreshToken {
        sub : user id
        type : "refresh"
        amr : local | pam
        csrf : same value as access
        jti : uuid4 → refresh_tokens.jti
        iat
        exp : iat + 7 days
        session : refresh_tokens.session_id, server side
    }
    class TerminalTicket {
        sub : user id
        type : "terminal"
        pam_user : system username
        jti : uuid4, single use
        iat
        exp : iat + 30 s
    }
    class SshTicket {
        sub : user id
        type : "ssh"
        profile_id : ssh_profiles.id of the user
        jti : uuid4, single use
        iat
        exp : iat + 30 s
    }
```

Decoding (`_decode`) requires `exp, iat, sub, type, jti`, checks the expected `type` (an access
token cannot be used as a refresh token and vice versa) and that `amr` is `local` or `pam`.
Errors give `401 token_expired` or `401 invalid_token`.

The **role in the token is informational only**: `require_auth` reloads the user row on every
request, so a demotion or deletion takes effect immediately. It also checks that the token's
session (`sid`) still has an active row in `refresh_tokens`, so logout and revocation take effect
immediately too.

## 4.3 Local login (browser)

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant R as Login.jsx / AuthContext
    participant N as nginx
    participant A as Flask /api/auth/login
    participant D as SQLite

    U->>R: username + password, method "local"
    R->>N: POST /api/auth/login {username, password, method}
    N->>N: limit_req 10 r/min per IP (burst 5)
    N->>A: proxy
    A->>D: COUNT login_failures for "ip|local:username" in window
    alt ≥ 5 failures
        A->>D: activity_log auth.login failure "throttled"
        A-->>R: 429 too many failed attempts
    end
    A->>D: SELECT user WHERE username = ? (NOCASE)
    A->>A: check_password_hash(hash or DUMMY_HASH)
    alt invalid
        A->>D: INSERT login_failures, activity failure
        A-->>R: 401 invalid username or password
    else valid
        A->>D: DELETE login_failures for key, activity success
        A->>A: csrf = token_urlsafe(32)
        A->>D: INSERT refresh_tokens (jti, user_id, new session_id, expires_at)
        A-->>R: 200 {user, session:{method:"local"}}<br/>Set-Cookie access_token, refresh_token, csrf_token
    end
    R->>R: setUser(), setSession() → redirect to /items
```

Cookies set on success:

| Cookie | HttpOnly | Path | Max-Age | Content |
|---|---|---|---|---|
| `access_token` | ✅ | `/` | 15 min | access JWT |
| `refresh_token` | ✅ | `/api/auth` | 7 days | refresh JWT |
| `csrf_token` | ❌ (JS must read it) | `/` | 7 days | same random value as the `csrf` claim |

All three are `Secure` and `SameSite=Strict`.

## 4.4 CSRF protection

```mermaid
flowchart TD
    A["State-changing request<br/>(POST/PUT/DELETE)"] --> B{"Authenticated by<br/>Bearer header?"}
    B -- yes --> OK["No CSRF check<br/>(scripts can't be driven cross-site)"]
    B -- no, cookie --> C["api.js reads csrf_token cookie<br/>→ X-CSRF-Token header"]
    C --> D{"hmac.compare_digest(<br/>header, JWT.csrf)"}
    D -- equal --> OK2["proceed"]
    D -- different / missing --> E["403 csrf_failed"]
```

Because the expected value lives **inside the signed JWT**, an attacker who manages to plant a
`csrf_token` cookie (e.g. from a sibling subdomain) still cannot pass the check. `SameSite=Strict`
is a second, independent layer.

## 4.5 Token refresh and rotation

```mermaid
sequenceDiagram
    autonumber
    participant C as api.js
    participant A as Flask
    participant D as SQLite
    participant P as homeacces-privd

    C->>A: GET /api/items (access expired)
    A-->>C: 401 token_expired
    C->>A: POST /api/auth/refresh (refresh cookie + X-CSRF-Token)
    Note over C: concurrent 401s share one in-flight refresh
    A->>A: decode refresh JWT, check CSRF
    A->>D: UPDATE … SET revoked=2, rotated_at=now WHERE jti = ? AND revoked = 0
    Note over A,D: atomic: of concurrent requests exactly one rotates
    alt not rotated, and not "rotated by another request less than 10 s ago"
        A->>D: UPDATE refresh_tokens SET revoked=1 WHERE user_id = ? (all sessions)
        A->>D: activity auth.token_reuse (failure)
        A-->>C: 401 invalid_token
    else rotated now, or within the 10 s grace period (another tab)
        opt amr == "pam"
            A->>P: {"op":"pam_account","username":…}
            alt account locked / expired / removed from group
                A->>D: revoke all, activity auth.session_revoked
                A-->>C: 401 invalid_token
            else ok
                A->>D: sync role from groups
            end
        end
        A->>D: INSERT new refresh_tokens row (same session_id)
        A-->>C: 200 + new access, refresh and csrf cookies
        C->>A: retry GET /api/items
        A-->>C: 200
    end
```

If refresh fails, `api.js` dispatches a `session-expired` window event; `AuthContext` clears the
user and the router sends the user to `/login`.

### Session states

```mermaid
stateDiagram-v2
    [*] --> Anonymous
    Anonymous --> Active: login / register (cookies set)
    Active --> AccessExpired: 15 min elapse
    AccessExpired --> Active: POST /refresh (rotation)
    AccessExpired --> Anonymous: refresh fails / 7 days elapse
    Active --> Anonymous: logout (session revoked, cookies cleared)
    Active --> Anonymous: password change elsewhere or token reuse (next request → 401)
    Active --> Revoked: PAM account disallowed
    Revoked --> Anonymous: next refresh → 401
```

Logout, a password change and token-reuse detection revoke sessions in the database, and
`require_auth` rejects access tokens of revoked sessions at once. Bearer tokens from
`/api/auth/token` are backed by a session row too, so a password change ends them as well.
Two cases still wait for the next refresh (≤ 15 min): a system account that was locked or
removed from `homeacces-pam`, and a terminal or SSH session that is already open (it stays open
until it is closed or idle).

## 4.6 PAM (system account) login

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as Flask (user homeacces)
    participant S as privd.sock
    participant H as homeacces-privd (root)
    participant L as libpam (/etc/pam.d/homeacces)
    participant D as SQLite

    B->>A: POST /api/auth/login {method:"pam", username:"demo", password}
    A->>A: PAM_ENABLED? username matches ^[a-z_][a-z0-9_.-]{0,31}$ ? throttle?
    A->>S: connect + {"op":"pam_auth","username":"demo","password":"…"}
    H->>H: SO_PEERCRED: peer uid ∈ {0, homeacces}?
    H->>L: pam_start("homeacces","demo") → pam_authenticate → pam_acct_mgmt
    L-->>H: PAM_SUCCESS
    H->>H: policy: uid ≥ 1000 and in group homeacces-pam
    H-->>A: {"ok":true,"username":"demo","groups":[…,"sudo"]}
    A->>D: upsert users row (auth_source='pam', password_hash='!')<br/>role = admin if "sudo" ∈ groups else user
    Note over A,D: 409 if a *local* account already uses this name
    A-->>B: cookies with amr="pam"
```

Safeguards:

- Only members of **`homeacces-pam`** with **uid ≥ 1000** can sign in; `root` and daemons never can.
- The policy is re-checked on **every refresh** (every 15 min) and **every terminal start**, so
  `gpasswd -d <user> homeacces-pam` or `usermod -L <user>` ends web access quickly.
- New local accounts cannot be registered under an existing system username (`pwd.getpwnam`),
  and a PAM login cannot take over a local account with the same name. Self-registration answers
  "username already taken" for system usernames, so it cannot be used to list the server's accounts
  (only admins creating users see "reserved for a system account"). Self-registration is off by default.
- PAM users in `HOMEACCES_PAM_ADMIN_GROUP` (default `sudo`) become web admins — they can already
  become root, so nothing new is granted.
- PAM users cannot change their password, name or role in the web UI: the system is authoritative.

## 4.7 Web terminal authentication

The WebSocket cannot carry the CSRF header and is handled by a different process, so it uses a
**ticket**:

```mermaid
sequenceDiagram
    autonumber
    participant T as Terminal.jsx
    participant A as Flask /api/terminal/ticket
    participant N as nginx
    participant H as homeacces-privd :8001
    participant SH as runuser --login demo

    T->>A: POST /api/terminal/ticket (cookie + CSRF)
    A->>A: require amr=="pam" and auth_source=="pam"
    A->>A: activity terminal.open
    A-->>T: {ticket (JWT type=terminal, 30 s), expires_in:30}
    T->>N: new WebSocket wss://host/api/terminal/ws
    N->>H: Upgrade (Host, Origin, X-Real-IP)
    H->>H: path == /api/terminal/ws, Origin host == Host
    T->>H: {"type":"auth","ticket":…,"cols":120,"rows":40}  (≤ 10 s)
    H->>H: verify JWT signature/type/exp, jti not used before
    H->>H: policy (uid, group) + pam_acct_mgmt
    H->>H: limits: ≤ 4 per user, ≤ 16 total
    H->>SH: openpty() + setsid --ctty runuser --login demo
    loop session
        T->>H: {"type":"input","data":"ls\r"} / {"type":"resize",…}
        H-->>T: binary frames (raw terminal output)
    end
    Note over H: 30 min without input → close "idle timeout"
```

| Threat | Mitigation |
|---|---|
| Cross-site WebSocket hijacking | Ticket comes from a CSRF-protected POST; `Origin` must equal `Host` |
| Ticket replay | `jti` remembered until expiry; 30 s lifetime |
| Local account named like a system user | Requires `amr == "pam"` **and** `auth_source == "pam"` |
| Removed / locked system user | Policy + `pam_acct_mgmt` on every terminal start |
| Resource exhaustion | Per-user (4) and global (16) session limits, max 32 unauthenticated handshakes, nginx max 8 connections per IP, 256 KiB message cap, output backpressure, idle timeout |

## 4.8 SSH menu authentication and security

The SSH terminal uses the same ticket scheme (`webterm.py` is shared), with a ticket of
`type: "ssh"` naming one of the user's saved connections. Any signed-in user may get one, whatever
the sign-in method, unless an admin switched the SSH menu off.

```mermaid
sequenceDiagram
    autonumber
    participant T as SshTerminals (TerminalView)
    participant A as Flask /api/ssh/ticket
    participant N as nginx
    participant S as homeacces-ssh :8002 (user homeacces)
    participant D as SQLite
    participant R as Remote SSH server

    T->>A: POST /api/ssh/ticket {profile_id} (cookie + CSRF)
    A->>A: ssh_enabled? (403 if off), profile owned by g.user? (404 if not)
    A->>A: activity ssh.open
    A-->>T: {ticket (JWT type=ssh, sub, profile_id, 30 s), expires_in:30}
    T->>N: new WebSocket wss://host/api/ssh/ws
    N->>S: Upgrade (Host, Origin, X-Real-IP)
    S->>S: path == /api/ssh/ws, Origin host == Host
    T->>S: {"type":"auth","ticket":…,"cols":120,"rows":40}  (≤ 10 s)
    S->>S: verify JWT signature/type/exp, jti not used before
    S->>S: limits: ≤ 4 per user, ≤ 16 total
    S->>D: SELECT profile WHERE id = profile_id AND owner_id = sub
    S->>S: private key? → 0600 temp file in a private temp dir
    S->>R: openpty() + setsid --ctty ssh -F /dev/null -e none -o … -- host
    S-->>T: ssh prompts (new host key, password, key passphrase), answered in the terminal
    loop session
        T->>S: {"type":"input",…} / {"type":"resize",…}
        S-->>T: binary frames
    end
    Note over S: 30 min without input → close "idle timeout", key file deleted
```

| Threat | Mitigation |
|---|---|
| Cross-site WebSocket hijacking, ticket replay | Same as the local terminal: CSRF-protected ticket POST, `Origin` == `Host`, single-use `jti`, 30 s lifetime |
| Using someone else's connection | Profiles are filtered by `owner_id` in the API **and** again in homeacces-ssh against the ticket's `sub`; admins cannot see others' profiles either |
| Escaping from SSH to the server | `ssh` runs as the unprivileged `homeacces` user in a hardened unit (`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, no capabilities); no `ssh_config`, no escape character, no forwardings, no `LocalCommand`, no agent, no connection sharing |
| Option injection through host / user | Strict validation (no leading `-`, no `%`/`$`, no spaces), destination after `--` |
| Trusting another user's servers / MITM | One `known_hosts` file per web user, no global file; new keys are confirmed by the user, changed keys are refused until the user forgets the old one ("Forget host key") |
| Password theft from the database | Passwords and passphrases are never stored, only typed in the terminal |
| Resource exhaustion | Per-user (4) and global (16) session limits, max 32 unauthenticated handshakes, shared nginx limit of 8 terminal connections per IP, idle timeout |

**Saved private keys** are stored unencrypted in `ssh_profiles.private_key`, and while a session runs
also in a 0600 temp file in the service's private `/tmp`. Anyone who can read the database (root,
the `homeacces` user, database backups) can read them. They are never sent back to the browser.
Users should save only keys protected by a passphrase (`ssh` asks for it in the terminal).

The SSH client connects **from the server**, so users can reach any host the server can reach (including
the local network). Admins can switch the SSH menu off in Admin → Settings (new tickets are refused;
open sessions continue until closed or idle), or remove the feature with
`sudo systemctl disable --now homeacces-ssh`.

## 4.9 Bearer tokens for scripts

```bash
TOKEN=$(curl -sk https://server.local/api/auth/token -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -sk https://server.local/api/items -H "Authorization: Bearer $TOKEN"
```

`/api/auth/token` returns only an access token (no refresh token); request a new one after
`expires_in` seconds. No CSRF header is needed for Bearer requests. A password change or
"revoke all" (token reuse) invalidates bearer tokens immediately. For scripts that run unattended,
a personal access token (§4.10) avoids storing the account password.

## 4.10 Personal access tokens

Each user can create long-lived tokens for scripts on the **Account** page
(`POST /api/auth/tokens`, code in `backend/app/api_tokens.py`), and use them as Bearer tokens:

```bash
curl -sk https://server.local/api/items -H "Authorization: Bearer hat_Q2xv9A…"
```

```mermaid
sequenceDiagram
    autonumber
    participant S as Script
    participant A as Flask require_auth
    participant D as SQLite
    participant P as homeacces-privd

    S->>A: GET /api/items  Authorization: Bearer hat_…
    A->>D: SELECT api_tokens WHERE token_hash = sha256(token)
    alt unknown / deleted
        A-->>S: 401 invalid_token
    else expires_at passed
        A-->>S: 401 token_expired
    end
    opt owner is a PAM user and last check ≥ 15 min ago
        A->>P: {"op":"pam_account","username":…}
        alt account locked / expired / removed from group
            A-->>S: 401 invalid_token
        else ok
            A->>D: sync role from groups, checked_at = now
        end
    end
    A->>D: last_used_at / last_used_ip (at most once a minute)
    A->>A: role check on the users row, g.claims = {type:"api_token", amr:"token", pat_id}
    A-->>S: 200
```

| Property | Implementation |
|---|---|
| Format | `hat_` + `secrets.token_urlsafe(32)` (256 bits). The prefix tells it apart from a JWT and makes leaked tokens easy to grep for |
| Storage | Only the hex SHA-256 (`api_tokens.token_hash`) and a hint (`hat_` + 6 characters). The secret is returned once, by the create call |
| Lifetime | 1–365 days, or no expiry (`expires_in_days: null`); the UI defaults to 90 days |
| Creation | Needs a browser session (CSRF) **and the current password** (system password through PAM for PAM users), throttled 5 failures / 15 min per account, so a stolen session cannot quietly mint a lasting credential. At most 20 tokens per user, names unique per user |
| Scope | Acts as the owner with the owner's *current* role. Refused with 403 by `require_auth(allow_api_token=False)` on: token management, password change, terminal and SSH tickets |
| Revocation | Deleting a token (Account page) ends it at once. A password change by the user or an admin deletes all of the user's tokens; deleting the user deletes them (cascade). PAM accounts are re-checked every 15 min (`HOMEACCES_ACCESS_TTL_MIN`) |
| CSRF | Not applicable: a token is only accepted from the `Authorization` header, never from a cookie |

## 4.11 Defence in depth

```mermaid
flowchart TB
    subgraph L1["nginx"]
        a1["TLS 1.2/1.3 only, HTTP→HTTPS"]
        a2["HSTS, CSP default-src 'self',<br/>X-Frame-Options DENY, nosniff, Referrer-Policy"]
        a3["Rate limit on auth endpoints,<br/>connection limit on the terminals"]
        a4["client_max_body_size 64k"]
    end
    subgraph L2["Flask API"]
        b1["JWT + CSRF + SameSite=Strict"]
        b2["Input validation on every field"]
        b3["Parameterised SQL, quoted identifiers"]
        b4["Login + password-change throttling,<br/>refresh rotation, reuse detection,<br/>instant session revocation"]
        b5["404 for others' items, last-admin protection"]
        b6["Cache-Control: no-store on API"]
    end
    subgraph L3["systemd sandbox (homeacces.service)"]
        c1["User homeacces, NoNewPrivileges,<br/>empty CapabilityBoundingSet"]
        c2["ProtectSystem=strict, ProtectHome,<br/>ReadWritePaths=/var/lib/homeacces only"]
        c3["Unix socket only nginx can connect to"]
    end
    subgraph L4["Privileged helper"]
        d1["Root code is root-owned in /opt/homeacces"]
        d2["SO_PEERCRED on Unix socket (0660)"]
        d3["Own policy check on every operation"]
    end
    subgraph L5["SSH service (homeacces-ssh)"]
        e1["User homeacces, hardened unit,<br/>keys only in PrivateTmp while in use"]
        e2["Locked-down ssh command line"]
        e3["Per-user known_hosts"]
    end
    L1 --> L2 --> L3 --> L4
    L3 --> L5
```

The CSP allows `style-src 'unsafe-inline'` because xterm.js sets inline styles; **scripts remain
`'self'` only**. `connect-src` permits `wss://<host>` for the terminals.

To disable the PAM/terminal feature entirely: set `HOMEACCES_PAM_ENABLED=0` and run
`sudo systemctl disable --now homeacces-privd`. To disable the SSH menu: switch it off in Admin →
Settings (or `HOMEACCES_SSH_ENABLED=0` before an admin changed it) and run
`sudo systemctl disable --now homeacces-ssh`.

## 4.12 Token log

Besides the activity log (what users did), the API writes a diagnostic log of what happened to
their **tokens** (`backend/app/authlog.py`, logger `homeacces.auth`, stderr → journald):

```bash
journalctl -u homeacces -g homeacces.auth -f
```

```text
INFO homeacces.auth: event=token.issued type="access" user_id=3 role="user" amr="local" sid="c69b…" jti="b7ba…"
     iat="2026-11-12T09:33:21Z" exp="2026-11-12T09:48:21Z" user="alice" fp="aabda5f39c9a" delivery="cookie"
     ip="192.168.1.20" path="/api/auth/login"
```

| Event | Level | When |
|---|---|---|
| `token.issued` | INFO | Access or refresh token created (login, register, `/token`, refresh, password change); `delivery` = `cookie` or `bearer` |
| `ticket.issued` | INFO | Terminal or SSH ticket created, with the `sid` of the session that asked for it |
| `token.verified` | DEBUG | Access token accepted by `require_auth`; `via` = `cookie` or `bearer` |
| `token.missing` | DEBUG | Protected endpoint called without a token |
| `token.rejected` | INFO / WARNING | `reason`: `expired` (INFO, routine), invalid signature or format, wrong type, session revoked, user deleted, refresh `jti` unknown, unknown or revoked personal access token, system account no longer allowed |
| `csrf.failed` | WARNING | Header missing or not matching the token's `csrf` claim |
| `access.forbidden` | WARNING | Valid token, but the user's role is not allowed |
| `refresh.rotated` | INFO | Refresh token exchanged for a new pair (same `sid`) |
| `refresh.grace_reuse` | INFO | Just-rotated token reused within the grace period (second tab) |
| `refresh.reuse_detected` | WARNING | Rotated/revoked refresh token replayed: all of the user's sessions are revoked |
| `session.revoke_all` | INFO / WARNING | All sessions of a user revoked, with `reason` and `tokens_revoked` |
| `session.logout` | INFO | Session ended by logout, with `tokens_revoked` |
| `api_token.created` | INFO | Personal access token created, with `pat_id`, `fp` and `expires` |
| `api_token.revoked` | INFO | Personal access token deleted by its owner |
| `api_token.revoke_all` | INFO | All personal access tokens of a user deleted (password change or reset), with `tokens_revoked` |

**Never logged:** the token string, its signature, the CSRF value or any password. A token is
identified by its `jti` and `fp` (first 12 hex digits of the SHA-256 of the whole token), so a
token seen in a browser or script can be matched against the log (`printf %s "$TOKEN" | sha256sum`)
without the log becoming a source of usable credentials. For a personal access token, `fp` is also
the start of its `api_tokens.token_hash`. For rejected tokens whose signature could not be verified
the claims are shown with `claims_verified=false`: they may be forged.

Set `HOMEACCES_AUTH_LOG_LEVEL` to `DEBUG` to trace every authenticated request, or `WARNING` to
keep only failures. The terminal services (`homeacces-privd`, `homeacces-ssh`) log each ticket they
accept or refuse (`ticket accepted` / `ticket rejected`, with `jti`, user id, `fp` and the reason,
e.g. `already used (replay?)`).
