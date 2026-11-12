#!/usr/bin/env bash
# Build HomeAcces packages: a .deb (Debian 13 / Ubuntu) and an .rpm (Fedora).
#
# The packages install the same layout as deploy/install.sh (/opt/homeacces, /var/www/homeacces,
# systemd units, nginx site, PAM service, logrotate). On install they create the homeacces user,
# /etc/homeacces/homeacces.env (deploy/gen-env.sh), the database, a self-signed certificate, and
# start the services. Both packages are architecture independent (all / noarch).
#
# Usage: ./deploy/build-packages.sh [options]
#       --deb                   build only the .deb
#       --rpm                   build only the .rpm
#   -o, --output DIR            where to write the packages (default: ./dist)
#       --version VER           package version (default: "version" in frontend/package.json)
#       --release N             package release (default: 1)
#       --skip-frontend-build   use the existing frontend/dist instead of running npm ci + build
#       --venv                  bundle no Python libraries: the package creates /opt/homeacces/venv
#                               on install and pip-installs backend/requirements.txt (needs Internet
#                               on the target). Default: depend on the distribution's python3-flask,
#                               python3-jwt/pyjwt, python3-gunicorn and python3-websockets packages.
#   -h, --help
#
# Build requirements: dpkg-deb (.deb), rpmbuild (.rpm: "sudo apt install rpm" on Debian,
# "sudo dnf install rpm-build" on Fedora), and node + npm unless --skip-frontend-build.
# Root is not needed.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
NAME=homeacces
OUTPUT="$SRC/dist"
VERSION="" RELEASE=1
BUILD_DEB=1 BUILD_RPM=1 SKIP_FRONTEND=0 PY_MODE=system
SUMMARY="Lightweight web app: React UI, Flask API, PAM login, web and SSH terminals"

die() { echo "build-packages: $*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit "${1:-0}"; }

only=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --deb|--rpm) [[ -z "$only" || "$only" == "$1" ]] || die "--deb and --rpm together: omit both to build both"
                     only="$1" ;;
        -o|--output) [[ $# -ge 2 ]] || die "$1 needs a value"; OUTPUT="$2"; shift ;;
        --version) [[ $# -ge 2 ]] || die "$1 needs a value"; VERSION="$2"; shift ;;
        --release) [[ $# -ge 2 ]] || die "$1 needs a value"; RELEASE="$2"; shift ;;
        --skip-frontend-build) SKIP_FRONTEND=1 ;;
        --venv) PY_MODE=venv ;;
        -h|--help) usage ;;
        *) echo "build-packages: unknown option '$1'" >&2; usage 1 ;;
    esac
    shift
done
case "$only" in
    --deb) BUILD_RPM=0 ;;
    --rpm) BUILD_DEB=0 ;;
esac

if [[ -z "$VERSION" ]]; then
    VERSION="$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$SRC/frontend/package.json" | head -n 1)"
