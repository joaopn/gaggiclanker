"""`.slog` binary shot-log codec, versions 1 through 7.

Layout mirrors `src/display/models/shot_log_format.h` and the reference
reader `web/src/pages/ShotHistory/parseBinaryShot.js`; where those two differ
the header file wins.

The three version breaks that matter:

* **v5** grew the header from 128 to 512 bytes and moved phase information out
  of the samples into a 12-entry transition table in the header.
* **v6** widened the sample's `t` field from a `uint16` *sample index* to a
  `uint32` of real elapsed milliseconds. Everything else kept its width, so a
  v6 sample is 2 bytes longer than a v5 one with the same `fieldsMask`.
* **v7** added `wp` (cumulative water pumped) as mask bit 13, and repurposed
  two reserved header bytes as `finalExitReason` and `brewDelayMs`.

Parsing is deliberately forgiving about *content* and strict about *shape*: a
half-written file still yields every sample it does contain (flagged
``incomplete``), because the machine deletes its copy and a partial shot beats
no shot. But an unknown version raises rather than guessing a layout.

The encoder exists because the JSON importer re-creates raw bytes from the web UI's JSON
exports, and because a round-trip is the only honest test of a binary reader.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from gaggiclanker.domain.models import PhaseTransition, Sample, SlogHeader

MAGIC = 0x544F4853  # 'SHOT', little-endian
HEADER_SIZE_V4 = 128
HEADER_SIZE_V5 = 512
MAX_TRANSITIONS = 12
TRANSITION_SIZE = 29
TRANSITIONS_OFFSET = 110
TRANSITION_COUNT_OFFSET = TRANSITIONS_OFFSET + MAX_TRANSITIONS * TRANSITION_SIZE  # 458
FINAL_EXIT_REASON_OFFSET = TRANSITION_COUNT_OFFSET + 1  # 459
BREW_DELAY_OFFSET = TRANSITION_COUNT_OFFSET + 2  # 460

MIN_VERSION = 1
MAX_VERSION = 7

TEMP_SCALE = 10.0
PRESSURE_SCALE = 10.0
FLOW_SCALE = 100.0
WEIGHT_SCALE = 10.0
RESISTANCE_SCALE = 100.0


@dataclass(frozen=True)
class _FieldDef:
    """One `fieldsMask` bit: where it lands in a :class:`Sample` and its scale."""

    bit: int
    name: str
    signed: bool
    scale: float | None  # None => keep the raw integer


#: Mask bits in ascending order — which is exactly the order they are written
#: in each sample record, so this list *is* the sample layout.
FIELD_DEFS: tuple[_FieldDef, ...] = (
    _FieldDef(0, "t", signed=False, scale=None),
    _FieldDef(1, "tt", signed=False, scale=TEMP_SCALE),
    _FieldDef(2, "ct", signed=False, scale=TEMP_SCALE),
    _FieldDef(3, "tp", signed=False, scale=PRESSURE_SCALE),
    _FieldDef(4, "cp", signed=False, scale=PRESSURE_SCALE),
    _FieldDef(5, "fl", signed=True, scale=FLOW_SCALE),
    _FieldDef(6, "tf", signed=True, scale=FLOW_SCALE),
    _FieldDef(7, "pf", signed=True, scale=FLOW_SCALE),
    _FieldDef(8, "vf", signed=True, scale=FLOW_SCALE),
    _FieldDef(9, "v", signed=False, scale=WEIGHT_SCALE),
    _FieldDef(10, "ev", signed=False, scale=WEIGHT_SCALE),
    _FieldDef(11, "pr", signed=False, scale=RESISTANCE_SCALE),
    _FieldDef(12, "si", signed=False, scale=None),
    _FieldDef(13, "wp", signed=False, scale=WEIGHT_SCALE),
)

_BY_BIT = {f.bit: f for f in FIELD_DEFS}
_BY_NAME = {f.name: f for f in FIELD_DEFS}

#: All 14 fields — what current firmware writes.
FIELDS_MASK_ALL = 0x3FFF
#: 13 fields, i.e. everything but `wp` — what v5 firmware wrote.
FIELDS_MASK_V5 = 0x1FFF

TIME_BIT = 0


class SlogError(ValueError):
    """A `.slog` payload we cannot read."""


class UnsupportedSlogVersion(SlogError):
    """The file declares a format version this codec does not know."""

    def __init__(self, version: int) -> None:
        super().__init__(
            f"unsupported .slog version {version} (this build reads v{MIN_VERSION}-v{MAX_VERSION})"
        )
        self.version = version


@dataclass
class Slog:
    """A parsed shot log: the header, the samples, and how much we trust it."""

    header: SlogHeader
    samples: list[Sample] = field(default_factory=list)
    #: True when the file was never finalised (`sampleCount == 0`), claimed
    #: more samples than it carries, or ended mid-sample. The device is still
    #: writing, or it lost power; either way the samples present are real.
    incomplete: bool = False
    #: Bytes after the last whole sample. Non-zero means a torn write.
    trailing_bytes: int = 0
    shot_id: str | None = None

    @property
    def version(self) -> int:
        return self.header.version

    @property
    def sample_interval(self) -> int:
        return self.header.sample_interval

    @property
    def transitions(self) -> list[PhaseTransition]:
        return self.header.transitions

    @property
    def profile_id(self) -> str:
        return self.header.profile_id

    @property
    def profile_name(self) -> str:
        return self.header.profile_name

    @property
    def timestamp(self) -> int:
        return self.header.start_epoch

    @property
    def duration_ms(self) -> int:
        """Effective duration: the header's value, or the last sample's `t`.

        The header's `durationMs` is patched in when recording stops, so on an
        incomplete file it is stale or zero and the last sample is the only
        honest answer.
        """
        last_t = self.samples[-1].t if self.samples else None
        if not self.incomplete and self.header.duration_ms:
            return self.header.duration_ms
        return last_t or 0

    @property
    def volume_g(self) -> float | None:
        """Final beverage weight: the header's, else the last scale reading."""
        if self.header.final_weight_g:
            return self.header.final_weight_g
        last_v = self.samples[-1].v if self.samples else None
        return last_v if last_v else None

    @property
    def has_pressure(self) -> bool:
        """Whether this shot carries a real pressure trace.

        Standard boards have no sensor and record a hard zero for `cp`, which
        makes every pressure-derived diagnostic meaningless rather than merely
        flat. An all-zero `cp` column is the signal.
        """
        return any(s.cp for s in self.samples)


