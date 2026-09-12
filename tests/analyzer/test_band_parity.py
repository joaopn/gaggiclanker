"""Every band the diagnostics engine can emit is explained in both places.

Three tables have to agree about which labels exist:

* `gaggiclanker/domain/diagnostics.py` — the thresholds, which *produce* them;
* `gaggiclanker/knowledge/seed/rules.yaml` — what the analyzer is told one means;
* `web/src/lib/shots.ts::BAND_MEANINGS` — what the shot page tells a reader.

They are not the same sentences and should not be: the rule carries the
interpretation the model needs ("flow deviation is the better grind signal,
because the PID masks pressure error"), the card carries one line a person can
read at a glance. What must not drift is *coverage*. A band added to the engine
and to one of the other two is a shot whose page says "NOTABLE_DEVIATION" and
whose analysis has never heard of it, which is exactly the kind of gap nobody
notices until they are comparing the two.

The TypeScript is read with a regex rather than executed. Standing up Node to
parse one object literal would make a Python test depend on the front-end
toolchain; the table is a flat map of string to string and the regex that reads
it is asserted to have found a plausible number of entries, so a format change
that broke the parse fails here rather than passing vacuously.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gaggiclanker.domain import diagnostics
from gaggiclanker.knowledge.rules import load_seed_rules

SHOTS_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "shots.ts"

#: Which threshold table each annotation the *summary* diagnostics emit is
#: banded by. `compute_summary_diagnostics` is the authority; this mirrors it,
#: and a metric that stopped using its table would fail the label assertions
#: below rather than drift quietly.
_METRIC_BANDS: dict[str, list[tuple[float, str]]] = {
    "resistance_level": diagnostics._RESISTANCE_LEVEL_BANDS,
    "resistance_erosion": diagnostics._RESISTANCE_SLOPE_BANDS,
    "pressure_adherence": diagnostics._PROFILE_ADHERENCE_BANDS,
    "pressure_overshoot": diagnostics._PRESSURE_OVERSHOOT_BANDS,
    "temperature_stability": diagnostics._TEMP_STABILITY_BANDS,
    "flow_adherence": diagnostics._PROFILE_ADHERENCE_BANDS,
    "flow_overshoot": diagnostics._FLOW_DEVIATION_BANDS,
}

#: Channeling risk is scored rather than banded by a threshold table
#: (`_assess_channeling_risk`), so its labels are listed rather than read.
_CHANNELING_RISKS = ("LOW", "MODERATE", "HIGH", "VERY_HIGH", "INSUFFICIENT_DATA")

#: The two naming schemes, reconciled.
#:
#: The summary block's annotations are named for the whole shot
#: (`resistance_level`), while the shot page's cards address a sub-block and
#: name the field inside it (`level`, on the resistance card). Neither is wrong
#: and neither is going to change, so the mapping is written down here — which
#: is also the only place that would notice if one of them did.
_UI_METRIC: dict[str, str] = {
    "resistance_level": "level",
    "resistance_erosion": "erosion",
    "channeling_risk": "channeling",
}


def _expected_keys() -> set[str]:
    keys = {f"{metric}:{label}" for metric, bands in _METRIC_BANDS.items() for _, label in bands}
    keys |= {f"channeling_risk:{risk}" for risk in _CHANNELING_RISKS}
    return keys


def _typescript_band_meanings() -> dict[str, str]:
    """The `BAND_MEANINGS` object, read as a flat map."""
    source = SHOTS_TS.read_text(encoding="utf-8")
    start = source.index("const BAND_MEANINGS")
    body = source[start : source.index("\n};", start)]
    return dict(re.findall(r'^\s*"?([A-Za-z_:/ ]+?)"?:\s*"(.*?)",\s*$', body, re.MULTILINE))


def _explained(table: dict[str, str], key: str) -> bool:
    """The UI's own lookup order: `metric:LABEL` first, bare label second."""
    metric, _, label = key.partition(":")
    ui = _UI_METRIC.get(metric, metric)
    return f"{ui}:{label}" in table or label in table


def test_the_typescript_table_actually_parsed() -> None:
    """Guards the regex: a format change must fail loudly, not vacuously pass."""
    table = _typescript_band_meanings()

    assert len(table) > 40, f"only {len(table)} entries — the parse is probably broken"
    assert table["EXCELLENT"].startswith("RMSE under 0.3")


@pytest.mark.parametrize("key", sorted(_expected_keys()))
def test_every_band_the_engine_emits_has_a_rule(key: str) -> None:
    rules = {rule.key for rule in load_seed_rules() if rule.category == "band_meanings"}

    assert key in rules, f"{key} is banded by diagnostics.py but no rule explains it"


@pytest.mark.parametrize("key", sorted(_expected_keys()))
def test_every_band_the_engine_emits_is_explained_on_the_shot_page(key: str) -> None:
    assert _explained(_typescript_band_meanings(), key), (
        f"{key} is banded by diagnostics.py but web/src/lib/shots.ts cannot explain it"
    )


def test_no_rule_explains_a_band_that_cannot_happen() -> None:
    """The other direction: a rule for a label the engine no longer emits.

    It would never be selected — the signal token that matches it is built from
    the annotations — so it is dead weight in every prompt that mentions its
    category.
    """
    rules = {rule.key for rule in load_seed_rules() if rule.category == "band_meanings"}

    assert rules - _expected_keys() == set()
