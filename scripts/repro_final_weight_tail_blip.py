#!/usr/bin/env python
"""Reproduce: a scale blip after the drop to zero hides the yield, and the shot reads under target.

    uv run python scripts/repro_final_weight_tail_blip.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A shot whose scale drops from the yield to zero as it ends (the cup lifted, or
the scale resetting) keeps the reading before the drop as its final weight. That
rule only looked past readings of exactly 0. On a real shot the empty scale then
read 0.1 g in the very last sample: the firmware wrote that reading into the
header's ``finalWeight`` at file close, the last sample was positive, and the
shot was stored with a final weight of 0.1 g although the curve shows 31.3 g
right before the drop. The Curve check then said "Shot: under target, 0.3 % of
the 32 g target yield", and the last phase's cup at end read 0.1 g.

This script builds that shot the way the machine writes it (a 250 ms interval,
the weight climbing to 31.3 g, six samples of 0, one of 0.1 g, the header at
0.1 g), round-trips it through the binary codec and the ingest derivation, and
checks the final weight, the last phase's cup at end, and the shot's warnings
against a 32 g target. It also checks that an empty scale drifting a tenth of a
gram still gives no yield.
"""

from __future__ import annotations

import json
import sys

from gaggiclanker.domain.models import PhaseTransition, Sample, SlogHeader
from gaggiclanker.domain.slog import (
    FIELDS_MASK_ALL,
    HEADER_SIZE_V5,
    Slog,
    encode_slog,
    parse_slog,
    sample_size_for,
)
from gaggiclanker.domain.warnings import shot_warnings
from gaggiclanker.sync.derive import derive_shot

INTERVAL_MS = 250
TARGET_G = 32.0


def build(weights: list[float], *, header_weight: float | None) -> bytes:
    samples = [
        Sample(
            t=i * INTERVAL_MS,
            tt=92.0,
            ct=91.5,
            tp=6.0 if w > 1 else 0.0,
            cp=6.0 if w > 1 else 4.0,
            fl=1.8 if w > 1 else 0.0,
            tf=0.0,
            pf=1.9 if w > 1 else 0.0,
            vf=1.4 if w > 1 else 0.4,
            v=w,
            ev=w,
            pr=1.2,
            si=29,  # the scale is connected, as it was on the shot that showed this
            wp=w * 1.1,
        )
        for i, w in enumerate(weights)
    ]
    header = SlogHeader(
        version=7,
        sample_size=sample_size_for(7, FIELDS_MASK_ALL),
        header_size=HEADER_SIZE_V5,
        sample_interval=INTERVAL_MS,
        fields_mask=FIELDS_MASK_ALL,
        sample_count=len(samples),
        duration_ms=len(samples) * INTERVAL_MS,
        start_epoch=1_791_711_659,
        profile_id="repro",
        profile_name="Repro",
        final_weight_g=header_weight,
        final_exit_reason=1,
        transitions=[PhaseTransition(sample_index=0, phase_number=0, phase_name="Decline")],
    )
    return encode_slog(Slog(header=header, samples=samples))


def faults(final_weight_g: float | None) -> list[str]:
    return [
        w.badge
        for w in shot_warnings(
            final_weight_g=final_weight_g,
            scale_connected=True,
            final_exit_reason=1,
            duration_s=40.5,
            target_yield_g=TARGET_G,
            phases=[{"name": "Decline", "phase_number": 0}],
            metrics=None,
        )
    ]


def main() -> int:
    failures = 0

    # Thirty seconds of dripping up to 31.3 g, the drop to zero, then the empty scale's blip.
    ramp = [round(31.3 * i / 120, 1) for i in range(121)]
    raw = build([*ramp, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1], header_weight=0.1)
    slog = parse_slog(raw, "000013")
    derived = derive_shot(slog, raw, device_id="000013")
    got = derived.shot.final_weight_g
    print(f"blip shot: header={slog.header.final_weight_g!r} stored final_weight_g={got!r}")
    if got is None or abs(got - 31.3) > 0.05:
        print("FAIL: the yield before the drop was lost to the empty scale's 0.1 g")
        failures += 1

    phases = json.loads(derived.shot.phases_json or "[]")
    cup_end = phases[-1].get("metrics", {}).get("cup_weight_end_g") if phases else None
    print(f"blip shot: last phase cup_weight_end_g={cup_end!r}")
    if cup_end is None or abs(cup_end - 31.3) > 0.05:
        print("FAIL: the last phase ends at the empty scale, not at the yield")
        failures += 1

    said = faults(got)
    print(f"blip shot: curve check against {TARGET_G:g} g -> {said}")
    if any("under target" in badge for badge in said):
        print("FAIL: the curve check reads the shot as under target")
        failures += 1

    raw = build([0.0] * 120 + [0.1, 0.1, 0.0, 0.1], header_weight=0.1)
    got = derive_shot(parse_slog(raw), raw, device_id="000014").shot.final_weight_g
    print(f"drifting empty scale: stored final_weight_g={got!r}")
    if got is not None and got > 0.15:
        print("FAIL: an empty scale's drift became a yield")
        failures += 1

    print("OK" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
