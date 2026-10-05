"""The base tier's per-phase lines: name, how the phase ended, the cup at its end and its share.

Every shot a Set conversation opens with carries one short line per phase, so the
agent need not ask for the cup at the end of a phase, which is a basic measure. The
cup lines need a scale; the share needs a version with a target.
"""

from __future__ import annotations

import dataclasses

import httpx
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.shotinfo import default_tiers, load_shots, render_shot
from gaggiclanker.shotinfo.catalogue import ITEMS, ShotTier
from gaggiclanker.shotinfo.fields import shot_fields_of
from gaggiclanker.shotinfo.glossary import render_glossary
from gaggiclanker.sync.derive import derive_shot
from tests.lever_shot import LEVER_PROFILE, lever_shot, without_scale
from tests.shotinfo.test_evaluate_route import file_lever


async def base_lines(db: Database, shot: int) -> list[str]:
    [facts] = await load_shots(db, [shot])
    return render_shot(facts, "base", default_tiers(), curve_points=60).splitlines()


def phase_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("phase ")]


async def test_every_phase_has_one_line_in_the_shots_order_with_its_share(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    shot, _, _ = await file_lever(app)
    lines = await base_lines(app.state.db, shot)
    assert phase_lines(lines) == [
        "phase 0 · preinfusion: duration 7.2 s; ended by Duration; "
        "cup at end 0.0 g, 0.0 % of target",
        "phase 1 · soak: duration 10.0 s; ended by Duration; cup at end 4.0 g, 11.1 % of target",
        "phase 2 · ramp: duration 16.2 s; ended by Volumetric target; "
        "cup at end 42.2 g, 117.2 % of target",
    ]
    # The group sits after the warnings and before everything else.
    assert lines.index("[Phases]") == lines.index("[Warnings]") + 1 + len(
        [line for line in lines[lines.index("[Warnings]") + 1 :] if "(amber)" in line]
    )


async def test_a_shot_that_is_not_filed_has_no_share_but_the_cup(app: FastAPI) -> None:
    db: Database = app.state.db
    slog = lever_shot()
    derived = derive_shot(
        slog, slog_to_raw(slog), device_id="000902", source="import", profile=LEVER_PROFILE
    )
    shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
    assert phase_lines(await base_lines(db, shot)) == [
        "phase 0 · preinfusion: duration 7.2 s; ended by Duration; cup at end 0.0 g",
        "phase 1 · soak: duration 10.0 s; ended by Duration; cup at end 4.0 g",
        "phase 2 · ramp: duration 16.2 s; ended by Volumetric target; cup at end 42.2 g",
    ]


async def test_a_shot_without_a_scale_shows_no_cup_and_no_share(app: FastAPI) -> None:
    shot, _, _ = await file_lever(app, device_id="000903", scale=False)
    assert phase_lines(await base_lines(app.state.db, shot)) == [
        "phase 0 · preinfusion: duration 7.2 s; ended by Duration",
        "phase 1 · soak: duration 10.0 s; ended by Duration",
        "phase 2 · ramp: duration 16.2 s; ended by Volumetric target",
    ]
    assert without_scale(lever_shot()).samples[0].v == 0.0  # zeros, never nulls


async def test_a_log_with_no_phase_table_shows_no_phase_lines(app: FastAPI) -> None:
    db: Database = app.state.db
    slog = lever_shot()
    bare = dataclasses.replace(
        slog, header=slog.header.model_copy(update={"version": 4, "transitions": []})
    )
    derived = derive_shot(bare, slog_to_raw(bare), device_id="000904", source="import")
    shot = await ShotsRepository(db).insert(derived.shot, derived.samples)
    lines = await base_lines(db, shot)
    assert phase_lines(lines) == []
    assert "[Phases]" not in lines


async def test_every_phase_line_carries_the_phases_duration(app: FastAPI) -> None:
    shot, _, _ = await file_lever(app, device_id="000905")
    [facts] = await load_shots(app.state.db, [shot])
    lines = phase_lines(await base_lines(app.state.db, shot))
    assert len(lines) == len(facts.phases) == 3
    for line, phase in zip(lines, facts.phases, strict=True):
        assert f"duration {phase['duration_seconds']:.1f} s; ended by" in line


async def test_an_extended_read_alone_still_names_every_phase(app: FastAPI) -> None:
    shot, _, _ = await file_lever(app, device_id="000906")
    [facts] = await load_shots(app.state.db, [shot])
    tiers: tuple[ShotTier, ...] = ("extended", "full")
    for tier in tiers:
        lines = phase_lines(render_shot(facts, tier, default_tiers(), curve_points=60).splitlines())
        assert [line.split(":")[0] for line in lines] == [
            "phase 0 · preinfusion",
            "phase 1 · soak",
            "phase 2 · ramp",
        ]


async def test_the_cup_and_its_share_are_one_item_for_the_chat_and_two_for_the_page(
    app: FastAPI,
) -> None:
    shot, _, _ = await file_lever(app, device_id="000909")
    [facts] = await load_shots(app.state.db, [shot])
    # No chat tier carries the share as an item of its own; the glossary has no entry for it.
    assert default_tiers()["phase_cup_share"] == "excluded"
    for tier in ("base", "extended", "full"):
        text = render_shot(facts, tier, default_tiers(), curve_points=60)
        assert "cup share of target" not in text
    assert "cup share of target" not in render_glossary(default_tiers(), "base")
    assert "cup share of target" not in (render_glossary(default_tiers(), "extended") or "")
    # The merged text, with a target, is the cup and its share; the page's field is unchanged.
    ramp = facts.phases[2]
    merged = ITEMS["phase_cup_end"].chat_phase
    assert merged is not None and merged(facts, ramp) == "42.2 g, 117.2 % of target"
    served = {f.key: f for p in shot_fields_of(facts).phases for f in p.fields if p.number == 2}
    assert served["phase_cup_end"].text == "42.2 g"
    assert served["phase_cup_share"].value == 117.2
    assert served["phase_cup_share"].text == "117.2 %"
