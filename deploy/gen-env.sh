#!/usr/bin/env bash
# Generate the production environment file for HomeAcces (default: /etc/homeacces/homeacces.env).
#
# The file is built from deploy/homeacces.env.example (the single source of defaults) with a fresh
# random HOMEACCES_SECRET_KEY and the options below applied. It is written atomically, owned by
# root:homeacces with mode 0640, so the services can read it but other users cannot.
#
# Usage: sudo ./deploy/gen-env.sh [options]
#   -o, --output FILE         where to write (default: /etc/homeacces/homeacces.env)
#       --print               print to stdout instead of writing a file
#   -f, --force               replace an existing file (a timestamped backup is kept);
#                             the existing secret is reused unless --rotate-secret is given
#       --rotate-secret       generate a new secret key (signs everyone out)
#   -u, --update              only add settings missing from an existing file, change nothing else
#       --no-pam              disable PAM login and the web terminal
#       --no-ssh              disable the SSH menu by default (admins can still enable it in the UI)
#       --registration        let anyone who can reach the site create an account
#       --no-registration     only admins can create accounts (default)
#       --pam-group GROUP     system group allowed to use PAM login (default: homeacces-pam)
#       --pam-admin-group G   system group whose PAM users are web admins ("" = none; default: sudo)
#       --access-ttl MIN      access token lifetime in minutes (default: 15)
#       --refresh-ttl DAYS    refresh token lifetime in days (default: 7)
#       --idle MIN            close terminals (local and SSH) after MIN minutes without input (default: 30)
#   -h, --help
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$SRC/deploy/homeacces.env.example"
OUTPUT=/etc/homeacces/homeacces.env
PRINT=0 FORCE=0 ROTATE=0 UPDATE=0
declare -A SET=()

die() { echo "gen-env: $*" >&2; exit 1; }
warn() { echo "gen-env: warning: $*" >&2; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit "${1:-0}"; }

need_value() { [[ $# -ge 2 ]] || die "$1 needs a value"; }
positive_int() { [[ "$2" =~ ^[1-9][0-9]{0,5}$ ]] || die "$1 must be a positive integer, got '$2'"; }
group_name() { [[ -z "$2" || "$2" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "$1: invalid group name '$2'"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output) need_value "$@"; OUTPUT="$2"; shift ;;
        --print) PRINT=1 ;;
        -f|--force) FORCE=1 ;;
        --rotate-secret) ROTATE=1 ;;
        -u|--update) UPDATE=1 ;;
        --no-pam) SET[HOMEACCES_PAM_ENABLED]=0 ;;
        --no-ssh) SET[HOMEACCES_SSH_ENABLED]=0 ;;
        --registration) SET[HOMEACCES_ALLOW_REGISTRATION]=1 ;;
        --no-registration) SET[HOMEACCES_ALLOW_REGISTRATION]=0 ;;
        --pam-group) need_value "$@"; group_name "$1" "$2"; [[ -n "$2" ]] || die "--pam-group cannot be empty"
                     SET[HOMEACCES_PAM_GROUP]="$2"; shift ;;
        --pam-admin-group) need_value "$@"; group_name "$1" "$2"; SET[HOMEACCES_PAM_ADMIN_GROUP]="$2"; shift ;;
        --access-ttl) need_value "$@"; positive_int "$1" "$2"; SET[HOMEACCES_ACCESS_TTL_MIN]="$2"; shift ;;
        --refresh-ttl) need_value "$@"; positive_int "$1" "$2"; SET[HOMEACCES_REFRESH_TTL_DAYS]="$2"; shift ;;
        --idle) need_value "$@"; positive_int "$1" "$2"; SET[HOMEACCES_TERMINAL_IDLE_MIN]="$2"; shift ;;
        -h|--help) usage ;;
        *) echo "gen-env: unknown option '$1'" >&2; usage 1 ;;
    esac
    shift
done

