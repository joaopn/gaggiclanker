"""Builders shared by the domain tests.

Two of them exist so tests can speak in the same dict-shaped samples the
vendored upstream suite used, without every test having to know how a
:class:`Slog` is assembled.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog
from gaggiclanker.domain.models import PhaseTransition, Sample, SlogHeader
from gaggiclanker.domain.phase_control import PhaseControl, phase_controls
from gaggiclanker.domain.slog import FIELDS_MASK_ALL, Slog

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SLOG_FIXTURES = FIXTURES / "slog"
EXPORT_FIXTURES = FIXTURES / "exports"
PROFILE_FIXTURES = FIXTURES / "profiles"
#: Real shots with a shape the three vendored ones lack, and the profiles they were pulled on.
#: Their own directory, so the tests that cover every vendored shot against a golden do not.
EDGE_FIXTURES = FIXTURES / "slog_edge"
CONSTRUCTED_PROFILES = FIXTURES / "constructed_profiles"


def constructed_profile(shot: str, variant: str = "pressure-first") -> dict[str, Any]:
    """The profile constructed for a real fixture shot (`shot_196`, `shot_129`, ...).

    The real profiles no longer exist; see the README beside the files for how
    these were made and what the two variants mean.
    """
    profile: dict[str, Any] = json.loads(
        (CONSTRUCTED_PROFILES / f"{shot}_{variant}.json").read_text()
    )
    return profile


def constructed_profile_for(slog_path: Path, variant: str = "pressure-first") -> dict[str, Any]:
    """The constructed profile of a real `.slog` fixture (`shot_204_ramping_flow.slog`)."""
    return constructed_profile("_".join(slog_path.stem.split("_")[:2]), variant)


def constructed_controls(shot: str, variant: str = "pressure-first") -> tuple[PhaseControl, ...]:
    """What each phase of :func:`constructed_profile` steers by."""
    controls = phase_controls(constructed_profile(shot, variant))
    assert controls is not None
    return controls


def make_slog(
    samples: list[dict[str, Any]],
    phases: list[tuple[int, int, str]] | None = None,
    *,
    version: int = 7,
    sample_interval: int = 250,
    duration_ms: int | None = None,
    profile_id: str = "test",
    profile_name: str = "Test Profile",
    timestamp: int = 1_640_000_000,
    final_weight_g: float | None = None,
    fields_mask: int = FIELDS_MASK_ALL,
    shot_id: str | None = "000001",
) -> Slog:
    """Build a :class:`Slog` from dict-shaped samples.

    `phases` entries are ``(sample_index, phase_number, phase_name)``.
    """
    transitions = [
        PhaseTransition(sample_index=i, phase_number=n, phase_name=name)
        for i, n, name in (phases or [])
    ]
    parsed = [Sample.model_validate(s) for s in samples]
    for i, sample in enumerate(parsed):
        for transition in transitions:
            if i >= transition.sample_index:
                sample.phase = transition.phase_number
                sample.phase_name = transition.phase_name
            else:
                break
    header = SlogHeader(
        version=version,
        sample_size=0,
        header_size=512 if version >= 5 else 128,
        sample_interval=sample_interval,
        fields_mask=fields_mask,
        sample_count=len(parsed),
        duration_ms=duration_ms if duration_ms is not None else len(parsed) * sample_interval,
        start_epoch=timestamp,
        profile_id=profile_id,
        profile_name=profile_name,
        final_weight_g=final_weight_g,
        transitions=transitions,
    )
    return Slog(header=header, samples=parsed, shot_id=shot_id)


def load_export(name: str) -> dict[str, Any]:
    """Read one of the maintainer's real exports from `tests/fixtures/exports`."""
    data: dict[str, Any] = json.loads((EXPORT_FIXTURES / name).read_text())
    return data


def slog_from_export(name: str) -> Slog:
    """The :class:`Slog` one of those exports describes.

    The conversion itself lives in the package now
    (:func:`gaggiclanker.domain.exports.shot_export_to_slog`) — the export
    is the firmware's own JS parser's reading of a `.slog`, so re-encoding it and
    parsing the bytes back is a round trip through *their* implementation, which
    is what makes these fixtures usable as ground truth.
    """
    return shot_export_to_slog(ShotExport.model_validate(load_export(name)))