def header_size_for(version: int) -> int:
    """Header size in bytes for a format version."""
    return HEADER_SIZE_V4 if version <= 4 else HEADER_SIZE_V5


def sample_size_for(version: int, fields_mask: int) -> int:
    """Sample record size: 2 bytes per set mask bit, plus 2 more for v6+ `t`.

    From v6 the time field is a `uint32` rather than a `uint16`; every other
    field kept its width, so the whole widening shows up as this +2.
    """
    width = 2 * _popcount(fields_mask)
    if version >= 6 and fields_mask & (1 << TIME_BIT):
        width += 2
    return width


def _popcount(value: int) -> int:
    return bin(value).count("1")


def is_html_response(data: bytes) -> bool:
    """True when the device served its SPA instead of the file we asked for.

    The firmware's web server answers unknown paths with its own UI, and
    `/api/history` returns HTML under load. Detected before the size check so
    the operator gets "the device served HTML" rather than "file too small".
    """
    return data[:15].lower().startswith((b"<!doc", b"<html"))


def _decode_cstring(raw: bytes) -> str:
    end = raw.find(b"\x00")
    if end != -1:
        raw = raw[:end]
    return raw.decode("utf-8", errors="replace")


def _encode_cstring(text: str, size: int) -> bytes:
    """NUL-terminated UTF-8, truncated on a character boundary."""
    raw = text.encode("utf-8")[: size - 1]
    while raw:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
            continue
        break
    return raw.ljust(size, b"\x00")


