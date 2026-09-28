"""`list_set_shots`, the shot search: every filter, the order, the cap, the scope.

Over the analyzer's fixture Set (six shots, summary-level diagnostics, five of
them judged), whose numbers are written out in `tests/analyzer/conftest.py`:

    shot  duration  weight  score  rating  balance   channeling  started
    0     21.0 s    38.0 g  7.4    2       sour      MODERATE    03-02
    1     23.0 s    37.2 g  8.1    3       sour      LOW         03-02
    2     25.0 s    36.4 g  8.6    3       sour      LOW         03-02
    3     26.5 s    36.0 g  8.8    4       balanced  LOW         03-03
    4     24.0 s    36.8 g  8.2    3       sour      LOW         03-03
    5     24.0 s    37.5 g  8.3    3       sour      LOW         03-03

Every shot's dose in is 18 g and its dose out its weight, so the ratio is the
weight over 18. First drip is 8.2 s and peak pressure 9.2 bar on all of them;
average brew flow is on none (it lives in the full diagnostics only).
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.domain import diagnostics as engine
from gaggiclanker.shotinfo.catalogue import ITEMS, default_tiers
from gaggiclanker.tools import builtin
from gaggiclanker.tools.registry import ToolContext, registry
from tests.analyzer.conftest import Fixture


async def search(ctx: ToolContext, **arguments: Any) -> dict[str, Any]:
    outcome = await registry.dispatch(ctx, "list_set_shots", arguments)
    assert outcome.ok, outcome.data
    return outcome.data


def ids(data: dict[str, Any]) -> list[int]:
    return [shot["shot_id"] for shot in data["shots"]]


def at(archive: Fixture, *indices: int) -> set[int]:
    return {archive.shots[index] for index in indices}


async def test_no_filter_is_the_newest_first_in_base(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await search(set_ctx)

    assert ids(data) == list(reversed(archive.shots))
    assert data["count"] == 6
    assert data["truncated"] is False
    newest = data["shots"][0]["text"]
    assert newest.startswith(f"shot {archive.shots[-1]}\n")
    assert "Rating: 3/5" in newest
    assert "[Curve]" not in newest and "Score confidence" not in newest


async def test_a_curve_in_base_reaches_the_results_and_nothing_else_reads_samples(
    set_ctx: ToolContext, archive: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression `scripts/repro_base_curve_never_loaded.py` reproduces, for the search.

    The search loaded its shots without samples, so a curve channel a person
    had moved into base came out empty. It now reads the results' samples, in
    one query, exactly when base carries a curve.
    """
    reads: list[list[int]] = []
    real = ShotsRepository.samples_for

    async def counted(self: ShotsRepository, shot_ids: Any) -> Any:
        reads.append(list(shot_ids))
        return await real(self, shot_ids)

    monkeypatch.setattr(ShotsRepository, "samples_for", counted)
    newest = archive.shots[-1]  # the fixture's one shot with samples

    before = await search(set_ctx)
    assert reads == [], "no curve in base, no samples read"
    await ShotInfoTiersRepository(archive.db).set_tier(
        ShotInfoTierWrite(item_key="curve_pressure", tier="base")
    )
    after = await search(set_ctx)

    text = {hit["shot_id"]: hit["text"] for hit in after["shots"]}
    assert "[Curve]" not in {hit["shot_id"]: hit["text"] for hit in before["shots"]}[newest]
    table = text[newest].split("[Curve]\n", 1)[1].splitlines()
    assert table[1] == "t (s),pressure (bar)"
    assert len(reads) == 1 and sorted(reads[0]) == sorted(ids(after)), "one query, the results"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"shot_time": {"min": 24}}, (2, 3, 4, 5)),
        ({"shot_time": {"min": 23, "max": 24}}, (1, 4, 5)),
        ({"yield_g": {"max": 36.5}}, (2, 3)),
        ({"execution_score": {"min": 8.5}}, (2, 3)),
        ({"rating": {"min": 4}}, (3,)),
        ({"rating": {"max": 2}}, (0,)),
        ({"dose_in": {"min": 18, "max": 18}}, (0, 1, 2, 3, 4, 5)),
        ({"dose_out": {"min": 37.5}}, (0, 5)),
        ({"ratio": {"min": 2.08}}, (0, 5)),
        ({"first_drip": {"max": 9}}, (0, 1, 2, 3, 4, 5)),
        ({"first_drip": {"min": 9}}, ()),
        ({"peak_pressure": {"min": 9.2, "max": 9.2}}, (0, 1, 2, 3, 4, 5)),
        # Recorded on none of them: absent never matches a range, even an open one.
        ({"brew_flow": {"min": 0}}, ()),
        ({"balance": "balanced"}, (3,)),
        ({"label": "improve"}, (5,)),
        ({"channeling_risk": "MODERATE"}, (0,)),
        ({"resistance_level": "MODERATE"}, (0, 1, 2, 3, 4, 5)),
        ({"resistance_level": "HIGH"}, ()),
        ({"pressure_adherence": "EXCELLENT"}, (0, 1, 2, 3, 4, 5)),
        ({"flow_adherence": "POOR"}, ()),
        ({"since": "2026-03-03"}, (3, 4, 5)),
        ({"until": "2026-03-02"}, (0, 1, 2)),
        ({"since": "2026-03-02", "until": "2026-03-02"}, (0, 1, 2)),
        ({"version_no": 1}, (0, 1, 2, 3, 4, 5)),
        ({"version_no": 2}, ()),
        ({"balance": "sour", "shot_time": {"min": 24}}, (2, 4, 5)),
    ],
)
async def test_each_filter(
    set_ctx: ToolContext, archive: Fixture, arguments: dict[str, Any], expected: tuple[int, ...]
) -> None:
    data = await search(set_ctx, **arguments)

    assert set(ids(data)) == at(archive, *expected)
    assert data["count"] == len(expected)


