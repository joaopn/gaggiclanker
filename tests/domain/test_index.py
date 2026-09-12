"""`index.bin` codec tests."""

from __future__ import annotations

import struct

import pytest

from gaggiclanker.domain.index import (
    INDEX_ENTRY_SIZE,
    INDEX_HEADER_SIZE,
    INDEX_MAGIC,
    ShotIndexError,
    encode_index,
    parse_index,
)
from gaggiclanker.domain.models import (
    SHOT_FLAG_COMPLETED,
    SHOT_FLAG_DELETED,
    SHOT_FLAG_HAS_NOTES,
    IndexEntry,
    IndexHeader,
    ShotIndex,
)


def _index(*entries: IndexEntry, next_id: int = 1) -> ShotIndex:
    return ShotIndex(
        header=IndexHeader(
            version=1, entry_size=INDEX_ENTRY_SIZE, entry_count=len(entries), next_id=next_id
        ),
        entries=list(entries),
    )


def _entry(**over: object) -> IndexEntry:
    base: dict[str, object] = {
        "id": 129,
        "timestamp": 1_789_063_889,
        "duration_ms": 54_617,
        "volume_g": 32.1,
        "rating": 4,
        "flags": SHOT_FLAG_COMPLETED,
        "profile_id": "rV4GhUcSZc",
        "profile_name": "Gratus 16:32 trad",
        "avg_temp_c": 86.4,
        "max_pressure_bar": 8.9,
        "avg_flow_ml_s": 1.83,
    }
    base.update(over)
    return IndexEntry.model_validate(base)


def test_round_trip() -> None:
    original = _index(_entry(), _entry(id=130, rating=0, volume_g=None), next_id=131)
    parsed = parse_index(encode_index(original))

    assert parsed.header.version == 1
    assert parsed.header.entry_size == INDEX_ENTRY_SIZE
    assert parsed.header.entry_count == 2
    assert parsed.header.next_id == 131
    assert len(parsed.entries) == 2

    first = parsed.entries[0]
    assert first.id == 129
    assert first.duration_ms == 54_617
    assert first.volume_g == pytest.approx(32.1)
    assert first.rating == 4
    assert first.profile_id == "rV4GhUcSZc"
    assert first.profile_name == "Gratus 16:32 trad"
    assert first.avg_temp_c == pytest.approx(86.4)
    assert first.max_pressure_bar == pytest.approx(8.9)
    assert first.avg_flow_ml_s == pytest.approx(1.83)

    assert parsed.entries[1].volume_g is None


def test_encoded_size_is_exactly_the_declared_layout() -> None:
    raw = encode_index(_index(_entry(), _entry(id=130)))
    assert len(raw) == INDEX_HEADER_SIZE + 2 * INDEX_ENTRY_SIZE
    assert struct.unpack_from("<I", raw, 0)[0] == INDEX_MAGIC


def test_flags_decode_to_properties() -> None:
    entry = _entry(flags=SHOT_FLAG_COMPLETED | SHOT_FLAG_HAS_NOTES)
    assert entry.completed is True
    assert entry.has_notes is True
    assert entry.deleted is False
    assert entry.incomplete is False

    torn = _entry(flags=0)
    assert torn.completed is False
    assert torn.incomplete is True


def test_zero_aggregates_read_as_not_recorded() -> None:
    """Older firmware wrote zeros where the aggregates now live.

    Reporting 0 °C average would be a lie about a shot that was brewed fine;
    None says "this firmware did not record it", which is the truth.
    """
    raw = encode_index(_index(_entry(avg_temp_c=None, max_pressure_bar=None, avg_flow_ml_s=None)))
    entry = parse_index(raw).entries[0]
    assert entry.avg_temp_c is None
    assert entry.max_pressure_bar is None
    assert entry.avg_flow_ml_s is None


def test_live_filters_deleted_and_sorts_newest_first() -> None:
    """Deletion is flag-only on the device; nothing is ever removed from the file."""
    index = _index(
        _entry(id=1, timestamp=100),
        _entry(id=2, timestamp=300, flags=SHOT_FLAG_COMPLETED | SHOT_FLAG_DELETED),
        _entry(id=3, timestamp=200),
    )
    assert [e.id for e in index.live()] == [3, 1]


def test_duplicate_ids_are_preserved() -> None:
    """A rebuilt index can carry the same id twice; both rows must survive."""
    index = _index(_entry(id=7), _entry(id=7, flags=SHOT_FLAG_COMPLETED | SHOT_FLAG_DELETED))
    parsed = parse_index(encode_index(index))
    assert [e.id for e in parsed.entries] == [7, 7]
    assert [e.deleted for e in parsed.entries] == [False, True]


def test_empty_index_round_trips() -> None:
    parsed = parse_index(encode_index(_index(next_id=1)))
    assert parsed.entries == []
    assert parsed.header.entry_count == 0


def test_bad_magic_is_rejected() -> None:
    raw = bytearray(encode_index(_index(_entry())))
    struct.pack_into("<I", raw, 0, 0xDEADBEEF)
    with pytest.raises(ShotIndexError, match="invalid index magic"):
        parse_index(bytes(raw))


def test_unknown_entry_size_is_rejected() -> None:
    raw = bytearray(encode_index(_index(_entry())))
    struct.pack_into("<H", raw, 6, 64)
    with pytest.raises(ShotIndexError, match="entry size"):
        parse_index(bytes(raw))


def test_truncated_index_is_rejected() -> None:
    raw = encode_index(_index(_entry(), _entry(id=130)))
    with pytest.raises(ShotIndexError, match="truncated"):
        parse_index(raw[:-10])


def test_too_small_for_a_header() -> None:
    with pytest.raises(ShotIndexError, match="too small"):
        parse_index(b"\x00" * 10)


def test_profile_name_survives_a_multibyte_round_trip() -> None:
    parsed = parse_index(encode_index(_index(_entry(profile_name="Crème brûlée — 1:2"))))
    assert parsed.entries[0].profile_name == "Crème brûlée — 1:2"
