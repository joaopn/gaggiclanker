#!/usr/bin/env bash
#
# Build and run the GaggiMate firmware simulator, then point the opt-in
# simulator tests at it:
#
#   scripts/sim.sh build          # prepare the work tree and build
#   scripts/sim.sh run            # build and run in the foreground (SDL window)
#   scripts/sim.sh serve          # build and run headless in the background
#   scripts/sim.sh test           # serve, then `uv run pytest -m simulator`
#   scripts/sim.sh stop           # stop a backgrounded simulator
#   scripts/sim.sh clean          # throw the work tree away
#
# The simulator is the real display firmware (`src/display/`) compiled natively
# with the BLE link to the controller mocked, an SDL window as the panel, and
# the embedded web UI served on **localhost:8080** (port 80 remapped so it needs
# no root). It is the only way to exercise this client against the actual
# firmware without the machine on the bench, and it earns its keep: it is what
# caught `OtaSettings` rejecting every real identity frame, because the device
# sends a heap/filesystem diagnostics block our fake did not.
#
# ─────────────────────────────────────────────────────────────────────────────
# It never builds inside `external/gaggimate`.
#
# That checkout is a read-only reference clone of somebody else's project. This
# script clones it to a scratch tree (`$SIM_WORKDIR`, under /workspace so it
# survives a container recreate), applies the patches in `scripts/sim-patches/`
# there, and builds that. `scripts/sim.sh clean` removes it; nothing in the
# reference clone is ever touched.
#
# One patch is needed, and it is not a local quirk:
#
#   `WebUIPlugin.cpp:190` filters `/api/history/*` to a 503 during an OTA using
#   `AsyncURIMatcher::prefix()`, which comes from ESPAsyncWebServer 3.12.0 — a
#   library the simulator does not link. It substitutes its own 203-line
#   stand-in at `sim/web/ESPAsyncWebServer.h`, and that file was never given
#   the matcher, so `pio run -e display-sim` fails with
#   "'AsyncURIMatcher' has not been declared". `sim-patches/0001` adds a
#   prefix-only matcher and the `setFilter` hook it needs. It belongs upstream;
#   until it lands there it lives here.
#
# Prerequisites, none of which are in the dev container image:
#
#   * `apt-get install build-essential pkg-config libsdl2-dev`
#   * PlatformIO in a venv under /workspace:
#       uv venv /workspace/.tools/pio
#       uv pip install --python /workspace/.tools/pio/bin/python platformio
#   * Node 22 at /workspace/.tools/node — the sim `.incbin`s
#     `src/display/webassets/web_ui.bin` and will not assemble without it.
#
# Two more things the script generates because only the ESP32 environments get
# them: `src/version.h` (their `auto_firmware_version.py` pre-script) and
# `scripts/sim-gcc-compat.h`, force-included — the firmware is developed against
# Apple clang, whose libc++ headers include more than libstdc++'s, so on GCC it
# fails over <memory>, <stdexcept> and <cstdarg> the source never asks for.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The reference checkout, read only. Override to seed from your own clone.
FIRMWARE_SRC="${GAGGIMATE_FIRMWARE_DIR:-/workspace/gaggiclanker/external/gaggimate}"
# Where the build actually happens.
SIM_WORKDIR="${GAGGIMATE_SIM_WORKDIR:-/workspace/.tools/gaggimate-sim}"

PIO_BIN="${PIO_BIN:-/workspace/.tools/pio/bin/pio}"
NODE_BIN_DIR="${NODE_BIN_DIR:-/workspace/.tools/node/bin}"

SIM_PORT="${GAGGIMATE_SIM_PORT:-8080}"
SIM_LOG="${GAGGIMATE_SIM_LOG:-/tmp/gaggimate-sim.log}"
SIM_PID="${GAGGIMATE_SIM_PID:-/tmp/gaggimate-sim.pid}"
# Where the simulator's own exit status is filed when it ends by itself, so a
# run that found it gone can say how it died rather than only that it is.
SIM_STATUS="${GAGGIMATE_SIM_STATUS:-/tmp/gaggimate-sim.status}"
PROGRAM="$SIM_WORKDIR/.pio/build/display-sim/program"

die() { echo "sim.sh: $*" >&2; exit 1; }

