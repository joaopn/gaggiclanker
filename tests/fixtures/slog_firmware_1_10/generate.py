"""Write the two firmware 1.10 shot fixtures from real shots already in the tree.

    python tests/fixtures/slog_firmware_1_10/generate.py

* ``shot_225_v8.slog``: the real v7 shot ``slog_edge/shot_225_fill_ended_at_start.slog`` with
  the version byte set to 8 and, as firmware 1.10 writes it, the "active scale is healthy" bit
  (``si`` 0x0100) set on every sample whose Bluetooth scale bit (0x0004) is set. Every other
  byte is the real shot's: v8 keeps v7's layout.
* ``shot_196_legacy_v6.slog``: the real v5 shot ``slog/shot_196_baseline_high.slog`` with the
  version byte set to 6. A v5 file already has the two things that mark the legacy
  hardware-scale v6 (26-byte samples recorded in ``reserved0``, all 13 v5 fields), so the
  version byte is the only difference; its ``t`` stays a tick count.

Never a synthetic trace: a board writes what these files hold.
"""

from __future__ import annotations

import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent

#: Where `si` sits in a v7+ sample: after `t` (4 bytes) and eleven 2-byte fields.
SI_OFFSET = 4 + 2 * 11
SAMPLE_SIZE_V7 = 30
HEADER_SIZE = 512
VERSION_OFFSET = 4
BLUETOOTH_SCALE = 0x0004
ACTIVE_SCALE = 0x0100


def v8_from_v7(raw: bytes) -> bytes:
    out = bytearray(raw)
    assert out[VERSION_OFFSET] == 7 and out[5] == SAMPLE_SIZE_V7
    out[VERSION_OFFSET] = 8
    for base in range(HEADER_SIZE, len(out) - SAMPLE_SIZE_V7 + 1, SAMPLE_SIZE_V7):
        (si,) = struct.unpack_from("<H", out, base + SI_OFFSET)
        if si & BLUETOOTH_SCALE:
            struct.pack_into("<H", out, base + SI_OFFSET, si | ACTIVE_SCALE)
    return bytes(out)


def legacy_v6_from_v5(raw: bytes) -> bytes:
    out = bytearray(raw)
    assert out[VERSION_OFFSET] == 5 and out[5] == 26
    assert struct.unpack_from("<I", out, 12)[0] == 0x1FFF
    out[VERSION_OFFSET] = 6
    return bytes(out)


def main() -> None:
    real_v7 = (FIXTURES / "slog_edge" / "shot_225_fill_ended_at_start.slog").read_bytes()
    real_v5 = (FIXTURES / "slog" / "shot_196_baseline_high.slog").read_bytes()
    (HERE / "shot_225_v8.slog").write_bytes(v8_from_v7(real_v7))
    (HERE / "shot_196_legacy_v6.slog").write_bytes(legacy_v6_from_v5(real_v5))


if __name__ == "__main__":
    main()
