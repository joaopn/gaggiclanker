#!/usr/bin/env python
"""Reproduce: a profile phase name longer than 24 bytes is never matched in the shot's log.

    uv run python scripts/repro_long_phase_names.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The firmware logs each phase's name in `char phaseName[25]` (`shot_log_format.h`, filled with
`strncpy`), so a log holds at most the first 24 bytes of a name while the profile holds all of
it. Comparing the two as strings fails for any phase whose name is longer: the metric
language's phase windows ("the cup at the end of the <long name>") and span anchors read
"the profile has this phase, the shot did not reach it" for a phase the shot ran, and
whatever is built on a name (a signature's `reached`, `expects_warning` and measures) reads
a false `skipped` or "not measured".

The check builds a three-phase shot from the constructed lever shot with phase names of 29 to 35
bytes (one with a multi-byte character straddling the cut), logs them cut as the firmware does,
and requires that: no phase is called skipped, a window over each long name evaluates, and
(where signatures exist) a `reached` and an `expects_warning` on a long name hold.
"""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gaggiclanker.domain.diagnostics import as_sample_dicts  # noqa: E402
from gaggiclanker.domain.exports import slog_to_raw  # noqa: E402
from gaggiclanker.domain.metric_language import Expression, ShotData, evaluate  # noqa: E402
from gaggiclanker.domain.phase_metrics import profile_phase_names  # noqa: E402
from gaggiclanker.domain.slog import Slog  # noqa: E402
from gaggiclanker.domain.warnings import shot_warnings  # noqa: E402
from gaggiclanker.sync.derive import derive_shot  # noqa: E402
from tests.lever_shot import LEVER_PROFILE, lever_shot  # noqa: E402

#: The profile's names, whole; what the firmware logs of them is `_logged` (24 bytes).
NAME_SETS = (
    (
        "Pre-infusion with a long soak",
        "Ramp up to the first pressure",
        "x" + "É" * 12 + " and a long tail",
    ),
    # The firmware cuts the raw bytes: spaces inside and in front of a name count in the 24.
    (
        "Pre  infusion with a long soak",
        " Ramp up to the full nine bar",
        "Final  push to the cup, long",
    ),
)


def _logged(name: str) -> str:
    raw = name.encode("utf-8")[:24]
    return raw.decode("utf-8", errors="ignore")


def build(names_in: tuple[str, ...]) -> tuple[Slog, dict[str, Any]]:
    profile: dict[str, Any] = {
        **LEVER_PROFILE,
        "phases": [dict(p) for p in LEVER_PROFILE["phases"][:3]],
    }
    for phase, name in zip(profile["phases"], names_in, strict=True):
        phase["name"] = name
    profile["phases"][2]["targets"] = [{"type": "volumetric", "operator": "gte", "value": 42.0}]
    slog = lever_shot()
    names = dict(zip(("preinfusion", "soak", "ramp"), names_in, strict=True))
    transitions = [
        t.model_copy(update={"phase_name": _logged(names[t.phase_name])}) for t in slog.transitions
    ]
    samples = [
        s.model_copy(
            update={"phase_name": _logged(names.get(s.phase_name or "", s.phase_name or ""))}
        )
        for s in slog.samples
    ]
    header = slog.header.model_copy(update={"transitions": transitions})
    return dataclasses.replace(slog, header=header, samples=samples), profile


@dataclass
class Exp:
    id: int
    position: int
    tier: str
    kind: str
    phase: str | None = None
    expression: Any = None
    warning_fault: str | None = None
    text: str = ""
    fault: str | None = None
    sentence: str = ""


def check(names_in: tuple[str, ...]) -> list[str]:
    for name in names_in:
        assert len(name.encode()) > 24, name
    slog, profile = build(names_in)
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000700", source="import", profile=profile
    )
    problems: list[str] = []

    import json

    metrics = json.loads(derived.shot.diagnostics_json or "{}").get("metrics", {})
    phases = json.loads(derived.shot.phases_json or "[]")
    warnings = shot_warnings(
        final_weight_g=derived.shot.final_weight_g,
        scale_connected=derived.shot.scale_connected,
        final_exit_reason=derived.shot.final_exit_reason or 0,
        duration_s=derived.shot.duration_ms / 1000,
        target_yield_g=42.0,
        phases=phases,
        metrics=metrics,
    )
    problems += [f"a false skipped: {w.detail}" for w in warnings if w.fault == "skipped"]

    data = ShotData.build(
        as_sample_dicts(slog),
        slog.transitions,
        profile_phases=_profile_names(profile),
        final_weight_g=slog.header.final_weight_g,
        target_yield_g=42.0,
        dose_g=18.0,
    )
    for name in names_in:
        result = evaluate(
            Expression.model_validate(
                {"channel": "cup_weight", "op": "at_end", "window": {"phase": name}}
            ),
            data,
        )
        if result.value is None:
            problems.append(f"a window over {name!r} reads: {result.why}")

    try:
        from gaggiclanker.domain.signature import build_checks
    except ImportError:
        build_checks = None
    if build_checks is not None:
        expectations = [
            Exp(1, 1, "critical", "reached", names_in[1], fault="skipped", sentence="reached"),
            Exp(
                2,
                2,
                "important",
                "expects_warning",
                names_in[2],
                warning_fault="fast flow",
                fault="fast flow",
                sentence="fast flow is expected",
            ),
        ]
        result = build_checks(
            warnings=warnings,
            expectations=expectations,  # type: ignore[arg-type]
            override=None,
            data=data,
            phases=phases,
            duration_s=derived.shot.duration_ms / 1000,
        )
        for check in result.checks:
            if check.status == "failed" or check.status == "unmeasured":
                problems.append(f"a false signature result: {check.badge}: {check.detail}")
        fast = [c for c in result.checks if c.kind == "warning" and c.status == "warning"]
        problems += [f"an expected warning left amber: {c.badge}" for c in fast]

    return problems


def _profile_names(profile: dict[str, Any]) -> list[str] | None:
    try:
        from gaggiclanker.domain.phase_names import raw_phase_names
    except ImportError:  # origin/dev: the stripped names are all there is
        return profile_phase_names(profile)
    return raw_phase_names(profile)


def main() -> int:
    # The third name of the first set is cut through the middle of a character, which is dropped.
    assert _logged(NAME_SETS[0][2]) == "x" + "É" * 11
    problems: list[str] = []
    for names in NAME_SETS:
        problems += check(names)
    for line in problems:
        print("BUG:", line)
    print("long phase names:", "matched" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