def parse_slog(data: bytes, shot_id: str | None = None) -> Slog:
    """Parse `.slog` bytes into a :class:`Slog`.

    Raises:
        SlogError: bad magic, HTML instead of binary, a truncated header, or a
            sample size that contradicts the field mask.
        UnsupportedSlogVersion: the version byte is outside v1-v7.
    """
    if is_html_response(data):
        raise SlogError(
            "device returned an HTML page instead of binary shot data "
            "(the firmware serves its own UI for unknown paths, and "
            "/api/history answers HTML while an OTA update runs)"
        )
    if len(data) < 6:
        raise SlogError(f"shot file too small: {len(data)} bytes")

    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MAGIC:
        raise SlogError(f"invalid shot magic: 0x{magic:08x} (expected 0x{MAGIC:08x})")

    version = data[4]
    if not MIN_VERSION <= version <= MAX_VERSION:
        raise UnsupportedSlogVersion(version)

    device_sample_size = data[5]  # `reserved0`
    header_size = header_size_for(version)
    if len(data) < header_size:
        raise SlogError(
            f"shot file too small for v{version} header: need {header_size} bytes, got {len(data)}"
        )

    declared_header_size = struct.unpack_from("<H", data, 6)[0]
    # Files old enough to predate the field write 0; anything else that
    # disagrees is a layout we would misread.
    if declared_header_size not in (0, header_size):
        raise SlogError(
            f"header size mismatch for v{version}: "
            f"expected {header_size}, got {declared_header_size}"
        )

    sample_interval = struct.unpack_from("<H", data, 8)[0]
    fields_mask = struct.unpack_from("<I", data, 12)[0]
    sample_count = struct.unpack_from("<I", data, 16)[0]
    duration_ms = struct.unpack_from("<I", data, 20)[0]
    start_epoch = struct.unpack_from("<I", data, 24)[0]
    profile_id = _decode_cstring(data[28:60])
    profile_name = _decode_cstring(data[60:108])
    final_weight_raw = struct.unpack_from("<H", data, 108)[0]

    transitions: list[PhaseTransition] = []
    final_exit_reason = 0
    brew_delay_ms = 0
    if version >= 5:
        count = min(data[TRANSITION_COUNT_OFFSET], MAX_TRANSITIONS)
        for i in range(count):
            offset = TRANSITIONS_OFFSET + i * TRANSITION_SIZE
            sample_index, phase_number, reason = struct.unpack_from("<HBB", data, offset)
            transitions.append(
                PhaseTransition(
                    sample_index=sample_index,
                    phase_number=phase_number,
                    transition_reason=reason,
                    phase_name=_decode_cstring(data[offset + 4 : offset + TRANSITION_SIZE]),
                )
            )
        final_exit_reason = data[FINAL_EXIT_REASON_OFFSET]
        brew_delay_ms = struct.unpack_from("<H", data, BREW_DELAY_OFFSET)[0]

    if fields_mask == 0:
        raise SlogError("invalid shot file: fieldsMask is 0 (no fields recorded)")

    expected_sample_size = sample_size_for(version, fields_mask)
    if device_sample_size not in (0, expected_sample_size):
        raise SlogError(
            f"field mask indicates {_popcount(fields_mask)} fields "
            f"({expected_sample_size} bytes), but the device wrote "
            f"{device_sample_size} bytes per sample"
        )
    sample_size = expected_sample_size

    header = SlogHeader(
        version=version,
        sample_size=device_sample_size,
        header_size=header_size,
        sample_interval=sample_interval,
        fields_mask=fields_mask,
        sample_count=sample_count,
        duration_ms=duration_ms,
        start_epoch=start_epoch,
        profile_id=profile_id,
        profile_name=profile_name,
        final_weight_g=final_weight_raw / WEIGHT_SCALE if final_weight_raw else None,
        transitions=transitions,
        final_exit_reason=final_exit_reason,
        brew_delay_ms=brew_delay_ms,
    )

    body = len(data) - header_size
    available = body // sample_size
    trailing_bytes = body - available * sample_size
    usable = min(sample_count, available) if sample_count else available

    layout = [_BY_BIT[bit] for bit in range(32) if fields_mask & (1 << bit) and bit in _BY_BIT]
    unknown_bits = [bit for bit in range(32) if fields_mask & (1 << bit) and bit not in _BY_BIT]

    samples = [
        _read_sample(
            data, header_size + i * sample_size, layout, unknown_bits, version, sample_interval
        )
        for i in range(usable)
    ]
    apply_phases(samples, transitions, version)

    incomplete = sample_count == 0 or trailing_bytes != 0 or sample_count > available

    return Slog(
        header=header,
        samples=samples,
        incomplete=incomplete,
        trailing_bytes=trailing_bytes,
        shot_id=shot_id,
    )


