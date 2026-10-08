"""Regenerate ``web/src/lib/profileCurve.golden.json``: the curve GaggiMate firmware v1.9.0 draws.

    PATH=/workspace/.tools/node/bin:$PATH \
    python3 tests/fixtures/profile_curve/generate.py external/gaggimate

``external/gaggimate`` is the read-only checkout of the firmware, detached at the tag below.
``harness.mjs`` cuts the firmware's own ``prepareData`` (with its easing functions) and
``buildPhaseRanges`` out of ``web/src/components/ExtendedProfileChart.jsx`` and evaluates that
text unchanged. The cases are every "pro" profile in ``tests/fixtures/profiles/`` and the
constructed ones below; each case carries its input, so the web test imports this one file and
nothing outside ``web/``. A point is ``[x, y, target]`` (target 1 or 0) at full precision; a NaN
``y`` (which the firmware produces after a phase of zero duration) is ``null``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TAG = "v1.9.0"
SOURCE = "web/src/components/ExtendedProfileChart.jsx"
GOLDEN = ROOT / "web/src/lib/profileCurve.golden.json"


def phase(
    name: str | None,
    duration: float | str,
    target: str | None,
    pressure: float | None = 0,
    flow: float | None = 0,
    transition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"duration": duration}
    if name is not None:
        out["name"] = name
    if transition is not None:
        out["transition"] = transition
    pump: dict[str, Any] = {}
    if target is not None:
        pump["target"] = target
    if pressure is not None:
        pump["pressure"] = pressure
    if flow is not None:
        pump["flow"] = flow
    out["pump"] = pump
    return out


def pro(*phases: dict[str, Any]) -> dict[str, Any]:
    return {"type": "pro", "phases": list(phases)}


def constructed() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for kind in ("instant", "linear", "ease-in", "ease-out", "ease-in-out", "bogus", None):
        transition = None if kind is None else {"type": kind, "duration": 3}
        cases[f"transition-{kind or 'absent'}"] = pro(
            phase("Fill", 4, "flow", 0, 4, transition),
            phase("Ramp", 6, "pressure", 9, 6, transition),
            phase("Back", 5, "flow", 2, 1, transition),
        )
    cases["carry-over-pressure"] = pro(
        phase("A", 3, "pressure", 6, 5, {"type": "linear", "duration": 1}),
        phase("B", 3, "pressure", -1, 4, {"type": "ease-in", "duration": 2}),
        phase("C", 3, "pressure", 9, 4, {"type": "ease-out", "duration": 2}),
    )
    cases["carry-over-flow"] = pro(
        phase("A", 3, "flow", 6, 5, {"type": "linear", "duration": 1}),
        phase("B", 3, "flow", 4, -1, {"type": "ease-in-out", "duration": 2}),
        phase("C", 3, "flow", 4, 2, {"type": "linear", "duration": 3}),
    )
    cases["carry-over-first-phase"] = pro(phase("A", 2, "pressure", -1, -1))
    cases["transition-duration-zero"] = pro(
        phase("A", 5, "pressure", 9, 0, {"type": "ease-in", "duration": 0}),
        phase("B", 4, "pressure", 3, 0, {"type": "ease-out", "duration": 0}),
    )
    cases["transition-longer-than-phase"] = pro(
        phase("A", 2, "pressure", 9, 0, {"type": "linear", "duration": 10}),
        phase("B", 2, "flow", 0, 7, {"type": "ease-in-out", "duration": 8}),
    )
    cases["zero-duration-phase"] = pro(
        phase("A", 2, "pressure", 6, 0, {"type": "linear", "duration": 1}),
        phase("Instant", 0, "pressure", 9, 0),
        phase("C", 2, "pressure", 4, 0, {"type": "linear", "duration": 1}),
    )
    cases["single-phase"] = pro(phase("Only", 7.5, "pressure", 9, 0, {"type": "linear"}))
    cases["empty-phase-list"] = pro()
    cases["fractional-durations"] = pro(
        phase("A", 0.25, "pressure", 5, 2, {"type": "ease-out", "duration": 0.2}),
        phase(None, 3.33, "flow", 4, 3.5, {"type": "linear", "duration": 3.33}),
        phase("", 1.05, "pressure", 8, 2),
    )
    cases["string-durations"] = pro(
        phase("A", "4.5", "pressure", 7, 0, {"type": "linear", "duration": "2"}),
        phase("B", "3", "flow", 0, 5, {"type": "ease-in", "duration": "3"}),
    )
    cases["missing-pump-values"] = pro(
        phase("A", 3, "pressure", None, None, {"type": "linear"}),
        phase("B", 3, None, 5, 5),
        phase("C", 3, "flow", None, 6, {"type": "linear"}),
    )
    cases["no-pump"] = pro({"name": "Bare", "duration": 2}, phase("Next", 2, "flow", 0, 3))
    return cases


def cases() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted((ROOT / "tests/fixtures/profiles").glob("*.json")):
        profile = json.loads(path.read_text())
        if profile.get("type") == "pro":
            out[f"fixture-{path.stem}"] = {"type": "pro", "phases": profile["phases"]}
    out.update(constructed())
    return out


def main() -> None:
    firmware = Path(sys.argv[1]).resolve()
    described = subprocess.run(  # noqa: S603
        ["git", "-C", str(firmware), "describe", "--tags", "--exact-match"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if described != TAG:
        sys.exit(f"{firmware} is at {described or 'no tag'}, not {TAG}: check out the pinned tag")
    inputs = cases()
    with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
        json.dump(inputs, handle)
        handle.flush()
        result = subprocess.run(  # noqa: S603
            ["node", str(HERE / "harness.mjs"), str(firmware / SOURCE), handle.name],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
    drawn = json.loads(result.stdout)
    golden = {
        "generated": {
            "firmware_tag": TAG,
            "command": "python3 tests/fixtures/profile_curve/generate.py external/gaggimate",
            "source": SOURCE,
            "note": "points are [x, y, target]; a null y is NaN",
        },
        "cases": {name: {"profile": inputs[name], **drawn[name]} for name in sorted(inputs)},
    }
    GOLDEN.write_text(json.dumps(golden, separators=(",", ":"), sort_keys=True) + "\n")
    print(f"{len(inputs)} cases -> {GOLDEN.relative_to(ROOT)} ({GOLDEN.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
