"""The per-phase numbers are expressions of the metric language, and nothing moved.

Every number the derivation stores for a phase is the value of one expression
read over that phase, so there is one code path for it and for anyone who asks
the language. The golden next to ``generate.py`` was produced from the tree from
before the numbers were computed this way: every stored number, and the order it is
stored in, is identical now.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.metric_language import (
    Expression,
    ShotData,
    canonical_form,
    evaluate,
    per_phase_method,
)
from gaggiclanker.domain.phase_metrics import profile_phase_names, stored_expressions
from gaggiclanker.domain.slog import Slog
from gaggiclanker.shotinfo.methods import METHODS
from gaggiclanker.sync.derive import derive_shot
from tests.domain.test_phase_metrics import SHOTS
from tests.lever_shot import LEVER_PROFILE, lever_shot, without_pressure, without_scale

GENERATE = Path(__file__).resolve().parents[1] / "fixtures" / "phase_metrics" / "generate.py"
GOLDEN = GENERATE.with_name("golden.json")


def _current() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("phase_metrics_generate", GENERATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["phase_metrics_generate"] = module
    spec.loader.exec_module(module)
    result: dict[str, Any] = module.stored_metrics()
    return result


GOLDEN_NUMBERS: dict[str, Any] = json.loads(GOLDEN.read_text())


@pytest.fixture(scope="module")
def current() -> dict[str, Any]:
    return _current()


def test_the_same_shots_are_covered(current: dict[str, Any]) -> None:
    assert sorted(current) == sorted(GOLDEN_NUMBERS)
    assert len(GOLDEN_NUMBERS) >= 40


@pytest.mark.parametrize("case", sorted(GOLDEN_NUMBERS))
def test_every_stored_per_phase_number_is_byte_identical_to_the_golden(
    current: dict[str, Any], case: str
) -> None:
    # Compared as text: the order a phase's numbers are stored in is part of the bytes.
    assert json.dumps(current[case]) == json.dumps(GOLDEN_NUMBERS[case])


# ── the stored numbers are the language's ────────────────────────────

#: The catalogue item that reads each stored key.
ITEM_OF = {
    "pressure_peak_bar": "phase_pressure_peak",
    "pressure_end_bar": "phase_pressure_end",
    "temperature_min_c": "phase_temperature_min",
    "temperature_target_c": "phase_temperature_target",
    "puck_flow_peak_ml_s": "phase_flow_peak",
    "scale_flow_mean_g_s": "phase_scale_flow",
    "scale_flow_peak_g_s": "phase_scale_flow_peak",
    "cup_weight_end_g": "phase_cup_end",
    "cup_weight_gained_g": "phase_cup_gained",
    "water_pumped_ml": "phase_water",
}

VARIANTS: list[tuple[str, Slog, dict[str, Any] | None, bool | None]] = [
    *[(f"{name}", slog, LEVER_PROFILE if name == "lever" else None, None) for name, slog in SHOTS],
    ("lever-noscale", without_scale(lever_shot()), LEVER_PROFILE, None),
    ("lever-nopressure", without_pressure(lever_shot()), LEVER_PROFILE, False),
]


@pytest.mark.parametrize(
    ("name", "slog", "profile", "has_pressure"), VARIANTS, ids=[v[0] for v in VARIANTS]
)
def test_each_stored_number_is_the_evaluators_value_for_its_expression(
    name: str, slog: Slog, profile: dict[str, Any] | None, has_pressure: bool | None
) -> None:
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000001", profile=profile, has_pressure=has_pressure
    )
    stored = {
        p["phase_number"]: p.get("metrics", {})
        for p in json.loads(derived.shot.phases_json or "[]")
    }
    data = ShotData.build(
        as_sample_dicts(slog),
        slog.transitions,
        profile_phases=profile_phase_names(profile),
        has_pressure=derived.shot.diagnostics_json is not None
        and json.loads(derived.shot.diagnostics_json)["has_pressure"],
        scale_connected=derived.shot.scale_connected,
        final_weight_g=slog.volume_g,
    )
    checked = 0
    for transition in slog.transitions:
        numbers = stored[transition.phase_number]
        for key, expression in stored_expressions().items():
            asked = Expression.model_validate(
                {
                    "channel": expression.channel,
                    "op": expression.op,
                    "window": {"phase_number": transition.phase_number},
                }
            )
            got = evaluate(asked, data).value
            if key.startswith("puck_flow") and not data.has_pressure:
                # The one place the stored number and the language differ: a board with no
                # pressure sensor has always been stored with a puck flow of zero, and the
                # language says it was not recorded.
                assert evaluate(asked, data).absent == "not_recorded"
                assert numbers[key] == 0.0
                continue
            assert numbers.get(key) == got, (name, transition.phase_number, key)
            checked += 1
    assert checked


def test_every_stored_number_is_an_expression_and_the_catalogue_names_it() -> None:
    expressions = stored_expressions()
    assert set(ITEM_OF) <= set(expressions)
    assert set(expressions) - set(ITEM_OF) == {"puck_flow_mean_ml_s"}
    for key, item in ITEM_OF.items():
        expression = expressions[key]
        assert METHODS[item] == per_phase_method(expression.channel, expression.op)
        # The id is the expression's canonical form with its window read as each phase.
        as_written = json.loads(METHODS[item])
        again = json.loads(canonical_form(expression))
        assert as_written.pop("window") == "each_phase" and again.pop("window")
        assert as_written == again


def test_two_catalogue_items_never_share_an_expression() -> None:
    ids = [METHODS[item] for item in ITEM_OF.values()]
    assert len(set(ids)) == len(ids)