async def test_it_sorts_on_a_number_and_breaks_ties_by_shot_id(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    shots = archive.shots

    rising = await search(set_ctx, order_by="shot_time", descending=False)
    falling = await search(set_ctx, order_by="shot_time")

    # Shots 4 and 5 both ran 24.0 s: the id decides, in the same direction.
    assert ids(rising) == [shots[0], shots[1], shots[4], shots[5], shots[2], shots[3]]
    assert ids(falling) == [shots[3], shots[2], shots[5], shots[4], shots[1], shots[0]]


async def test_a_shot_with_no_value_for_the_sort_key_comes_last_either_way(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    await JudgementsRepository(archive.db).upsert(archive.shots[2], JudgementWrite())

    rising = await search(set_ctx, order_by="rating", descending=False)
    falling = await search(set_ctx, order_by="rating")

    assert ids(rising)[-1] == archive.shots[2]
    assert ids(falling)[-1] == archive.shots[2]
    assert ids(falling)[0] == archive.shots[3], "the only 4-star shot"


async def test_the_limit_cuts_the_list_and_says_so(set_ctx: ToolContext) -> None:
    data = await search(set_ctx, limit=2)

    assert data["count"] == 2
    assert data["truncated"] is True
    exact = await search(set_ctx, balance="balanced", limit=1)
    assert exact["truncated"] is False, "exactly the limit is not cut short"


async def test_at_most_ten_come_back(set_ctx: ToolContext, archive: Fixture) -> None:
    sets = SetsRepository(archive.db)
    for number in range(6):
        extra = await ShotsRepository(archive.db).insert(
            ShotInsert(
                device_id=f"0009{number:02d}",
                raw_slog=b"extra",
                started_at=f"2026-03-04T08:{number:02d}:00.000Z",
                duration_ms=25_000,
            )
        )
        assert await sets.assign_shot(extra, archive.version_id)

    data = await search(set_ctx)

    assert data["count"] == 10
    assert data["truncated"] is True
    refused = await registry.dispatch(set_ctx, "list_set_shots", {"limit": 11})
    assert refused.status == "error"


async def test_it_never_returns_a_shot_of_another_set(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    sets = SetsRepository(archive.db)
    other = await sets.create(
        SetWrite(name="Another bag", bean_id=archive.bean_id),
        SetVersionWrite(grind_setting="20"),
        automatch=False,
    )
    version = await sets.current_version(other.id)
    assert version is not None
    moved = archive.shots[3]
    assert await sets.assign_shot(moved, version.id)

    everything = await search(set_ctx)
    balanced = await search(set_ctx, balance="balanced")

    assert moved not in ids(everything)
    assert ids(balanced) == []


async def test_a_version_filter_finds_the_shots_of_that_version(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    sets = SetsRepository(archive.db)
    newer = await sets.add_version(archive.set_id, SetVersionPatch(grind_setting="21"))
    assert newer is not None
    assert await sets.assign_shot(archive.shots[0], newer.id)

    data = await search(set_ctx, version_no=newer.version_no)

    assert ids(data) == [archive.shots[0]]
    assert f"Set version: v{newer.version_no} of Set {archive.set_id}" in data["shots"][0]["text"]


async def test_a_shot_that_does_not_count_is_found_and_says_so(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    await JudgementsRepository(archive.db).upsert(
        archive.shots[0], JudgementWrite(decision="discard")
    )

    data = await search(set_ctx, label="discard")

    assert ids(data) == [archive.shots[0]]
    assert "Counted: not counted: discarded" in data["shots"][0]["text"]


async def test_a_general_conversation_is_not_offered_the_search(ctx: ToolContext) -> None:
    outcome = await registry.dispatch(ctx, "list_set_shots", {})

    assert outcome.status == "refused"


def test_the_band_arguments_are_the_engine_s_labels() -> None:
    """Typed out for the schema; pinned here to the tables they come from."""
    assert get_args(builtin.ResistanceLevel) == tuple(
        label for _, label in engine._RESISTANCE_LEVEL_BANDS
    )
    assert get_args(builtin.Adherence) == tuple(
        label for _, label in engine._PROFILE_ADHERENCE_BANDS
    )
    risks = {
        engine._assess_channeling_risk(jitter, None, drop, 0.0, pressure)
        for jitter in (0.0, 0.2)
        for drop in (0.0, -4.0)
        for pressure in (0.0, 0.3)
    } | {engine._assess_channeling_risk(0.2, 1.0, -4.0, 0.2, 0.0), "INSUFFICIENT_DATA"}
    assert set(get_args(builtin.ChannelingRisk)) == risks


#: Every search argument that reads an item, with a value that uses it, and the
#: item it reads. `version_no` reads the Set version, which is locked to base.
_FILTERS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("label", {"label": "keep"}, "label"),
    ("balance", {"balance": "sour"}, "balance"),
    # Sorted on something else, so it is the filter that is refused, not the
    # default sort on the date.
    ("since", {"since": "2026-03-01", "order_by": "shot_time"}, "started_at"),
    ("until", {"until": "2026-03-09", "order_by": "shot_time"}, "started_at"),
    ("execution_score", {"execution_score": {"min": 1}}, "execution_score"),
    ("rating", {"rating": {"min": 1}}, "rating"),
    ("shot_time", {"shot_time": {"min": 1}}, "shot_time"),
    ("yield_g", {"yield_g": {"min": 1}}, "yield"),
    ("first_drip", {"first_drip": {"min": 1}}, "first_drip"),
    ("peak_pressure", {"peak_pressure": {"min": 1}}, "peak_pressure"),
    ("brew_flow", {"brew_flow": {"min": 0}}, "brew_flow"),
    ("dose_in", {"dose_in": {"min": 1}}, "dose_in"),
    ("dose_out", {"dose_out": {"min": 1}}, "dose_out"),
    ("ratio", {"ratio": {"min": 1}}, "ratio"),
    ("channeling_risk", {"channeling_risk": "LOW"}, "channeling_risk"),
    ("resistance_level", {"resistance_level": "MODERATE"}, "resistance_level"),
    ("pressure_adherence", {"pressure_adherence": "GOOD"}, "pressure_adherence"),
    ("flow_adherence", {"flow_adherence": "GOOD"}, "flow_adherence"),
    ("order_by rating", {"order_by": "rating"}, "rating"),
    ("order_by yield_g", {"order_by": "yield_g"}, "yield"),
    ("order_by date", {"order_by": "date"}, "started_at"),
)


def _tiers_with(monkeypatch: pytest.MonkeyPatch, **moved: str) -> dict[str, Any]:
    """Tiers as a person's choice would make them, read where the tools read them."""
    tiers = {**default_tiers(), **moved}

    async def chosen(_db: object) -> dict[str, Any]:
        return tiers

    monkeypatch.setattr(builtin, "effective_tiers", chosen)
    return tiers


@pytest.mark.parametrize(("case", "arguments", "key"), _FILTERS)
async def test_the_search_refuses_to_filter_or_sort_on_an_excluded_item(
    set_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    arguments: dict[str, Any],
    key: str,
) -> None:
    """Filtering on an item the person withheld would hand back what they withheld."""
    _tiers_with(monkeypatch, **{key: "excluded"})

    outcome = await registry.dispatch(set_ctx, "list_set_shots", arguments)

    assert not outcome.ok, case
    assert outcome.data["detail"] == (
        f"{ITEMS[key].name} is not shared with the agent, so the search cannot filter or sort "
        "on it; leave that argument out."
    )


@pytest.mark.parametrize(("case", "arguments", "key"), _FILTERS)
async def test_the_search_still_filters_and_sorts_on_an_extended_item(
    set_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    arguments: dict[str, Any],
    key: str,
) -> None:
    """An extended item is one `get_shot_extended` hands over anyway."""
    _tiers_with(monkeypatch, **{key: "extended"})

    outcome = await registry.dispatch(set_ctx, "list_set_shots", arguments)

    assert outcome.ok, (case, outcome.data)


async def test_an_excluded_item_the_search_does_not_use_refuses_nothing(
    set_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tiers_with(monkeypatch, rating="excluded")

    data = await search(set_ctx, shot_time={"min": 1})

    assert data["count"] == 6
    assert all("Rating:" not in shot["text"] for shot in data["shots"])


_SEARCHABLE = sorted({key for _, _, key in _FILTERS})


async def _every_rendering(ctx: ToolContext, archive: Fixture, order_by: str) -> str:
    """Every shot, through every tool that renders one, as one text."""
    texts: list[str] = []
    for name in ("get_shot", "get_shot_extended", "get_shot_full"):
        for shot_id in archive.shots:
            outcome = await registry.dispatch(ctx, name, {"shot_id": shot_id})
            assert outcome.ok, outcome.data
            texts.append(outcome.data["text"])
    compared = await registry.dispatch(ctx, "compare_shots", {"shot_ids": archive.shots[:4]})
    assert compared.ok, compared.data
    texts += [shot["text"] for shot in compared.data["shots"]]
    texts += [shot["text"] for shot in (await search(ctx, order_by=order_by))["shots"]]
    return "\n".join(texts)


@pytest.mark.parametrize("key", _SEARCHABLE)
async def test_no_shot_tool_ever_shows_an_excluded_item(
    set_ctx: ToolContext, archive: Fixture, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Every searchable item, excluded in turn, from every tool that renders a shot."""
    line = f"\n{ITEMS[key].label}: "
    # Sorted on something other than the item itself, which would be refused.
    order_by = "shot_time" if key == "started_at" else "date"

    shown = await _every_rendering(set_ctx, archive, order_by)
    _tiers_with(monkeypatch, **{key: "excluded"})
    hidden = await _every_rendering(set_ctx, archive, order_by)

    # The fixture records no average brew flow at all, so that line never shows.
    assert line in shown or key == "brew_flow", "the item shows while it is not excluded"
    assert line not in hidden, key


@pytest.mark.parametrize("descending", [True, False])
async def test_with_the_date_excluded_the_default_sort_is_the_shot_id(
    set_ctx: ToolContext, archive: Fixture, monkeypatch: pytest.MonkeyPatch, descending: bool
) -> None:
    """The agent named no date, so nothing it asked for is refused."""
    _tiers_with(monkeypatch, started_at="excluded")

    data = await search(set_ctx, descending=descending)

    ids = [shot["shot_id"] for shot in data["shots"]]
    assert ids == sorted(archive.shots, reverse=descending)
    assert all("Date and time:" not in shot["text"] for shot in data["shots"])
