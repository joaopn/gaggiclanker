#!/usr/bin/env python
"""Reproduce: a shot whose scale reads zero in its last samples has no yield.

    uv run python scripts/repro_final_weight_dropout.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The firmware writes the header's ``finalWeight`` from the scale reading at the
moment it closes the file, and records a reading at or below zero as 0 ("no
weight"). Some shots end with the scale dropping straight from the real yield to
zero for the last few samples (the cup lifted, or the scale resetting as the shot
ends): the header then says 0, the last sample says 0, and the shot was stored
with no final weight although the curve shows ~32 g right before the drop.

This script builds such a shot the way the machine writes it (a 250 ms
interval, the weight climbing to 31.8 g, then three samples of 0 with the scale
still connected, and a header weight of 0), round-trips it through the binary
codec and the ingest derivation, and checks the stored final weight is the
reading before the drop. It also checks that a shot with no scale at all still
has no final weight.
"""

from __future__ import annotations

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
from gaggiclanker.sync.derive import derive_shot

INTERVAL_MS = 250


def build(weights: list[float], *, scale: bool) -> bytes:
    samples = [
        Sample(
            t=i * INTERVAL_MS,
            tt=86.5,
            ct=86.4,
            tp=9.0 if w else 0.0,
            cp=8.8 if w else 3.0,
            fl=2.0 if w else 0.0,
            tf=0.0,
            pf=1.8 if w else 0.0,
            vf=1.5 if w else 0.0,
            v=w,
            ev=w,
            pr=1.46,
            # 0x04: the scale is connected, as it was in the shot that showed this.
            si=29 if scale else 25,
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
        start_epoch=1_758_000_000,
        profile_id="repro",
        profile_name="Repro",
        final_weight_g=None,  # the firmware wrote 0: the scale read 0 at close
        transitions=[PhaseTransition(sample_index=0, phase_number=0, phase_name="brew")],
    )
    return encode_slog(Slog(header=header, samples=samples))


def main() -> int:
    failures = 0

    # Thirty seconds of dripping up to 31.8 g, then the drop to zero at the end.
    ramp = [round(31.8 * i / 120, 1) for i in range(121)]
    raw = build([*ramp, 0.0, 0.0, 0.0], scale=True)
    slog = parse_slog(raw, "000900")
    derived = derive_shot(slog, raw, device_id="000900")
    got = derived.shot.final_weight_g
    print(f"dropout shot: slog.volume_g={slog.volume_g!r} stored final_weight_g={got!r}")
    if got is None or abs(got - 31.8) > 0.05:
        print("FAIL: the yield before the drop to zero was lost")
        failures += 1

    raw = build([0.0] * 124, scale=False)
    got = derive_shot(parse_slog(raw), raw, device_id="000901").shot.final_weight_g
    print(f"no-scale shot: stored final_weight_g={got!r}")
    if got is not None:
        print("FAIL: a shot without a scale reading gained a weight")
        failures += 1

    print("OK" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
