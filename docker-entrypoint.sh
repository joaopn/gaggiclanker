#!/bin/sh
# Make DATA_DIR usable by the app user, then drop to it and exec the app.
#
# Why this exists: when the bind-mount source in compose.yml does not exist yet,
# Docker creates it as root:root 0755. The container runs as a non-root user, so
# the very first `docker compose up -d --build` on a fresh checkout used to
# restart-loop with SQLite's "unable to open database file" — a message that
# says nothing about ownership and sends people hunting for a corrupt database.
#
# So the container starts as root, fixes the one directory it owns, and drops
# privileges before running a single line of application code. Nothing in the
# app ever runs as root.

set -eu

APP_USER="${APP_USER:-app}"
APP_GROUP="${APP_GROUP:-app}"
DATA_DIR="${DATA_DIR:-/app/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "${DATA_DIR}"

    # Only chown when it is actually wrong. On a large existing archive a
    # recursive chown is slow, and on some filesystems it is not permitted;
    # skipping the no-op case keeps restarts fast and quiet.
    if [ "$(stat -c '%u' "${DATA_DIR}")" != "$(id -u "${APP_USER}")" ]; then
        echo "{\"event\":\"entrypoint_chown\",\"path\":\"${DATA_DIR}\",\"owner\":\"${APP_USER}\"}"
        chown -R "${APP_USER}:${APP_GROUP}" "${DATA_DIR}" || \
            echo "{\"event\":\"entrypoint_chown_failed\",\"path\":\"${DATA_DIR}\"}" >&2
    fi

    # setpriv over su/sudo: no PAM, no extra process hanging around to reap
    # signals, and the app stays PID 1 so `docker stop` reaches it directly.
    #
    # --bounding-set=-all empties the capability bounding set, so the app
    # cannot regain any of the capabilities this entrypoint needed, by any
    # route, for the life of the process. Dropping the bounding set itself
    # needs CAP_SETPCAP, which is why compose adds it.
    exec setpriv \
        --reuid "${APP_USER}" \
        --regid "${APP_GROUP}" \
        --init-groups \
        --bounding-set=-all \
        "$@"
fi

# Already non-root: the operator set `user:` in compose, so respect it and let
# the app's own DATA_DIR check produce the error if the directory is unusable.
exec "$@"
