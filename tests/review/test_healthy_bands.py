"""A review's retrieval queries come from the bands that stand out, not the healthy ones.

The label alone cannot say which is which (`LOW` is healthy for
`channeling_risk` and notable for `resistance_level`), so
`domain.diagnostics.BAND_READINGS` classifies every label per metric. These
tests hold that table to the band tables it describes, to the tokens a real
shot's review emits, and to what the queries do with it.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain import diagnostics
from gaggiclanker.domain.diagnostics import (
    BAND_READING_TABLES,
    BAND_READINGS,
    is_healthy_band,
    transform_shot,
)
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.knowledge.service import _UNREMARKABLE_BANDS, KnowledgeService, RetrievalContext
from gaggiclanker.review.context import build_review_input, signal_tokens
from gaggiclanker.sync.derive import derive_shot

SLOGS = sorted((Path(__file__).resolve().parents[1] / "fixtures" / "slog").glob("*.slog"))

#: Band tables whose labels never become a review token: the channeling
#: indicators are read only through the risk they add up to, the pressure and
#: flow volatility tables label the weight and per-phase blocks the review
#: does not read, and the ramp rate and taper smoothness are per-phase.
#: A new table is not on this list until someone has decided it is not a token.
NOT_TOKENS = {
    "_PRESSURE_CV_BANDS",
    "_PRESSURE_VOLATILITY_BANDS",
    "_FLOW_VOLATILITY_BANDS",
    "_FLOW_JITTER_BANDS",
    "_PRESSURE_JITTER_BANDS",
    "_FLOW_VS_TARGET_BANDS",
    "_FLOW_ACCELERATION_BANDS",
    "_PRESSURE_DROP_RATE_BANDS",
    "_TAPER_SMOOTHNESS_BANDS",
    "_RAMP_RATE_BANDS",
}


def _labels(table: list[tuple[float, str]]) -> set[str]:
    return {label for _, label in table}


def test_every_band_table_is_classified_or_declared_not_a_token() -> None:
    tables = {
        name: value
        for name, value in vars(diagnostics).items()
        if name.startswith("_") and name.endswith("_BANDS") and isinstance(value, list)
    }
    classified = {id(table) for table in BAND_READING_TABLES.values()}
    unaccounted = sorted(
        name
        for name, table in tables.items()
        if id(table) not in classified and name not in NOT_TOKENS
    )
    assert not unaccounted, (
        f"classify these bands in BAND_READINGS, or add to NOT_TOKENS: {unaccounted}"
    )
    assert NOT_TOKENS <= set(tables), "NOT_TOKENS names a table that no longer exists"


@pytest.mark.parametrize("metric", sorted(BAND_READING_TABLES))
def test_every_label_of_a_table_is_healthy_or_notable(metric: str) -> None:
    healthy, notable = BAND_READINGS[metric]
    assert not healthy & notable
    assert healthy | notable == _labels(BAND_READING_TABLES[metric]), (
        f"{metric}: a label was added or renamed in its band table; say whether it is healthy"
    )
    assert healthy, f"{metric} has no healthy reading"


def test_the_metrics_that_are_not_tables_are_pinned_to_their_source() -> None:
    assert set(BAND_READINGS) - set(BAND_READING_TABLES) == {"channeling_risk", "flow_trend"}
    # Every total the channeling score can reach, through the function itself.
    risks = {
        diagnostics._assess_channeling_risk(jitter, target, drop, accel, pjit)
        for jitter, target, drop, accel, pjit in itertools.product(
            (0.0, 0.05, 0.1),
            (None, 0.0, 0.35, 0.7),
            (0.0, -1.5, -3.0),
            (0.0, 0.05, 0.1),
            (0.0, 0.2),
        )
    }
    # And the block's other return: a steady-state window too short to assess.
    short = diagnostics._build_channeling(
        [1.0, 1.0], [1.0, 1.0], [{"cp": 1.0, "pf": 1.0}, {"cp": 1.0, "pf": 1.0}], 0.1
    )
    assert short["channeling_risk"] == "INSUFFICIENT_DATA"
    risks.add(short["channeling_risk"])
    healthy, notable = BAND_READINGS["channeling_risk"]
    assert (
        risks
        == healthy | notable
        == {
            "LOW",
            "MODERATE",
            "HIGH",
            "VERY_HIGH",
            "INSUFFICIENT_DATA",
        }
    )
    # The flow trend's three words are literals in the engine, not a table.
    assert BAND_READINGS["flow_trend"][0] | BAND_READINGS["flow_trend"][1] == {
        "DECLINING",
        "STABLE",
        "INCREASING",
    }


#: Tables whose order does not run from best to worst: resistance level and
#: saturation timing are best in the middle, and the slope tables run from a
#: rise through flat to steeper and steeper declines. For these the test holds
#: both ends of the table to be notable instead.
MIDDLE_OUT = {
    "resistance_level",
    "resistance_saturation",
    "resistance_erosion",
    "pressure_trend",
}


@pytest.mark.parametrize("metric", sorted(BAND_READING_TABLES))
def test_a_classification_is_sensible_not_only_complete(metric: str) -> None:
    """Healthy labels sit together in table order and the worst one is never healthy."""
    order = [label for _, label in BAND_READING_TABLES[metric]]
    healthy = BAND_READINGS[metric][0]
    flags = [label in healthy for label in order]
    first, last = flags.index(True), len(flags) - 1 - flags[::-1].index(True)
    assert all(flags[first : last + 1]), f"{metric}: healthy labels are not contiguous: {order}"
    if metric in MIDDLE_OUT:
        assert not flags[0], f"{metric}: the first label of the table cannot be healthy"
    else:
        assert flags[0], f"{metric}: the table runs best to worst, so its first label is healthy"
    assert not flags[-1], f"{metric}: the worst label ({order[-1]}) cannot be healthy"


def test_an_unclassified_metric_is_never_healthy() -> None:
    assert not is_healthy_band("a_metric_nobody_classified", "LOW")
    assert not is_healthy_band("a_metric_nobody_classified", "VERY_STABLE")
    assert not is_healthy_band("channeling_risk", "A_LABEL_NOBODY_CLASSIFIED")


def test_a_gradual_decline_is_normal_for_resistance_and_not_for_pressure() -> None:
    assert is_healthy_band("resistance_erosion", "GRADUAL_DECLINE")
    assert not is_healthy_band("pressure_trend", "GRADUAL_DECLINE")
    assert not is_healthy_band("resistance_erosion", "MODERATE_DECLINE")
    assert is_healthy_band("channeling_risk", "INSUFFICIENT_DATA")


def test_the_global_healthy_labels_are_never_notable_for_a_metric() -> None:
    for metric, (_, notable) in BAND_READINGS.items():
        assert not notable & _UNREMARKABLE_BANDS, metric


def _facts(path: Path, detail: str) -> Any:
    """What `signal_tokens` reads, from the engine at one detail level, through JSON as stored."""
    transformed = transform_shot(parse_slog(path.read_bytes()), detail)
    return SimpleNamespace(
        diagnostics=json.loads(json.dumps(transformed["diagnostics"])),
        summary=json.loads(json.dumps(transformed["summary"])),
        shot=SimpleNamespace(scale_connected=True, volume_g=transformed["final_weight_g"]),
    )


@pytest.mark.parametrize("detail", ["per_phase", "summary"])
@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
def test_every_band_token_a_real_shot_produces_is_classified(path: Path, detail: str) -> None:
    facts = _facts(path, detail)
    tokens = signal_tokens(facts, SimpleNamespace(style="unknown"))  # type: ignore[arg-type]
    banded = [t for t in tokens if t.partition(":")[2].isupper()]
    assert banded, "the shot must produce band tokens"
    for token in banded:
        metric, _, label = token.partition(":")
        assert metric in BAND_READINGS, f"{token}: an unclassified metric"
        assert label in BAND_READINGS[metric][0] | BAND_READINGS[metric][1], token


def _queries(*signals: str, style: str = "classic") -> list[str]:
    context = RetrievalContext(style=style, signals=signals)
    return KnowledgeService(None).queries_for(context)  # type: ignore[arg-type]


def test_a_healthy_reading_makes_no_query_and_the_same_word_elsewhere_does() -> None:
    # `LOW` and `MODERATE` are healthy for one metric and notable for another.
    assert _queries("channeling_risk:LOW") == ["classic shot profile"]
    assert "resistance level LOW" in _queries("resistance_level:LOW")
    assert _queries("resistance_level:MODERATE") == ["classic shot profile"]
    assert "temperature stability MODERATE" in _queries("temperature_stability:MODERATE")
    assert "resistance stability MODERATE" in _queries("resistance_stability:MODERATE")


@pytest.mark.parametrize(
    "token",
    [
        "channeling_risk:MODERATE",
        "channeling_risk:HIGH",
        "resistance_level:LOW",
        "resistance_level:HIGH",
        "resistance_erosion:MODERATE_DECLINE",
        "resistance_erosion:INCREASING",
        "pressure_trend:GRADUAL_DECLINE",
        "resistance_saturation:EARLY",
        "temperature_overshoot:SLIGHT",
        "temperature_undershoot:SIGNIFICANT",
        "pressure_adherence:FAIR",
        "pressure_overshoot:MINOR_OVERSHOOT",
    ],
)
def test_a_reading_that_stands_out_still_makes_its_query(token: str) -> None:
    metric, _, label = token.partition(":")
    assert f"{metric} {label}".replace("_", " ") in _queries(token)


@pytest.mark.parametrize(
    "token",
    [
        "channeling_risk:LOW",
        "temperature_overshoot:MINIMAL",
        "temperature_undershoot:MINIMAL",
        "temperature_stability:VERY_STABLE",
        "resistance_stability:VERY_STABLE",
        "resistance_saturation:GOOD_TIMING",
        "resistance_erosion:FLAT",
        "resistance_erosion:GRADUAL_DECLINE",
        "channeling_risk:INSUFFICIENT_DATA",
        "flow_trend:STABLE",
        "pressure_adherence:GOOD",
    ],
)
def test_a_healthy_reading_makes_no_query(token: str) -> None:
    metric, _, label = token.partition(":")
    assert is_healthy_band(metric, label) or label in _UNREMARKABLE_BANDS
    assert _queries(token) == ["classic shot profile"]


def test_the_order_of_the_remaining_queries_is_unchanged() -> None:
    signals = (
        "channeling_risk:LOW",
        "primary:flow_jitter",
        "resistance_level:LOW",
        "temperature_overshoot:MINIMAL",
        "temperature_stability:MODERATE",
    )
    assert _queries(*signals) == [
        "channeling flow jitter",
        "resistance level LOW",
        "temperature stability MODERATE",
        "classic shot profile",
    ]


@pytest.mark.parametrize("path", SLOGS, ids=lambda p: p.stem)
async def test_a_real_shot_s_review_retrieves_nothing_for_a_healthy_band(
    seeded: Database, path: Path
) -> None:
    raw = path.read_bytes()
    derived = derive_shot(parse_slog(raw), raw, device_id=path.stem)
    shot_id = await ShotsRepository(seeded).insert(derived.shot, derived.samples)
    review = await build_review_input(seeded, shot_id)

    healthy = {
        f"{metric} {label}".replace("_", " ")
        for token in review.signals
        for metric, _, label in [token.partition(":")]
        if is_healthy_band(metric, label)
    }
    assert healthy, "the shot must carry healthy readings for this test to bite"
    queries = KnowledgeService(seeded).queries_for(
        RetrievalContext(style=review.style, signals=tuple(review.signals))
    )
    assert not healthy & set(queries)
    assert not [e for e in review.excerpts if e["query"] in healthy]
    # The reading that does stand out is asked about.
    assert any(t.startswith("resistance_level:") for t in review.signals)
    assert any(q.startswith("resistance level ") for q in queries)
