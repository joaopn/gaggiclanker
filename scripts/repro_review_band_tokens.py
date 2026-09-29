#!/usr/bin/env python
"""Reproduce: a review never selects the resistance, temperature or channeling band rules.

    uv run python scripts/repro_review_band_tokens.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A review picks knowledge rules by signal tokens (`review.context.signal_tokens`),
and the seed rules key on section-qualified tokens: `resistance_level:LOW`,
`resistance_erosion:FLAT`, `temperature_stability:MODERATE`,
`channeling_risk:LOW`. Those are the keys of the *summary* diagnostics'
annotations. But ingest (the sync and the importer alike) stores the *full*
diagnostics, where the annotations are nested per section under short keys
(`resistance.annotations.level`, `temperature.annotations.stability`), and the
tokens came out as `level:LOW`, `erosion:FLAT`, `stability:MODERATE` — which no
rule matches, and `stability` is ambiguous between two sections. The review
tests passed only because their fixture hand-builds the summary shape.

The check derives each real fixture shot the way ingest does, builds its tokens
from both shapes, and requires every token the summary shape produces that some
seed rule keys on to be present in the tokens of the shape the archive stores.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from gaggiclanker.domain.diagnostics import transform_shot
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.knowledge.rules import load_seed_rules
from gaggiclanker.review.context import signal_tokens

ROOT = Path(__file__).resolve().parents[1]


def tokens_at(slog, detail: str) -> set[str]:
    transformed = transform_shot(slog, detail)
    # Through JSON, as the column stores it.
    blob = json.loads(
        json.dumps({"summary": transformed["summary"], "diagnostics": transformed["diagnostics"]})
    )
    # signal_tokens reads the diagnostics, the summary, the scale flag and the weight only.
    facts = SimpleNamespace(
        diagnostics=blob["diagnostics"],
        summary=blob["summary"],
        shot=SimpleNamespace(scale_connected=True, volume_g=transformed["final_weight_g"]),
    )
    return set(signal_tokens(facts, SimpleNamespace(style="unknown")))  # type: ignore[arg-type]


def main() -> int:
    used = {token for rule in load_seed_rules() for token in (rule.applies.get("signal") or [])}
    failed = False
    for path in sorted((ROOT / "tests" / "fixtures" / "slog").glob("*.slog")):
        slog = parse_slog(path.read_bytes())
        wanted = tokens_at(slog, "summary") & used
        got = tokens_at(slog, "per_phase")
        missing = sorted(wanted - got)
        band_rules = sorted(
            t
            for t in wanted
            if t.split(":")[0]
            in {
                "resistance_level",
                "resistance_erosion",
                "temperature_stability",
                "channeling_risk",
            }
        )
        print(f"{path.name}: rule tokens from the summary shape: {sorted(wanted)}")
        if missing or not band_rules:
            failed = True
            print(f"  MISSING from the stored (full) shape's tokens: {missing}")
    if failed:
        print("FAIL: real shots do not produce the section-qualified tokens the rules key on")
        return 1
    print("ok: the stored shape produces every rule token the summary shape does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