def upstream_shot(
    *,
    samples: list[dict[str, Any]],
    phases: list[PhaseTransition] | None = None,
    id: str = "000001",
    version: int = 5,
    fields_mask: int = FIELDS_MASK_ALL,
    sample_count: int | None = None,
    sample_interval: int = 250,
    profile_id: str = "test",
    profile_name: str = "Test Profile",
    timestamp: int = 1_640_000_000,
    rating: int = 0,
    duration: int = 30_000,
    weight: float | None = None,
    incomplete: bool = False,
) -> Slog:
    """Build a :class:`Slog` from gaggimate-mcp's `ShotData` keyword shape.

    The vendored diagnostics tests construct shots this way. Keeping the
    signature lets them be ported almost literally, which is the point: their
    value is in the exact numbers they assert, and a rewrite would lose that.
    `rating` is accepted and ignored — it never lived in the binary file.
    """
    del rating
    transitions = list(phases or [])
    parsed = [Sample.model_validate(s) for s in samples]
    for i, sample in enumerate(parsed):
        for transition in transitions:
            if i >= transition.sample_index:
                sample.phase = transition.phase_number
                sample.phase_name = transition.phase_name
            else:
                break
    header = SlogHeader(
        version=version,
        sample_size=0,
        header_size=512 if version >= 5 else 128,
        sample_interval=sample_interval,
        fields_mask=fields_mask,
        sample_count=len(parsed) if sample_count is None else sample_count,
        duration_ms=duration,
        start_epoch=timestamp,
        profile_id=profile_id,
        profile_name=profile_name,
        final_weight_g=weight,
        transitions=transitions,
    )
    return Slog(header=header, samples=parsed, incomplete=incomplete, shot_id=id)


#: What a GaggiMate Standard board logs as zero on every sample: it has no
#: pressure sensor and no dimmed pump, and the firmware sends no target flow
#: without the pressure capability. The temperature and the scale stay real.
STANDARD_BOARD_ZEROED = ("cp", "tp", "pf", "tf", "fl", "pr", "wp")


def standard_board(slog: Slog) -> Slog:
    """The same shot as a Standard board would have logged it."""
    samples = [
        s.model_copy(update={f: 0.0 for f in STANDARD_BOARD_ZEROED if getattr(s, f) is not None})
        for s in slog.samples
    ]
    return dataclasses.replace(slog, samples=samples)


def fill_ended_shot() -> tuple[Slog, bytes, dict[str, Any]]:
    """The real shot whose fill ended before its first sample, its bytes and its profile.

    A spring-lever profile whose Fill exits on 2.8 bar met a group still at 4.8 bar: the log
    opens in the Ramp, with one transition row ``(sample 0, phase 1, reason: pressure target)``.
    """
    from gaggiclanker.domain.slog import parse_slog

    raw = (EDGE_FIXTURES / "shot_225_fill_ended_at_start.slog").read_bytes()
    profile: dict[str, Any] = json.loads((EDGE_FIXTURES / "alma_lever_16_32.json").read_text())
    return parse_slog(raw, "000225"), raw, profile


def with_phase_table(slog: Slog, rows: list[tuple[int, int, int, str]]) -> Slog:
    """A real shot with its phase table rewritten: ``(first sample, phase number, reason, name)``.

    The samples take the phase of the last row that has started by their index, as the machine
    writes them, so the table and the samples agree. The rest of the shot is the real one.
    """
    transitions = [
        PhaseTransition(
            sample_index=start, phase_number=number, transition_reason=reason, phase_name=name
        )
        for start, number, reason, name in rows
    ]
    samples = []
    for index, original in enumerate(slog.samples):
        row = max((t for t in transitions if t.sample_index <= index), key=lambda t: t.sample_index)
        samples.append(
            original.model_copy(update={"phase": row.phase_number, "phase_name": row.phase_name})
        )
    header = slog.header.model_copy(update={"transitions": transitions})
    return dataclasses.replace(slog, header=header, samples=samples)
