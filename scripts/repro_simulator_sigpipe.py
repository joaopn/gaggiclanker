#!/usr/bin/env python
"""Reproduce: a client that hangs up mid-response kills the firmware simulator.

    uv run python scripts/repro_simulator_sigpipe.py

Exits non-zero while the bug exists, zero when it is fixed. It builds and
starts the simulator with `scripts/sim.sh serve`, so it wants :8080 to itself,
and it stops what it started.

The bug
-------

The simulator's web shim writes every HTTP response and every WebSocket frame
with a plain ``send()`` — no ``MSG_NOSIGNAL`` — and the process had SIGPIPE at
its default disposition, which is to die. One write to a socket whose peer had
already closed was therefore fatal, and the peer closing first is ordinary:
a browser tab shut mid-page, a client that got what it asked for and dropped
the connection, a test that finished a pass.

What it looked like from the outside was not a crash but a hang: the simulator
answered a request, went away, and the suite running against it timed out
waiting for a shot that would never arrive, then reported every test after it
as skipped for want of a simulator. The exit status, once something bothered to
collect it, was 141 — 128 + SIGPIPE.

This script makes the window certain rather than rare. It pipelines a long
batch of ``req:profiles:list`` requests down one WebSocket and never reads the
answers, with a receive buffer small enough that the simulator's own socket
buffer fills and it blocks inside its send loop; then it resets the connection
underneath it. The next write returns EPIPE, and with the default disposition
that write never returns at all.

The fix
-------

`scripts/sim.sh` ignores SIGPIPE before exec'ing the simulator (SIG_IGN
survives both the fork and the exec), so the write returns an error the shim
already handles — it breaks out of the send loop and drops the connection at
the next pump.
"""

from __future__ import annotations

import base64
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SIM_SH = REPO_ROOT / "scripts" / "sim.sh"

HOST = "127.0.0.1"
PORT = int(os.environ.get("GAGGIMATE_SIM_PORT", "8080"))
PID_FILE = Path(os.environ.get("GAGGIMATE_SIM_PID", "/tmp/gaggimate-sim.pid"))  # noqa: S108
LOG_FILE = Path(os.environ.get("GAGGIMATE_SIM_LOG", "/tmp/gaggimate-sim.log"))  # noqa: S108
STATUS_FILE = Path(os.environ.get("GAGGIMATE_SIM_STATUS", "/tmp/gaggimate-sim.status"))  # noqa: S108

#: Requests per round. The answers add up to more than the socket buffers hold,
#: which is the point: the simulator must still be writing when the peer goes.
REQUESTS_PER_ROUND = 2000

#: How long to let it write before the connection is reset under it.
WRITE_WINDOW_S = 0.3

#: How many times to do it. One is enough to kill an unfixed simulator; the
#: rest are there to show the fixed one is not merely lucky.
ROUNDS = 5


def masked_text_frame(payload: bytes) -> bytes:
    """One client-to-server WebSocket text frame, masked as RFC 6455 demands."""
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    else:
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    return bytes(header) + mask + masked


def hang_up_mid_response() -> None:
    """Ask for far more than fits in a socket, then reset it without reading."""
    sock = socket.socket()
    # A small receive buffer keeps the window tiny, so the simulator's own send
    # buffer fills after a few kilobytes and it is still writing when we go.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    sock.settimeout(10)
    try:
        sock.connect((HOST, PORT))
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET /ws HTTP/1.1\r\nHost: {HOST}:{PORT}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        handshake = sock.recv(4096)
        if b"101" not in handshake.split(b"\r\n")[0]:
            raise RuntimeError(f"the simulator refused the WebSocket handshake: {handshake!r}")
        batch = b"".join(
            masked_text_frame(json.dumps({"tp": "req:profiles:list", "rid": f"r{i}"}).encode())
            for i in range(REQUESTS_PER_ROUND)
        )
        try:
            sock.sendall(batch)
        except OSError:
            # It stopped reading us because it is busy writing to us. Fine.
            pass
        time.sleep(WRITE_WINDOW_S)
        # SO_LINGER with a zero timeout makes close() send a RST rather than a
        # FIN: every later write from the other end fails at once, which is the
        # abrupt hang-up a browser or a killed client produces.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    finally:
        sock.close()


def simulator_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def simulator_is_alive() -> bool:
    pid = simulator_pid()
    return pid is not None and Path(f"/proc/{pid}").exists()


def simulator_answers() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2) as probe:
            probe.sendall(f"GET /api/status HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
            return b"200" in probe.recv(256)
    except OSError:
        return False


def how_it_died() -> str:
    """The exit status `scripts/sim.sh` filed, if it was in a position to."""
    try:
        status = int(STATUS_FILE.read_text().strip())
    except (OSError, ValueError):
        return "no exit status was filed"
    if status > 128:
        signal_number = status - 128
        try:
            return f"exit status {status} — {signal.Signals(signal_number).name}"
        except ValueError:
            return f"exit status {status} — signal {signal_number}"
    return f"exit status {status}"


def last_log_lines(count: int = 5) -> str:
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()
    except OSError:
        return f"(no {LOG_FILE})"
    return "\n".join(f"    {line}" for line in lines[-count:])


def main() -> int:
    if simulator_answers():
        print(
            f"Something is already serving :{PORT}. This script starts and stops its own "
            f"simulator; stop that one first with `scripts/sim.sh stop`.",
            file=sys.stderr,
        )
        return 2

    print("Building and starting the simulator ...")
    # Whatever an earlier run left there is not how this one ends.
    STATUS_FILE.unlink(missing_ok=True)
    subprocess.run([str(SIM_SH), "serve"], check=True, cwd=REPO_ROOT)
    try:
        for round_number in range(1, ROUNDS + 1):
            hang_up_mid_response()
            time.sleep(0.4)
            if not simulator_is_alive():
                print(
                    f"FAIL: the simulator died when a client hung up on it "
                    f"(round {round_number} of {ROUNDS}, {how_it_died()}).\n"
                    f"Its log ends:\n{last_log_lines()}",
                    file=sys.stderr,
                )
                return 1
            if not simulator_answers():
                print(
                    f"FAIL: the simulator is still running but stopped answering "
                    f"/api/status after a client hung up on it (round {round_number}).\n"
                    f"Its log ends:\n{last_log_lines()}",
                    file=sys.stderr,
                )
                return 1
            print(f"round {round_number}: still up and answering")
    finally:
        subprocess.run([str(SIM_SH), "stop"], check=False, cwd=REPO_ROOT)

    print(f"OK: {ROUNDS} clients hung up mid-response and the simulator survived every one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
