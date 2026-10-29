# Development container for HomeAcces: Debian 13 (trixie), same packages as the Raspberry Pi target.
FROM debian:13

ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        nodejs npm \
        nginx sqlite3 openssl rsync \
        sudo git curl ca-certificates procps less nano bash-completion \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/nginx/sites-enabled/default

# Development user with passwordless sudo (homeacces-privd and nginx are started as root).
RUN groupadd --gid "$USER_GID" "$USERNAME" \
 && useradd --uid "$USER_UID" --gid "$USER_GID" --create-home --shell /bin/bash "$USERNAME" \
 && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > "/etc/sudoers.d/$USERNAME" \
 && chmod 0440 "/etc/sudoers.d/$USERNAME"

# PAM login + web terminal, as on the Pi. "pi" / "raspberry" is a throwaway test account for
# this container only: it can sign in with "System account" and is a web admin (group sudo).
COPY deploy/pam.d/homeacces /etc/pam.d/homeacces
RUN groupadd --system homeacces-pam \
 && useradd --create-home --shell /bin/bash --groups homeacces-pam,sudo pi \
 && echo 'pi:raspberry' | chpasswd

USER $USERNAME
