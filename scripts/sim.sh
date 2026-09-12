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
    # SDL_VIDEODRIVER=dummy so it runs without an X server; the web UI and the
    # WebSocket are what we are after, not the panel.
    ( cd "$SIM_WORKDIR" && SDL_VIDEODRIVER=dummy nohup "$PROGRAM" >"$SIM_LOG" 2>&1 & echo $! >"$SIM_PID" )
    for _ in $(seq 1 60); do
        if curl -fsS --max-time 1 "http://127.0.0.1:$SIM_PORT/api/status" >/dev/null 2>&1; then
            echo "sim.sh: up on http://127.0.0.1:$SIM_PORT"
            return 0
        fi
        sleep 1
    done
    die "the simulator did not answer /api/status within 60s; see $SIM_LOG"
}

stop() {
    [[ -f "$SIM_PID" ]] || { echo "sim.sh: nothing to stop"; return 0; }
    kill "$(cat "$SIM_PID")" 2>/dev/null || true
    rm -f "$SIM_PID"
    echo "sim.sh: stopped"
}

case "${1:-test}" in
    build) build ;;
    run)
        build
        [[ -x "$PROGRAM" ]] || die "the build produced no $PROGRAM"
        cd "$SIM_WORKDIR" && exec "$PROGRAM"
        ;;
    serve) serve ;;
    stop) stop ;;
    clean)
        stop
        rm -rf "$SIM_WORKDIR"
        echo "sim.sh: removed $SIM_WORKDIR"
        ;;
    test)
        serve
        trap stop EXIT
        (cd "$REPO_ROOT" && GAGGIMATE_SIM_HOST="127.0.0.1:$SIM_PORT" uv run pytest -m simulator)
        ;;
    *) die "usage: sim.sh [build|run|serve|test|stop|clean]" ;;
esac