ensure_workdir() {
    [[ -x "$PIO_BIN" ]] || die "no PlatformIO at $PIO_BIN — see the header of this script"
    if [[ ! -d "$SIM_WORKDIR/.git" ]]; then
        [[ -d "$FIRMWARE_SRC" ]] ||
            die "no firmware checkout at $FIRMWARE_SRC (set GAGGIMATE_FIRMWARE_DIR)"
        echo "sim.sh: cloning $FIRMWARE_SRC -> $SIM_WORKDIR"
        rm -rf "$SIM_WORKDIR"
        # A local clone: committed content only, so nothing the reference tree
        # has built leaks in and the patches apply to a known state.
        git clone --quiet "$FIRMWARE_SRC" "$SIM_WORKDIR"
    fi
}

apply_patches() {
    shopt -s nullglob
    for patch in "$REPO_ROOT"/scripts/sim-patches/*.patch; do
        if git -C "$SIM_WORKDIR" apply --reverse --check "$patch" >/dev/null 2>&1; then
            continue  # already applied
        fi
        echo "sim.sh: applying $(basename "$patch")"
        git -C "$SIM_WORKDIR" apply "$patch" ||
            die "$(basename "$patch") no longer applies — upstream moved; re-cut it against $SIM_WORKDIR"
    done
    shopt -u nullglob
}

ensure_webui() {
    [[ -f "$SIM_WORKDIR/src/display/webassets/web_ui.bin" ]] && return 0
    echo "sim.sh: building the firmware's embedded web UI (needs Node 22)"
    [[ -d "$NODE_BIN_DIR" ]] && export PATH="$NODE_BIN_DIR:$PATH"
    command -v node >/dev/null || die "node not found; set NODE_BIN_DIR"
    (cd "$SIM_WORKDIR" && bash scripts/build_webui.sh)
}

ensure_version_header() {
    local header="$SIM_WORKDIR/src/version.h"
    [[ -f "$header" ]] && return 0
    local described
    described="$(git -C "$SIM_WORKDIR" describe --tags --always --dirty 2>/dev/null || echo sim)"
    cat >"$header" <<EOF
#pragma once
#ifndef GIT_VERSION_H
#define GIT_VERSION_H
#define BUILD_GIT_VERSION "$described"
#define BUILD_TIMESTAMP "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
#endif
EOF
}

build() {
    ensure_workdir
    apply_patches
    ensure_webui
    ensure_version_header
    echo "sim.sh: building display-sim in $SIM_WORKDIR"
    (
        cd "$SIM_WORKDIR"
        PLATFORMIO_BUILD_FLAGS="-include $REPO_ROOT/scripts/sim-gcc-compat.h" \
            "$PIO_BIN" run -e display-sim
    )
}

serve() {
    build
    [[ -x "$PROGRAM" ]] || die "the build produced no $PROGRAM"
    echo "sim.sh: starting the simulator, logging to $SIM_LOG"
    rm -f "$SIM_STATUS"
    # SDL_VIDEODRIVER=dummy so it runs without an X server; the web UI and the
    # WebSocket are what we are after, not the panel.
    #
    # `setsid` so the simulator leads its own process group: `stop` kills the
    # group rather than one pid, because the SDL program spawns helpers and
    # killing only the leader left them holding :8080 — which made the *next*
    # run fail to bind and read as a broken build rather than as a leftover.
    #
    # `exec` keeps that leader's pid equal to `$!` (the subshell is replaced
    # rather than waited on), and `disown` stops this script waiting for it, so
    # `serve` returns and the simulator stays.
    #
    # `trap '' PIPE` because SIG_IGN survives both the fork and the exec, and
    # the simulator needs it to survive a client that hangs up: its web shim
    # writes every response with a plain `send()` — no MSG_NOSIGNAL — so one
    # write to a socket whose peer had already closed killed the whole process
    # with SIGPIPE, mid-suite, right after it had answered a request. What that
    # looked like from outside was a test timing out on a shot that never
    # arrived and every test after it reporting "no simulator".
    #
    # The `bash -c` wrapper is there to file the program's exit status: the
    # simulator is disowned, so nothing waits for it, and without the file a
    # run that finds it gone cannot say whether it was killed, crashed, or
    # exited. It stays the session and group leader, so `stop` still reaches
    # the whole group; being killed by `stop` files nothing, which is the
    # difference we want.
    (
        cd "$SIM_WORKDIR" || exit 1
        trap '' PIPE
        exec setsid bash -c \
            'SDL_VIDEODRIVER=dummy "$1"; status=$?; printf %s "$status" >"$2"; exit "$status"' \
            sim "$PROGRAM" "$SIM_STATUS"
    ) >"$SIM_LOG" 2>&1 </dev/null &
    local pid=$!
    disown "%%" 2>/dev/null || true
    echo "$pid" >"$SIM_PID"
    for _ in $(seq 1 60); do
        if curl -fsS --max-time 1 "http://127.0.0.1:$SIM_PORT/api/status" >/dev/null 2>&1; then
            echo "sim.sh: up on http://127.0.0.1:$SIM_PORT"
            return 0
        fi
        sleep 1
    done
    die "the simulator did not answer /api/status within 60s; see $SIM_LOG"
}

# Is anything still listening on the simulator's port? The only question that
# actually matters: a leftover holding :8080 makes the *next* run fail to bind,
# which reads as a broken build rather than as a leftover.
port_is_free() {
    ! curl -fsS --max-time 1 "http://127.0.0.1:$SIM_PORT/api/status" >/dev/null 2>&1
}

stop() {
    if [[ -f "$SIM_PID" ]]; then
        local pid
        pid="$(cat "$SIM_PID")"
        # Never kill our own group. A stale or mis-written pid file that names
        # this script (or its caller) would otherwise turn `sim.sh stop` into a
        # kill of the terminal that ran it — a spectacular way to lose a test
        # run. The port sweep below handles that case instead.
        if [[ "$pid" == "$$" || "$pid" == "$(ps -o pgid= -p $$ | tr -d ' ')" ]]; then
            echo "sim.sh: $SIM_PID names this process group; ignoring it" >&2
            pid=""
            rm -f "$SIM_PID"
        fi
    fi
    if [[ -n "${pid:-}" ]]; then
        # The process *group*, not the pid. `serve` starts it under setsid, so
        # the pid is the group leader and `kill -- -$pid` reaches the SDL
        # helpers too; killing the single pid left them alive holding the port.
        kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
        # The simulator is an SDL program and does not always take SIGTERM
        # while it is inside a frame. Give it a second, then insist.
        for _ in $(seq 1 20); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "sim.sh: $pid ignored SIGTERM, sending SIGKILL to the group"
            kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$SIM_PID"
    fi

    # Whatever the pid file said, and whatever was started by hand: the port is
    # the contract. Wait for it, then take anything still holding it.
    for _ in $(seq 1 20); do
        port_is_free && { echo "sim.sh: stopped"; return 0; }
        sleep 0.1
    done
    echo "sim.sh: something is still on :$SIM_PORT, killing it by name"
    pkill -9 -f "$PROGRAM" 2>/dev/null || true
    for _ in $(seq 1 20); do
        port_is_free && { echo "sim.sh: stopped"; return 0; }
        sleep 0.1
    done
    die "port $SIM_PORT is still in use after stop; find it with \`ss -lptn 'sport = :$SIM_PORT'\`"
}

case "${1:-test}" in
    build) build ;;
    run)
        build
        [[ -x "$PROGRAM" ]] || die "the build produced no $PROGRAM"
        # Ignored the same way `serve` ignores it, and for the same reason: a
        # browser tab closed mid-response must not take the simulator with it.
        trap '' PIPE
        cd "$SIM_WORKDIR" && exec "$PROGRAM"
        ;;
    serve) serve ;;
    stop) stop ;;
    clean)
        stop
        rm -rf "$SIM_WORKDIR"
        echo "sim.sh: removed $SIM_WORKDIR"
        # Only the scratch tree. `external/gaggimate` is a read-only reference
        # clone and may carry git-ignored artefacts (.pio/, sim/build/) from an
        # early attempt to build in place; they are harmless, they are not ours
        # to delete, and `git status` never shows them. Remove them by hand if
        # you want the space back.
        echo "sim.sh: external/gaggimate is untouched (it may hold ignored build artefacts)"
        ;;
    test)
        serve
        trap stop EXIT
        # `-n 0`: the suite runs in parallel by default, but there is one
        # simulator, it allows three WebSocket clients and it brews one shot at
        # a time. These tests take turns.
        (cd "$REPO_ROOT" && GAGGIMATE_SIM_HOST="127.0.0.1:$SIM_PORT" uv run pytest -m simulator -n 0)
        ;;
    *) die "usage: sim.sh [build|run|serve|test|stop|clean]" ;;
esac
