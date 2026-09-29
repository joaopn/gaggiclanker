"""A review of a synced or imported shot selects the band rules.

`tests/review/conftest.py` hand-builds the *summary* diagnostics shape, which
already names its annotations by section (`resistance_level`). Ingest stores
the *full* shape instead, where they are nested per section under short keys,
and the tokens used to come out as `level:LOW` and an ambiguous `stability:`.
Nothing matched them, so the resistance, temperature-stability and channeling
band rules were never selected for a real shot. These tests derive real
fixture recordings the way ingest does and read the review's input for them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.diagnostics import transform_shot
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.knowledge.rules import load_seed_rules
from gaggiclanker.review.context import build_review_input, signal_tokens
from gaggiclanker.review.style import StyleVerdict
from gaggiclanker.shotinfo import load_shots
from gaggiclanker.sync.derive import derive_shot

SLOGS = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "slog").glob("*.slog"))

#: The metrics whose rules key on the summary shape's section-qualified names.
BAND_METRICS = (
    "resistance_level",
    "resistance_erosion",
    "temperature_stability",
    "channeling_risk",
)


def _rule_tokens() -> set[str]:
    """Every token some seed rule keys on."""
    return {token for rule in load_seed_rules() for token in rule.applies.get("signal") or []}


async def _ingest(db: Database, path: Path, *, has_pressure: bool | None = None) -> int:
    raw = path.read_bytes()
    derived = derive_shot(parse_slog(raw), raw, device_id=path.stem, has_pressure=has_pressure)
    assert derived.diagnostics_error is None
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


def _summary_tokens(path: Path) -> set[str]:
    """What the summary shape of the same recording names, straight from the engine."""
    diagnostics = transform_shot(parse_slog(path.read_bytes()), "summary")["diagnostics"]
    return {
        f"{metric}:{label}"
        for metric, label in json.loads(json.dumps(diagnostics))["annotations"].items()
    }


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
async def test_a_derived_shot_produces_the_summary_shape_s_band_tokens(
    seeded: Database, path: Path
) -> None:
    shot_id = await _ingest(seeded, path)
    (facts,) = await load_shots(seeded, [shot_id])
    assert facts.full, "ingest stores the full diagnostics; this test must not read the flat shape"

    listed = signal_tokens(facts, StyleVerdict(style="unknown", tier="none", evidence=[]))
    tokens = set(listed)
    # Every token the summary shape produces that any rule matches, not only
    # the four band metrics: a section prefix wrongly put on the profile
    # compliance keys would lose `pressure_adherence:` and `flow_adherence:`.
    expected = _summary_tokens(path) & _rule_tokens()
    assert {t.split(":")[0] for t in expected} >= set(BAND_METRICS)
    assert expected <= tokens
    # Nothing is left under a short, ambiguous key or as prose.
    assert not [
        t for t in tokens if t.startswith(("level:", "erosion:", "stability:", "guidance:"))
    ]
    assert listed == sorted(tokens)


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
async def test_the_band_rules_are_selected_for_a_derived_shot(seeded: Database, path: Path) -> None:
    shot_id = await _ingest(seeded, path)
    review = await build_review_input(seeded, shot_id)

    by_key = {rule.key: rule for rule in load_seed_rules()}
    band_keys = {
        key
        for key, rule in by_key.items()
        if any(token.startswith(BAND_METRICS) for token in rule.applies.get("signal") or [])
    }
    selected = set(review.rule_keys)
    tokens = set(review.signals)
    wanted = {key for key in band_keys if set(by_key[key].applies.get("signal") or []) & tokens}
    assert wanted, "the shot's tokens must match at least one band rule"
    assert wanted <= selected
    for metric in ("resistance_level", "resistance_erosion", "temperature_stability"):
        assert any(t.startswith(metric + ":") for t in tokens)
        assert any(
            t.startswith(metric + ":")
            for key in selected & band_keys
            for t in by_key[key].applies.get("signal") or []
        ), metric


@pytest.mark.parametrize("path", SLOGS[:1], ids=lambda p: p.stem)
async def test_a_shot_without_a_pressure_sensor_gets_no_prose_token(
    seeded: Database, path: Path
) -> None:
    """The engine's "No pressure sensor" sentence is an annotation, not a band."""
    shot_id = await _ingest(seeded, path, has_pressure=False)
    (facts,) = await load_shots(seeded, [shot_id])
    diagnostics = facts.diagnostics
    assert "note" in diagnostics["extraction"]["annotations"], "the fixture must carry the note"

    tokens = signal_tokens(facts, StyleVerdict(style="unknown", tier="none", evidence=[]))
    assert not [t for t in tokens if t.startswith(("note:", "guidance:"))]
    assert all(len(t) < 60 for t in tokens)


class _Shot:
    scale_connected = True
    volume_g = 36.0


class _Facts:
    """The four things `signal_tokens` reads, with a hand-built full-shape block."""

    def __init__(self, channeling: dict[str, object]) -> None:
        self.shot = _Shot()
        self.summary: dict[str, object] = {}
        self.diagnostics = {"channeling": channeling}


def _tokens_for(primary: str) -> list[str]:
    facts = _Facts(
        {"channeling_risk": "HIGH", "annotations": {"primary_signal": primary, "guidance": "x"}}
    )
    return signal_tokens(facts, StyleVerdict(style="unknown", tier="none", evidence=[]))  # type: ignore[arg-type]


def test_the_channeling_indicators_that_fired_become_primary_tokens() -> None:
    tokens = _tokens_for("flow_jitter,pressure_cliff")
    assert {"primary:flow_jitter", "primary:pressure_cliff", "channeling_risk:HIGH"} <= set(tokens)
    assert not [t for t in tokens if t.startswith("guidance:")]


def test_no_indicator_fired_means_no_primary_token() -> None:
    tokens = _tokens_for("none")
    assert not [t for t in tokens if t.startswith("primary:")]
    assert "channeling_risk:HIGH" in tokens