fi
MAINTAINER="$(sed -n 's/^ *"author": *"\([^"]*\)".*/\1/p' "$SRC/frontend/package.json" | head -n 1)"
[[ "$VERSION" =~ ^[0-9][A-Za-z0-9.+~]*$ ]] || die "invalid version '$VERSION'"
[[ "$RELEASE" =~ ^[0-9][A-Za-z0-9.]*$ ]] || die "invalid release '$RELEASE'"
[[ -n "$MAINTAINER" ]] || MAINTAINER="HomeAcces maintainers <root@localhost>"

(( BUILD_DEB )) && { command -v dpkg-deb >/dev/null || die "dpkg-deb not found (Debian: it is in the dpkg package)"; }
(( BUILD_RPM )) && { command -v rpmbuild >/dev/null || die "rpmbuild not found (Debian: sudo apt install rpm; Fedora: sudo dnf install rpm-build)"; }

umask 022
WORK="$(mktemp -d "${TMPDIR:-/tmp}/homeacces-pkg.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"

# ------------------------------------------------------------------ frontend

if (( ! SKIP_FRONTEND )); then
    command -v npm >/dev/null || die "npm not found: install nodejs + npm, or use --skip-frontend-build"
    echo "==> Building frontend"
    (cd "$SRC/frontend" && npm ci --no-audit --no-fund && npm run build)
fi
[[ -f "$SRC/frontend/dist/index.html" ]] || die "frontend/dist/index.html is missing (build the frontend first)"

# ------------------------------------------------------------------ staging

# Copy the files git tracks under a source directory (so local venvs, caches, databases and
# test output never end up in a package); without git, copy everything minus the usual junk.
copy_tracked() {
    local from=$1 to=$2
    mkdir -p "$to"
    if git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        (cd "$SRC" && git ls-files -z -- "$from" | tar --null --ignore-failed-read -T - -cf -) \
            | tar -C "$to" --strip-components="$(tr -cd / <<< "$from/" | wc -c)" -xf -
    else
        tar -C "$SRC/$from" --exclude=__pycache__ --exclude='*.pyc' --exclude=.venv --exclude=venv \
            --exclude=instance --exclude=.pytest_cache -cf - . | tar -C "$to" -xf -
    fi
}

# Rewrite the venv paths used by the units and scripts to the system interpreter.
use_system_python() {
    sed -i -e 's#/opt/homeacces/venv/bin/gunicorn#/usr/bin/python3 -m gunicorn#g' \
           -e 's#/opt/homeacces/venv/bin/flask#/usr/bin/python3 -m flask#g' \
           -e 's#/opt/homeacces/venv/bin/python#/usr/bin/python3#g' "$@"
    if grep -l '/opt/homeacces/venv' "$@"; then
        die "venv path left in the files above: update use_system_python()"
    fi
}

# stage_common ROOT FLAVOR: the files shared by both packages.
stage_common() {
    local root=$1 flavor=$2 app="$1/opt/homeacces"

    copy_tracked backend "$app/backend"
    rm -rf "$app/backend/tests"
    find "$app/backend" -type d -exec chmod 0755 {} + -o -type f -exec chmod 0644 {} +

    install -d "$root/var/www/homeacces"
    cp -r "$SRC/frontend/dist/." "$root/var/www/homeacces/"
    find "$root/var/www/homeacces" -type d -exec chmod 0755 {} + -o -type f -exec chmod 0644 {} +

    install -D -m 0755 "$SRC/deploy/gen-env.sh" "$app/deploy/gen-env.sh"
    install -D -m 0755 "$SRC/deploy/create-admin.sh" "$app/deploy/create-admin.sh"
    install -D -m 0644 "$SRC/deploy/homeacces.env.example" "$app/deploy/homeacces.env.example"
    write_setup_script "$flavor" > "$app/deploy/pkg-setup.sh"
    chmod 0755 "$app/deploy/pkg-setup.sh"

    install -d "$root/usr/sbin"
    for cmd in gen-env create-admin; do
        printf '#!/bin/sh\nexec /opt/homeacces/deploy/%s.sh "$@"\n' "$cmd" > "$root/usr/sbin/homeacces-$cmd"
        chmod 0755 "$root/usr/sbin/homeacces-$cmd"
    done

    install -d "$root/usr/lib/systemd/system"
    for unit in homeacces.socket homeacces.service homeacces-privd.service homeacces-ssh.service; do
        install -m 0644 "$SRC/deploy/systemd/$unit" "$root/usr/lib/systemd/system/$unit"
    done

    install -D -m 0644 "$SRC/deploy/nginx/homeacces-proxy.conf" "$root/etc/nginx/snippets/homeacces-proxy.conf"
    install -D -m 0644 "$SRC/deploy/nginx/homeacces-headers.conf" "$root/etc/nginx/snippets/homeacces-headers.conf"
    install -D -m 0644 "$SRC/deploy/logrotate/homeacces" "$root/etc/logrotate.d/homeacces"

    install -D -m 0644 "$SRC/README.md" "$root/usr/share/doc/homeacces/README.md"
    if (( PY_MODE_SYSTEM )); then
        use_system_python "$root"/usr/lib/systemd/system/*.service "$app/deploy/create-admin.sh"
    fi
}

PY_MODE_SYSTEM=0
[[ "$PY_MODE" == system ]] && PY_MODE_SYSTEM=1

# ------------------------------------------------------------------ install-time setup script

# The maintainer scripts of both packages call /opt/homeacces/deploy/pkg-setup.sh. It does what
# deploy/install.sh does after copying files, with the distribution's differences filled in.
write_setup_script() {
    local flavor=$1 nginx_user pam_admin_group
    case "$flavor" in
        debian) nginx_user=www-data; pam_admin_group=sudo ;;
        fedora) nginx_user=nginx; pam_admin_group=wheel ;;
    esac
    cat <<EOF
#!/bin/bash
# Generated by deploy/build-packages.sh. Called by the package's maintainer scripts:
#   pkg-setup.sh configure [--first]   after install or upgrade (--first: new install)
#   pkg-setup.sh remove                before the package is removed (not on upgrade)
set -euo pipefail

FLAVOR=$flavor
PY_MODE=$PY_MODE
NGINX_USER=$nginx_user
PAM_ADMIN_GROUP=$pam_admin_group
EOF
    cat <<'EOF'
APP_DIR=/opt/homeacces
ENV_FILE=/etc/homeacces/homeacces.env
SSL_DIR=/etc/ssl/homeacces
UNITS=(homeacces.socket homeacces-privd homeacces-ssh homeacces)

warn() { echo "homeacces: warning: $*" >&2; }
have_systemd() { [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null; }
if [[ "$PY_MODE" == venv ]]; then PY="$APP_DIR/venv/bin/python"; else PY=/usr/bin/python3; fi

reload_nginx() {
    have_systemd || return 0
    if nginx -t 2>/dev/null; then
        systemctl reload-or-restart nginx || warn "could not reload nginx"
    else
        warn "nginx configuration test failed (run: nginx -t); nginx was not reloaded"
    fi
}

configure() {
    local first=0
    [[ "${1:-}" == --first ]] && first=1

    getent group homeacces >/dev/null || groupadd --system homeacces
    getent passwd homeacces >/dev/null || \
        useradd --system --gid homeacces --home-dir "$APP_DIR" --no-create-home \
            --shell /usr/sbin/nologin homeacces
    # Members of this group may sign in with their system account (PAM) and use the web terminal.
    getent group homeacces-pam >/dev/null || groupadd --system homeacces-pam
    install -d -o homeacces -g homeacces -m 0750 /var/lib/homeacces /var/log/homeacces

    if [[ "$PY_MODE" == venv ]]; then
        # Recreate the venv when the system Python changed under it (distribution upgrade).
        if [[ -d "$APP_DIR/venv" ]] && ! "$APP_DIR/venv/bin/python" -c '' 2>/dev/null; then
            rm -rf "$APP_DIR/venv"
        fi
        [[ -d "$APP_DIR/venv" ]] || python3 -m venv "$APP_DIR/venv"
        "$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check \
            -r "$APP_DIR/backend/requirements.txt"
    fi

    if [[ -f "$ENV_FILE" ]]; then
        "$APP_DIR/deploy/gen-env.sh" --update --output "$ENV_FILE"
    else
        "$APP_DIR/deploy/gen-env.sh" --output "$ENV_FILE" --pam-admin-group "$PAM_ADMIN_GROUP"
    fi

    # Sourced by the homeacces user, not passed as arguments: those are visible to all users in `ps`.
    (cd / && runuser -u homeacces -- bash -c 'set -a; . "$1"; set +a; shift; exec "$@"' _ "$ENV_FILE" \
        "$PY" -m flask --app "$APP_DIR/backend/wsgi.py" init-db)

    if [[ ! -f "$SSL_DIR/homeacces.crt" ]]; then
        local host
        host="$(hostname 2>/dev/null || cat /etc/hostname 2>/dev/null || echo localhost)"
        install -d -m 0755 "$SSL_DIR"
        (umask 077 && openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
            -keyout "$SSL_DIR/homeacces.key" -out "$SSL_DIR/homeacces.crt" \
            -subj "/CN=$host" -addext "subjectAltName=DNS:$host,DNS:$host.local" 2>/dev/null)
        chmod 0644 "$SSL_DIR/homeacces.crt"
        echo "homeacces: generated a self-signed certificate in $SSL_DIR"
    fi

    if [[ "$FLAVOR" == debian ]] && (( first )); then
        ln -sf /etc/nginx/sites-available/homeacces /etc/nginx/sites-enabled/homeacces
        # Both servers are default_server on port 80: the stock site has to go.
        if [[ -L /etc/nginx/sites-enabled/default ]]; then
            rm -f /etc/nginx/sites-enabled/default
            echo "homeacces: disabled the default nginx site (/etc/nginx/sites-enabled/default)"
        fi
    fi

    if [[ "$FLAVOR" == fedora ]] && command -v selinuxenabled >/dev/null && selinuxenabled; then
        # nginx proxies the terminal WebSockets to 127.0.0.1:8001 and :8002.
        setsebool -P httpd_can_network_connect 1 || warn "could not set SELinux boolean httpd_can_network_connect"
    fi

    if ! have_systemd; then
        warn "systemd is not running: start nginx and ${UNITS[*]} yourself"
        return 0
    fi
    systemctl daemon-reload
    systemctl enable "${UNITS[@]}" 2>/dev/null || warn "could not enable ${UNITS[*]}"
    # The API gets its socket from homeacces.socket: stop it first so the socket can be (re)created.
    systemctl stop homeacces || true
    systemctl restart homeacces.socket homeacces-privd homeacces-ssh || warn "a HomeAcces service failed to start"
    systemctl start homeacces || warn "homeacces.service failed to start (journalctl -u homeacces)"
    if (( first )); then
        systemctl enable nginx 2>/dev/null || true
    fi
    reload_nginx

    if (( first )); then
        cat <<MSG

HomeAcces is installed: https://$(hostname 2>/dev/null || echo '<host>').local/
  Create the first admin:            sudo homeacces-create-admin <username>
  Allow a system user to use PAM:    sudo usermod -aG homeacces-pam <system-user>
  Settings:                          $ENV_FILE (or sudo homeacces-gen-env --help)
MSG
        if [[ "$FLAVOR" == fedora ]]; then
            cat <<'MSG'
  Firewall (if firewalld is active): sudo firewall-cmd --permanent --add-service=http --add-service=https
                                     sudo firewall-cmd --reload
  SELinux: if the site answers 502, allow nginx to reach the API socket:
           sudo ausearch -m avc -c nginx --raw | audit2allow -M homeacces-nginx && sudo semodule -i homeacces-nginx.pp
MSG
        fi
    fi
}

remove() {
    if have_systemd; then
        systemctl disable --now homeacces homeacces.socket homeacces-privd homeacces-ssh 2>/dev/null || true
    fi
    # Generated at run time, so the package manager does not know about them.
    find "$APP_DIR/backend" -depth -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
    rm -rf "$APP_DIR/venv"
}

case "${1:-}" in
    configure) shift; configure "$@" ;;
    remove) remove ;;
    *) echo "usage: $0 configure [--first] | remove" >&2; exit 1 ;;
esac
EOF
}

# ------------------------------------------------------------------ .deb

build_deb() {
    local root="$WORK/deb" deb_version="$VERSION-$RELEASE"
    echo "==> Staging .deb"
    stage_common "$root" debian
    install -D -m 0644 "$SRC/deploy/nginx/homeacces.conf" "$root/etc/nginx/sites-available/homeacces"
    install -D -m 0644 "$SRC/deploy/pam.d/homeacces" "$root/etc/pam.d/homeacces"
    install -m 0644 "$SRC/LICENSE" "$root/usr/share/doc/homeacces/copyright"

    local depends="nginx, python3, openssl, openssh-client, libpam0g, passwd, util-linux"
    if (( PY_MODE_SYSTEM )); then
        depends+=", python3-flask, python3-jwt, python3-gunicorn, python3-websockets"
    else
        depends+=", python3-venv, python3-pip"
    fi

    install -d -m 0755 "$root/DEBIAN"
    cat > "$root/DEBIAN/control" <<EOF
Package: $NAME
Version: $deb_version
Architecture: all
Maintainer: $MAINTAINER
Installed-Size: $(du -sk --exclude=DEBIAN "$root" | cut -f1)
Depends: $depends
Recommends: logrotate, sqlite3
Section: web
Priority: optional
Description: $SUMMARY
 nginx serves a React UI and proxies /api to a Flask API backed by SQLite.
 Authentication uses JWT in HttpOnly cookies. Users can sign in with a
 HomeAcces account or their Linux account (PAM), with a web terminal and
 saved SSH connections opened in the browser.
EOF
    (cd "$root" && find etc -type f | sed 's#^#/#' | sort) > "$root/DEBIAN/conffiles"

    cat > "$root/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
case "$1" in
    configure)
        if [ -z "$2" ]; then
            /opt/homeacces/deploy/pkg-setup.sh configure --first
        else
            /opt/homeacces/deploy/pkg-setup.sh configure
        fi ;;
esac
exit 0
EOF
    cat > "$root/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
case "$1" in
    remove|deconfigure) /opt/homeacces/deploy/pkg-setup.sh remove ;;
esac
exit 0
EOF
    cat > "$root/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
case "$1" in
    remove|purge)
        rm -f /etc/nginx/sites-enabled/homeacces
        if [ -d /run/systemd/system ]; then
            systemctl daemon-reload || true
            if nginx -t 2>/dev/null; then systemctl reload nginx 2>/dev/null || true; fi
        fi ;;
esac
if [ "$1" = purge ]; then
    rm -rf /etc/homeacces /etc/ssl/homeacces /var/log/homeacces
    echo "homeacces: the database in /var/lib/homeacces and the homeacces user were kept"
fi
exit 0
EOF
    chmod 0755 "$root/DEBIAN/postinst" "$root/DEBIAN/prerm" "$root/DEBIAN/postrm"
    (cd "$root" && find . -path ./DEBIAN -prune -o -type f -printf '%P\0' | sort -z | xargs -0 md5sum) \
        > "$root/DEBIAN/md5sums"

    local out="$OUTPUT/${NAME}_${deb_version}_all.deb"
    echo "==> Building $out"
    dpkg-deb --root-owner-group -Zxz --build "$root" "$out" >/dev/null
}

# ------------------------------------------------------------------ .rpm

build_rpm() {
    local root="$WORK/rpm-root" top="$WORK/rpmbuild"
    echo "==> Staging .rpm"
    stage_common "$root" fedora
    # Fedora's nginx has no sites-available / sites-enabled: conf.d is included from nginx.conf.
    install -D -m 0644 "$SRC/deploy/nginx/homeacces.conf" "$root/etc/nginx/conf.d/homeacces.conf"
    sed -i 's#^\# /etc/nginx/sites-available/homeacces#\# /etc/nginx/conf.d/homeacces.conf#' \
        "$root/etc/nginx/conf.d/homeacces.conf"
    # nginx runs as "nginx", not "www-data", and only it may connect to the API socket.
    sed -i 's/^SocketUser=www-data/SocketUser=nginx/; s/^SocketGroup=www-data/SocketGroup=nginx/' \
        "$root/usr/lib/systemd/system/homeacces.socket"
    grep -q '^SocketUser=nginx' "$root/usr/lib/systemd/system/homeacces.socket" \
        || die "homeacces.socket: could not switch the socket owner to nginx"
    # Fedora's PAM stack has no common-auth / common-account.
    install -d "$root/etc/pam.d"
    cat > "$root/etc/pam.d/homeacces" <<'EOF'
# /etc/pam.d/homeacces — used by homeacces-privd to check system account passwords.
# Same rules as other Fedora logins (pam_unix, faillock, account expiry).
auth       include      password-auth
account    include      password-auth
EOF
    install -d "$root/usr/share/licenses/homeacces"
    install -m 0644 "$SRC/LICENSE" "$root/usr/share/licenses/homeacces/LICENSE"

    local requires="nginx python3 openssl openssh-clients pam shadow-utils util-linux logrotate"
    if (( PY_MODE_SYSTEM )); then
        requires+=" python3dist(flask) python3dist(pyjwt) python3dist(gunicorn) python3dist(websockets)"
    else
        requires+=" python3-pip"
    fi

    mkdir -p "$top"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
    local spec="$top/SPECS/$NAME.spec"
    cat > "$spec" <<EOF
# Files come pre-staged from deploy/build-packages.sh: no build step, no brp post-processing
# (byte-compilation, shebang mangling, stripping) and no automatic dependencies.
%global debug_package %{nil}
%global __os_install_post %{nil}
%define _build_id_links none

Name:           $NAME
Version:        ${VERSION//-/_}
Release:        $RELEASE%{?dist}
Summary:        $SUMMARY
License:        MIT
BuildArch:      noarch
AutoReqProv:    no
Requires:       ${requires// /, }

%description
nginx serves a React UI and proxies /api to a Flask API backed by SQLite.
Authentication uses JWT in HttpOnly cookies. Users can sign in with a
HomeAcces account or their Linux account (PAM), with a web terminal and
saved SSH connections opened in the browser.

%install
cp -a "$root/." %{buildroot}/

%post
if [ \$1 -eq 1 ]; then
    /opt/homeacces/deploy/pkg-setup.sh configure --first || :
else
    /opt/homeacces/deploy/pkg-setup.sh configure || :
fi

%preun
if [ \$1 -eq 0 ]; then
    /opt/homeacces/deploy/pkg-setup.sh remove || :
fi

%postun
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || :
    if [ \$1 -eq 0 ] && nginx -t 2>/dev/null; then systemctl reload nginx 2>/dev/null || :; fi
fi

%files
%defattr(-,root,root,-)
/opt/homeacces
/var/www/homeacces
/usr/sbin/homeacces-create-admin
/usr/sbin/homeacces-gen-env
/usr/lib/systemd/system/homeacces.socket
/usr/lib/systemd/system/homeacces.service
/usr/lib/systemd/system/homeacces-privd.service
/usr/lib/systemd/system/homeacces-ssh.service
%dir /etc/nginx/snippets
%config(noreplace) /etc/nginx/conf.d/homeacces.conf
%config(noreplace) /etc/nginx/snippets/homeacces-proxy.conf
%config(noreplace) /etc/nginx/snippets/homeacces-headers.conf
%config(noreplace) /etc/pam.d/homeacces
%config(noreplace) /etc/logrotate.d/homeacces
%doc /usr/share/doc/homeacces
%license /usr/share/licenses/homeacces/LICENSE

%changelog
* $(LC_ALL=C date -u '+%a %b %d %Y') $MAINTAINER - ${VERSION//-/_}-$RELEASE
- Package built by deploy/build-packages.sh
EOF

    echo "==> Building .rpm"
    rpmbuild --quiet -bb --nodeps --define "_topdir $top" "$spec"
    local rpm
    for rpm in "$top"/RPMS/noarch/*.rpm; do
        cp "$rpm" "$OUTPUT/"
        echo "==> Built $OUTPUT/$(basename "$rpm")"
    done
}

(( BUILD_DEB )) && build_deb
(( BUILD_RPM )) && build_rpm
echo "Done. Packages in $OUTPUT"