def _read_sample(
    data: bytes,
    base: int,
    layout: list[_FieldDef],
    unknown_bits: list[int],
    version: int,
    sample_interval: int,
) -> Sample:
    values: dict[str, float | int | None] = {}
    offset = base
    # Unknown mask bits still occupy 2 bytes each; walking every set bit in
    # ascending order keeps the known fields aligned even on a newer firmware.
    for bit in sorted([f.bit for f in layout] + unknown_bits):
        fdef = _BY_BIT.get(bit)
        if fdef is None:
            offset += 2
            continue
        if bit == TIME_BIT:
            if version >= 6:
                values["t"] = struct.unpack_from("<I", data, offset)[0]
                offset += 4
            else:
                # v1-v5 store a sample *index*; real time is index * interval.
                values["t"] = struct.unpack_from("<H", data, offset)[0] * sample_interval
                offset += 2
            continue
        raw = struct.unpack_from("<h" if fdef.signed else "<H", data, offset)[0]
        values[fdef.name] = raw if fdef.scale is None else raw / fdef.scale
        offset += 2
    return Sample.model_validate(values)


def apply_phases(samples: list[Sample], transitions: list[PhaseTransition], version: int) -> None:
    """Stamp each sample with the phase that was active when it was recorded.

    The transition table records the sample index at which each phase became
    active, so the phase of sample *i* is the last transition at or before it.

    From v5 every sample has a phase, even where the table is empty or starts
    late: the firmware's own reader defaults to phase 0 named "Phase 1" there,
    and a shot whose early samples had no phase at all would slice differently
    from the way the device's UI draws it. Before v5 there is no table, and
    None is the honest answer.
    """
    if version < 5:
        return
    for i, sample in enumerate(samples):
        sample.phase = 0
        # The reader's fallback label is 1-based for display, so an unnamed
        # phase reads here the way the machine's own screen shows it.
        sample.phase_name = "Phase 1"
        for transition in transitions:
            if i >= transition.sample_index:
                sample.phase = transition.phase_number
                sample.phase_name = transition.phase_name
            else:
                break


def encode_slog(slog: Slog) -> bytes:
    """Render a :class:`Slog` back to `.slog` bytes.

    The inverse of :func:`parse_slog` for everything the format carries. Values
    are re-scaled and clamped the way `ShotHistoryPlugin.cpp:34-68` does, so a
    parse-encode round trip on a device file is byte-identical and an
    encode-parse round trip on synthetic data is value-identical.

    `header.sample_count` and `header.duration_ms` are written as given; pass a
    header whose counts match the samples unless you are deliberately building
    a torn file.
    """
    header = slog.header
    version = header.version
    if not MIN_VERSION <= version <= MAX_VERSION:
        raise UnsupportedSlogVersion(version)
    if header.fields_mask == 0:
        raise SlogError("cannot encode a shot with an empty fieldsMask")

    header_size = header_size_for(version)
    sample_size = sample_size_for(version, header.fields_mask)

    out = bytearray(header_size)
    struct.pack_into(
        "<IBBHHHIIII32s48sH",
        out,
        0,
        MAGIC,
        version,
        sample_size,
        header_size,
        header.sample_interval,
        0,  # reserved1
        header.fields_mask,
        header.sample_count,
        header.duration_ms,
        header.start_epoch,
        _encode_cstring(header.profile_id, 32),
        _encode_cstring(header.profile_name, 48),
        _clamp_u16(header.final_weight_g, WEIGHT_SCALE),
    )

    if version >= 5:
        transitions = header.transitions[:MAX_TRANSITIONS]
        for i, transition in enumerate(transitions):
            struct.pack_into(
                "<HBB25s",
                out,
                TRANSITIONS_OFFSET + i * TRANSITION_SIZE,
                transition.sample_index,
                transition.phase_number,
                transition.transition_reason,
                _encode_cstring(transition.phase_name, 25),
            )
        out[TRANSITION_COUNT_OFFSET] = len(transitions)
        out[FINAL_EXIT_REASON_OFFSET] = header.final_exit_reason
        struct.pack_into("<H", out, BREW_DELAY_OFFSET, header.brew_delay_ms)

    layout = [
        _BY_BIT[bit] for bit in range(32) if header.fields_mask & (1 << bit) and bit in _BY_BIT
    ]
    if _popcount(header.fields_mask) != len(layout):
        raise SlogError("cannot encode a fieldsMask with bits this build does not know")

    body = bytearray()
    for sample in slog.samples:
        record = bytearray(sample_size)
        offset = 0
        for fdef in layout:
            # Every value written here goes through the same two functions the
            # importer reads through (`stored_time`, `stored_sample_value`).
            # One quantisation path, so an imported shot and a fetched one
            # cannot round a sample differently and score differently for it.
            if fdef.bit == TIME_BIT:
                stored = stored_time(
                    sample.t, version=version, sample_interval=header.sample_interval
                )
                if version >= 6:
                    struct.pack_into("<I", record, offset, stored)
                    offset += 4
                else:
                    struct.pack_into("<H", record, offset, stored)
                    offset += 2
                continue
            raw = stored_sample_value(fdef.name, getattr(sample, fdef.name))
            struct.pack_into("<h" if fdef.signed else "<H", record, offset, raw)
            offset += 2
        body += record

    return bytes(out + body)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _round_half_away(value: float) -> int:
    """Round half away from zero — what the firmware's `lroundf` does.

    Python's `round` is banker's rounding, which would put 0.05 bar on the
    wrong side of the scale factor half the time and make round-trips flaky.
    """
    return int(value + 0.5) if value >= 0 else int(value - 0.5)


