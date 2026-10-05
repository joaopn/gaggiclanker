"""The computations that were kept (resistance, adherence, the firmware's values) did not move.

The golden next to ``generate.py`` was produced by running that script against
the tree from before the per-phase metrics arrived and the score and the bands
went. Every number the kept computations give for every fixture shot, with and
without a profile and as a machine with no pressure sensor, must be identical
now: a change to one of them is a change to numbers people compared shots by,
not part of this work.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

GENERATE = Path(__file__).resolve().parents[1] / "fixtures" / "kept_numbers" / "generate.py"
GOLDEN = GENERATE.with_name("golden.json")


def _current() -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("kept_numbers_generate", GENERATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["kept_numbers_generate"] = module
    spec.loader.exec_module(module)
    result: dict[str, object] = module.kept_numbers()
    return result


GOLDEN_NUMBERS: dict[str, object] = json.loads(GOLDEN.read_text())


@pytest.fixture(scope="module")
def current() -> dict[str, object]:
    return _current()


def test_the_same_shots_are_covered(current: dict[str, object]) -> None:
    assert sorted(current) == sorted(GOLDEN_NUMBERS)
    assert len(GOLDEN_NUMBERS) >= 30


@pytest.mark.parametrize("case", sorted(GOLDEN_NUMBERS))
def test_kept_numbers_are_unchanged(current: dict[str, object], case: str) -> None:
    assert current[case] == GOLDEN_NUMBERS[case]
