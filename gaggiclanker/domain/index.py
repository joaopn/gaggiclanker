"""`index.bin` codec — the device's shot index.

Layout from `ShotIndexHeader` / `ShotIndexEntry` in `shot_log_format.h`, with
the JS reader `parseBinaryIndex.js` as the cross-check. 32-byte header, then
128-byte entries appended in id order.

Two properties of the file drive how the sync layer uses it: entries are never
removed (a deletion only sets a flag, and duplicate ids can exist), and the
per-shot aggregates were carved out of what used to be reserved padding, so a
zero there means "this firmware did not record it" rather than "zero".
"""

from __future__ import annotations

import struct

from gaggiclanker.domain.models import IndexEntry, IndexHeader, ShotIndex

INDEX_MAGIC = 0x58444953  # 'SIDX', little-endian
INDEX_HEADER_SIZE = 32
INDEX_ENTRY_SIZE = 128
INDEX_VERSION = 1

TEMP_SCALE = 10.0
PRESSURE_SCALE = 10.0
FLOW_SCALE = 100.0
WEIGHT_SCALE = 10.0


class ShotIndexError(ValueError):
    """An `index.bin` payload we cannot read."""


def _decode_cstring(raw: bytes) -> str:
    end = raw.find(b"\x00")
    if end != -1:
        raw = raw[:end]
    return raw.decode("utf-8", errors="replace")


def _encode_cstring(text: str, size: int) -> bytes:
    raw = text.encode("utf-8")[: size - 1]
    while raw:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
            continue
        break
    return raw.ljust(size, b"\x00")


def _scaled(raw: int, scale: float) -> float | None:
    """Zero on the wire means 'not recorded', which is not the same as 0.0."""
    return raw / scale if raw > 0 else None


def parse_index(data: bytes) -> ShotIndex:
    """Parse `index.bin` bytes.

    Raises:
        ShotIndexError: bad magic, an entry size we do not know, or a file
            shorter than its own entry count claims.
    """
    if len(data) < INDEX_HEADER_SIZE:
        raise ShotIndexError(f"index file too small: {len(data)} bytes")

    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != INDEX_MAGIC:
        raise ShotIndexError(f"invalid index magic: 0x{magic:08x} (expected 0x{INDEX_MAGIC:08x})")

    version, entry_size, entry_count, next_id = struct.unpack_from("<HHII", data, 4)

    if entry_size != INDEX_ENTRY_SIZE:
        raise ShotIndexError(
            f"unsupported index entry size {entry_size} (expected {INDEX_ENTRY_SIZE})"
        )

    expected = INDEX_HEADER_SIZE + entry_count * INDEX_ENTRY_SIZE
    if len(data) < expected:
        raise ShotIndexError(f"index file truncated: {len(data)} bytes (expected {expected})")

    entries: list[IndexEntry] = []
    for i in range(entry_count):
        base = INDEX_HEADER_SIZE + i * INDEX_ENTRY_SIZE
        entry_id, timestamp, duration, volume, rating, flags = struct.unpack_from(
            "<IIIHBB", data, base
        )
        avg_temp, max_pressure, avg_flow = struct.unpack_from("<HHH", data, base + 96)
        entries.append(
            IndexEntry(
                id=entry_id,
                timestamp=timestamp,
                duration_ms=duration,
                volume_g=_scaled(volume, WEIGHT_SCALE),
                rating=rating,
                flags=flags,
                profile_id=_decode_cstring(data[base + 16 : base + 48]),
                profile_name=_decode_cstring(data[base + 48 : base + 96]),
                avg_temp_c=_scaled(avg_temp, TEMP_SCALE),
                max_pressure_bar=_scaled(max_pressure, PRESSURE_SCALE),
                avg_flow_ml_s=_scaled(avg_flow, FLOW_SCALE),
            )
        )

    return ShotIndex(
        header=IndexHeader(
            version=version,
            entry_size=entry_size,
            entry_count=entry_count,
            next_id=next_id,
        ),
        entries=entries,
    )


def encode_index(index: ShotIndex) -> bytes:
    """Render a :class:`ShotIndex` back to `index.bin` bytes.

    The header's `entry_count` is taken from the entry list rather than the
    header field, so an encoded index is always self-consistent.
    """
    out = bytearray(INDEX_HEADER_SIZE)
    struct.pack_into(
        "<IHHII",
        out,
        0,
        INDEX_MAGIC,
        index.header.version or INDEX_VERSION,
        INDEX_ENTRY_SIZE,
        len(index.entries),
        index.header.next_id,
    )

    for entry in index.entries:
        row = bytearray(INDEX_ENTRY_SIZE)
        struct.pack_into(
            "<IIIHBB",
            row,
            0,
            entry.id,
            entry.timestamp,
            entry.duration_ms,
            _raw(entry.volume_g, WEIGHT_SCALE),
            entry.rating,
            entry.flags,
        )
        row[16:48] = _encode_cstring(entry.profile_id, 32)
        row[48:96] = _encode_cstring(entry.profile_name, 48)
        struct.pack_into(
            "<HHH",
            row,
            96,
            _raw(entry.avg_temp_c, TEMP_SCALE),
            _raw(entry.max_pressure_bar, PRESSURE_SCALE),
            _raw(entry.avg_flow_ml_s, FLOW_SCALE),
        )
        out += row

    return bytes(out)


def _raw(value: float | None, scale: float) -> int:
    if not value:
        return 0
    scaled = value * scale
    rounded = int(scaled + 0.5) if scaled >= 0 else int(scaled - 0.5)
    return max(0, min(0xFFFF, rounded))