def _writable(value: float | None) -> bool:
    """False for anything the firmware would have written as a plain zero.

    NaN and infinity reach the encoder from a JSON export whose producer did
    arithmetic on a missing reading. `ShotHistoryPlugin.cpp:34-68` maps both to
    0 before writing; doing the same here keeps a re-encoded file identical to
    what the machine would have produced, and keeps `struct.pack` from raising
    on a value it cannot represent.
    """
    return value is not None and bool(value) and math.isfinite(value)


def _clamp_u16(value: float | None, scale: float) -> int:
    if not _writable(value):
        return 0
    return _clamp(_round_half_away((value or 0.0) * scale), 0, 0xFFFF)


def _clamp_i16(value: float | None, scale: float) -> int:
    if not _writable(value):
        return 0
    return _clamp(_round_half_away((value or 0.0) * scale), -0x8000, 0x7FFF)


# ── quantisation ─────────────────────────────────────────────────────
#
# The JSON exports carry real units rounded to two decimals; the binary format
# carries scaled 16-bit integers. The importer rebuilds a `.slog` from an export, so the
# importer has to land on exactly the values a device-fetched file would have
# produced — otherwise the same shot scores differently depending on which way
# it entered the archive. These two functions are the encoder's own arithmetic,
# exposed so the importer cannot drift from it.


def stored_sample_value(name: str, value: float | None) -> int:
    """The integer the file holds for a real-unit value: scaled, rounded, clamped.

    The encoder writes exactly this and :func:`quantise_sample_value` reads
    exactly this back, which is what makes the two agree by construction rather
    than by two pieces of arithmetic that look alike.

    ``None`` becomes 0: the caller only asks about fields whose `fieldsMask` bit
    is *set*, and the firmware writes a plain zero for a value it does not have
    (`ShotHistoryPlugin.cpp:34-68`), as does this encoder.
    """
    fdef = _BY_NAME[name]
    if fdef.scale is None:
        if value is None or not _writable(value):
            return 0
        return _clamp(int(value), 0, 0xFFFF)
    return _clamp_i16(value, fdef.scale) if fdef.signed else _clamp_u16(value, fdef.scale)


def stored_time(t: int | None, *, version: int, sample_interval: int) -> int:
    """The integer the file holds for an elapsed time.

    Two different things depending on the version, which is the whole reason
    this is a function: before v6 the field is a sample *index*, from v6 it is a
    `uint32` of real milliseconds.
    """
    value = max(int(t or 0), 0)
    if version >= 6:
        return min(value, 0xFFFFFFFF)
    return min(value // (sample_interval or 1), 0xFFFF)


def quantise_sample_value(name: str, value: float | None) -> float | int | None:
    """A real-unit value as the format would store it and read it back.

    ``None`` quantises to the field's zero rather than to ``None``, on purpose:
    the caller only asks about fields whose `fieldsMask` bit is *set*, and for
    those the encoder writes 0 and the parser reads 0 back. "The mask says this
    field exists but the export omitted it" is a zero, not an absence.
    """
    fdef = _BY_NAME[name]
    raw = stored_sample_value(name, value)
    return raw if fdef.scale is None else raw / fdef.scale


def quantise_time(t: int | None, *, version: int, sample_interval: int) -> int:
    """Elapsed milliseconds as the format would store them and read them back.

    Before v6 the file stores a sample *index* and the reader multiplies it by
    the header's interval, so any `t` that is not a whole number of intervals
    comes back rounded down. From v6 the field is a real `uint32` millisecond
    count and survives as it is.
    """
    stored = stored_time(t, version=version, sample_interval=sample_interval)
    return stored if version >= 6 else stored * (sample_interval or 1)
