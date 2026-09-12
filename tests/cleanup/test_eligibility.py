"""Which shots may be deleted from the machine, and what happens to the ones that may not.

The rule is one function
(:func:`~gaggiclanker.cleanup.eligibility.ineligible_reason`) applied in two
places, and both are tested here: the pure form, over rows built by hand, and
the enforced form, through the write gate that a real `delete_shot` passes
through. The second is the one that matters — a rule the plan step applies and the
gate does not is a rule a background task can walk past.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.cleanup.eligibility import expected_slog_bytes, ineligible_reason
from gaggiclanker.db.repos.cleanup import CleanupCandidate, CleanupRepository
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.device.writes import DeviceWriteRefused
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.slog import header_size_for, sample_size_for
from tests.cleanup.conftest import CORRUPT_ID, FIRST_ID, machine_id


def _candidate(**overrides: object) -> CleanupCandidate:
    """A shot that is eligible, so each test changes exactly one thing."""
    version, mask, samples = 5, 0b1111, 100
    values: dict[str, object] = {
        "id": 1,
        "device_id": "000100",
        "machine_id": 1,
        "slog_version": version,
        "fields_mask": mask,
        "sample_count": samples,
        "raw_bytes": header_size_for(version) + samples * sample_size_for(version, mask),
    }
    values.update(overrides)
    return CleanupCandidate.model_validate(values)


def test_a_complete_archived_shot_is_eligible() -> None:
    candidate = _candidate()
    assert ineligible_reason(candidate, machine_id=1, device_id="000100") is None
    assert expected_slog_bytes(candidate) == candidate.raw_bytes


def test_a_shot_the_archive_has_never_seen_is_refused() -> None:
    reason = ineligible_reason(None, machine_id=1, device_id="000999")
    assert reason is not None
    assert "not in the archive" in reason


def test_a_shot_belonging_to_another_machine_is_refused() -> None:
    """Ids are a per-device counter, so a matching number proves nothing."""
    reason = ineligible_reason(_candidate(machine_id=2), machine_id=1, device_id="000100")
    assert reason is not None
    assert "different machine" in reason


def test_a_quarantined_shot_is_refused() -> None:
    """Its bytes did not parse, so a parser fix is still worth having the file for."""
    reason = ineligible_reason(_candidate(quarantined=True), machine_id=1, device_id="000100")
    assert reason is not None
    assert "quarantined" in reason


def test_a_shot_shorter_than_its_header_implies_is_refused() -> None:
    reason = ineligible_reason(_candidate(raw_bytes=512), machine_id=1, device_id="000100")
    assert reason is not None
    assert "the machine may hold bytes this box does not" in reason.lower()


def test_a_shot_with_no_stored_bytes_is_refused() -> None:
    reason = ineligible_reason(_candidate(raw_bytes=0), machine_id=1, device_id="000100")
    assert reason is not None
    assert "no raw bytes" in reason


def test_a_short_file_the_archive_already_called_incomplete_is_eligible() -> None:
    """The parser saw the shortfall and said so; the machine served these bytes.

    This is the one case where a length mismatch is not a refusal, and it has to
    be: the device was still writing the file when it was fetched, the fetch
    recorded that, and there is nothing more on the machine to lose.
    """
    candidate = _candidate(raw_bytes=600, incomplete=True)
    assert ineligible_reason(candidate, machine_id=1, device_id="000100") is None


def test_a_row_with_no_header_fields_is_refused() -> None:
    candidate = _candidate(slog_version=None, fields_mask=None)
    assert expected_slog_bytes(candidate) is None
    reason = ineligible_reason(candidate, machine_id=1, device_id="000100")
    assert reason is not None
    assert "cannot be checked" in reason


# ── the same rule, through the gate that actually enforces it ────────


async def test_the_gate_refuses_a_shot_that_is_not_in_the_archive(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """A frame never leaves, and the refusal is audited with its reason."""
    app, _ = writes_on
    with pytest.raises(DeviceWriteRefused) as caught:
        await app.state.device.delete_shot(999_999)
    assert "not in the archive" in str(caught.value)

    rows = await DeviceWritesRepository(app.state.db).list_writes()
    refusal = next(row for row in rows if row.kind == "shot_delete")
    assert refusal.result == "refused"
    assert refusal.device_id == pad6(999_999)
    assert "not in the archive" in refusal.error


async def test_the_gate_refuses_a_quarantined_shot(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writes_on
    with pytest.raises(DeviceWriteRefused, match="quarantined"):
        await app.state.device.delete_shot(CORRUPT_ID)


async def test_the_gate_refuses_every_delete_while_writes_are_off(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The master switch is checked before the per-kind rule, as for a profile."""
    app, _ = live
    with pytest.raises(DeviceWriteRefused, match="switched off"):
        await app.state.device.delete_shot(FIRST_ID)


async def test_the_gate_allows_a_shot_the_archive_holds_intact(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writes_on
    await app.state.device.delete_shot(FIRST_ID)

    rows = await DeviceWritesRepository(app.state.db).list_writes()
    write = next(row for row in rows if row.kind == "shot_delete")
    assert write.result == "ok"
    assert write.device_id == pad6(FIRST_ID)


async def test_the_candidate_query_only_sees_this_machine(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = live
    identifier = await machine_id(app)
    repo = CleanupRepository(app.state.db)
    assert await repo.candidate(identifier, pad6(FIRST_ID)) is not None
    assert await repo.candidate(identifier + 1, pad6(FIRST_ID)) is None
