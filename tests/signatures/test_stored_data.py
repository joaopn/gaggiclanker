"""The language reads what the derivation stored, and gets the answers the parsed log gives.

A read never parses a log: a shot's checks are worked out from its stored samples, its stored
phases and the shot row's flags. This pins that to the parsed log: for every fixture shot and
the constructed variants (a lever shot, with no scale, with no pressure sensor, a turbo's phase
names, names longer than the log keeps), every channel, op and window evaluates to the same
result on both, and "a phase began" reads the same.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.metric_language import (
    CHANNELS,
    OPS,
    Expression,
    ShotData,
    evaluate,
)
from gaggiclanker.domain.phase_metrics import phase_began, profile_phase_names
from gaggiclanker.domain.slog import Slog, parse_slog
from gaggiclanker.domain.unsampled import phases_unsampled
from gaggiclanker.signatures.checks import CheckSubject, stored_shot_data
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import SLOG_FIXTURES, fill_ended_shot
from tests.lever_shot import LEVER_PROFILE, lever_shot, without_pressure, without_scale
from tests.signatures.helpers import (
    LONG_NAME_SETS,
    long_name_profile,
    long_name_shot,
    turbo_profile,
    turbo_shot,
)

Case = tuple[str, Slog, dict[str, Any] | None]


def _cases() -> Iterator[Case]:
    for path in sorted(SLOG_FIXTURES.glob("*.slog")):
        yield path.stem, parse_slog(path.read_bytes()), None
    fill_slog, _, fill_profile = fill_ended_shot()
    yield "shot_225-with-its-profile", fill_slog, fill_profile
    yield "lever", lever_shot(), LEVER_PROFILE
    yield "lever-no-scale", without_scale(lever_shot()), LEVER_PROFILE
    yield "lever-no-pressure", without_pressure(lever_shot()), LEVER_PROFILE
    yield "turbo", turbo_shot(), turbo_profile()
    for number, names in enumerate(LONG_NAME_SETS):
        yield f"long-names-{number}", long_name_shot(names), long_name_profile(names)


CASES = list(_cases())


def _windows(names: list[str]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = [
        {},
        {"from": "first_drip", "to": "shot_end"},
        {"from": "peak_pressure", "to": {"anchor": "shot_end", "offset_s": -1}},
        {"from": {"at_s": 2}, "to": {"at_s": 9}},
    ]
    for name in dict.fromkeys(names):
        windows.append({"phase": name})
    if names:
        windows.append({"from": {"phase_start": names[0]}, "to": {"phase_end": names[-1]}})
    windows.append({"phase": "a phase the profile does not have"})
    return windows


def _expressions(names: list[str]) -> Iterator[Expression]:
    for channel in CHANNELS:
        for op in OPS:
            for window in _windows(names):
                body: dict[str, Any] = {"channel": channel, "op": op, "window": window}
                if op in ("time_to", "time_above", "time_below"):
                    body["threshold"] = 1.0
                for relative in (None, "target_yield"):
                    if relative is not None:
                        body["relative_to"] = relative
                    try:
                        yield Expression.model_validate(body)
                    except ValueError:
                        continue


@pytest.mark.parametrize(("name", "slog", "profile"), CASES, ids=[c[0] for c in CASES])
async def test_every_expression_reads_the_same_on_stored_data_as_on_the_parsed_log(
    db: Database, name: str, slog: Slog, profile: dict[str, Any] | None
) -> None:
    raw = slog_to_raw(slog)
    derived = derive_shot(slog, raw, device_id="000800", source="import", profile=profile)
    shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)
    phases = json.loads(derived.shot.phases_json or "[]")
    metrics = json.loads(derived.shot.diagnostics_json or "{}").get("metrics", {})
    diagnostics = json.loads(derived.shot.diagnostics_json or "{}")
    has_pressure = bool(diagnostics.get("has_pressure", True))
    subject = CheckSubject(
        shot_id=shot_id,
        profile_version_id=None,
        set_version_id=None,
        warnings=[],
        phases=phases,
        duration_s=derived.shot.duration_ms / 1000,
        scale_connected=derived.shot.scale_connected,
        final_weight_g=derived.shot.final_weight_g,
        target_yield_g=36.0,
        dose_g=18.0,
        has_pressure=has_pressure,
        per_phase=metrics.get("per_phase") is not False,
        metrics=metrics,
    )
    names_in_profile = profile_phase_names(profile)
    stored = stored_shot_data(
        (await SignatureRepository(db).stored_samples([shot_id]))[shot_id],
        phases,
        subject,
        names_in_profile,
    )
    final = derived.shot.final_weight_g if derived.shot.scale_connected else None
    parsed = ShotData.build(
        as_sample_dicts(slog),
        slog.transitions,
        profile_phases=names_in_profile,
        has_pressure=has_pressure,
        scale_connected=derived.shot.scale_connected,
        final_weight_g=final if final and final > 0 else None,
        target_yield_g=36.0,
        dose_g=18.0,
        unsampled=phases_unsampled(
            names_in_profile,
            as_sample_dicts(slog),
            slog.transitions,
            version=slog.version,
            has_pressure=has_pressure,
        ),
    )

    # The samples are the same numbers, field for field, bar `si` (not read by the language).
    assert [dict(s) for s in stored.samples] == [dict(s) for s in parsed.samples]
    assert [(p.number, p.name, p.start, p.end) for p in stored.phases] == [
        (p.number, p.name, p.start, p.end) for p in parsed.phases
    ]
    for number in range(8):
        assert phase_began(number, stored.samples) == phase_began(number, parsed.samples)
    assert [dict(p) for p in stored.unsampled] == [dict(p) for p in parsed.unsampled]

    names = [p.name for p in parsed.phases] or [str(n) for n in (names_in_profile or [])]
    checked = 0
    for expression in _expressions(names):
        assert evaluate(expression, stored) == evaluate(expression, parsed), (
            name,
            expression.model_dump_json(by_alias=True, exclude_none=True),
        )
        checked += 1
    assert checked > 500


def test_the_cases_cover_every_fixture_log() -> None:
    fixtures = {path.stem for path in Path(SLOG_FIXTURES).glob("*.slog")}
    assert fixtures and fixtures <= {c[0] for c in CASES}