[[ -f "$TEMPLATE" ]] || die "template not found: $TEMPLATE"
(( UPDATE && (FORCE || ROTATE || PRINT || ${#SET[@]}) )) && die "--update cannot be combined with other options"

# 48 random bytes as URL-safe base64 (64 characters). Only needs coreutils.
new_secret() {
    head -c 48 /dev/urandom | base64 -w 0 | tr '+/' '-_' | tr -d '='
}

# Print the value of KEY in FILE (last occurrence), or nothing.
get_key() { sed -n "s/^$1=//p" "$2" | tail -n 1; }

# Replace KEY=... in the file, or append it. Values go through ENVIRON, so any character is safe.
set_key() {
    local key=$1 value=$2 file=$3
    KEY="$key" VALUE="$value" awk '
        BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"]; done = 0 }
        index($0, k "=") == 1 { print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$file" > "$file.tmp" && cat "$file.tmp" > "$file" && rm -f "$file.tmp"
}

# Move a finished temp file into place with the right owner and mode.
install_file() {
    local tmp=$1
    chmod 0640 "$tmp"
    if [[ $EUID -eq 0 ]]; then
        if getent group homeacces >/dev/null; then
            chown root:homeacces "$tmp"
        else
            warn "group 'homeacces' does not exist yet (install.sh creates it); file is root-only for now"
            chmod 0600 "$tmp"
        fi
    fi
    mv -f "$tmp" "$OUTPUT"
}

# ---------------------------------------------------------------- --update

if (( UPDATE )); then
    [[ -f "$OUTPUT" ]] || die "$OUTPUT does not exist (run without --update to create it)"
    tmp="$(mktemp "$OUTPUT.XXXXXX")"
    trap 'rm -f "$tmp" "$tmp.tmp"' EXIT
    cp "$OUTPUT" "$tmp"
    added=0
    while IFS= read -r line; do
        [[ "$line" =~ ^(HOMEACCES_[A-Z_]+)= ]] || continue
        key="${BASH_REMATCH[1]}"
        if ! grep -q "^$key=" "$tmp"; then
            echo "$line" >> "$tmp"
            echo "    added $key"
            added=1
        fi
    done < "$TEMPLATE"
    if (( added )); then
        install_file "$tmp"
        trap - EXIT
    else
        echo "    $OUTPUT is up to date"
    fi
    exit 0
fi

# ---------------------------------------------------------------- create / replace

if (( ! PRINT )) && [[ -e "$OUTPUT" ]] && (( ! FORCE )); then
    die "$OUTPUT already exists. Use --force to replace it, or --update to add missing settings."
fi

secret=""
if (( ! ROTATE )) && [[ -f "$OUTPUT" ]]; then
    secret="$(get_key HOMEACCES_SECRET_KEY "$OUTPUT")"
    [[ ${#secret} -ge 32 && "$secret" != "change-me" ]] || secret=""
fi
[[ -n "$secret" ]] || secret="$(new_secret)"
[[ ${#secret} -ge 32 ]] || die "could not generate a secret key"
SET[HOMEACCES_SECRET_KEY]="$secret"

if (( PRINT )); then
    tmp="$(mktemp)"
else
    install -d -m 0750 "$(dirname "$OUTPUT")"
    [[ $EUID -eq 0 ]] && getent group homeacces >/dev/null && chgrp homeacces "$(dirname "$OUTPUT")"
    tmp="$(mktemp "$OUTPUT.XXXXXX")"
fi
trap 'rm -f "$tmp" "$tmp.tmp"' EXIT

{
    echo "# Generated by deploy/gen-env.sh on $(date -u '+%Y-%m-%d %H:%M UTC'). Edit, then: sudo systemctl restart homeacces homeacces-privd"
    grep -v '^# /etc/homeacces/homeacces.env\|^# install.sh creates this file' "$TEMPLATE"
} > "$tmp"
for key in "${!SET[@]}"; do
    set_key "$key" "${SET[$key]}" "$tmp"
done

# Sanity checks on the result.
[[ "$(get_key HOMEACCES_COOKIE_SECURE "$tmp")" == 1 ]] || warn "HOMEACCES_COOKIE_SECURE is not 1: cookies will be sent over plain HTTP"
if [[ "$(get_key HOMEACCES_PAM_ENABLED "$tmp")" == 1 ]]; then
    for g in "$(get_key HOMEACCES_PAM_GROUP "$tmp")" "$(get_key HOMEACCES_PAM_ADMIN_GROUP "$tmp")"; do
        [[ -z "$g" ]] || getent group "$g" >/dev/null || warn "system group '$g' does not exist"
    done
fi

if (( PRINT )); then
    cat "$tmp"
    exit 0
fi

if [[ -e "$OUTPUT" ]]; then
    backup="$(mktemp "$OUTPUT.bak.$(date +%Y%m%d-%H%M%S).XXXX")"  # unique even within one second
    cp -p "$OUTPUT" "$backup"
    echo "    backup: $backup"
fi
install_file "$tmp"
trap - EXIT
echo "    wrote $OUTPUT$( (( ROTATE )) && echo ' (new secret key: all users are signed out)')"
