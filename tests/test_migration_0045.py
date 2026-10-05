"""0045 retires the execution score and keeps every shot, then the next boot derives them again.

A database as the version before this one left it (a populated archive with
scored shots, a view that names the score columns, a person's tier choices and
the seeded band rules) is migrated, and then booted: the views answer, nothing is
lost or dangling, every shot is derived again from its bytes (so the stored score
block and the band annotations are gone), and the choices a person made about the
renamed and the retired items follow or go.
"""

from __future__ import annotations

import json
from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.knowledge import RulesRepository, RuleWrite
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.exports import slog_to_raw
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.knowledge.rules import seed_rules
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync.derive import DERIVATION_VERSION, derive_shot
from tests.conftest import running_app
from tests.domain.helpers import SLOG_FIXTURES
from tests.lever_shot import LEVER_PROFILE, TARGET_YIELD_G, lever_shot
from tests.test_migrations import _migrate_below

#: What a diagnostics blob looked like before: the score block, the bands, the channeling block.
OLD_BLOB = json.dumps(
    {
        "summary": {"flow": {"time_to_first_drip_s": 8.2}},
        "diagnostics": {
            "has_pressure": True,
            "resistance": {
                "source": "machine",
                "avg": 1.3,
                "std": 0.2,
                "slope": -0.1,
                "peak": 2.0,
                "peak_timing_pct": 0.1,
                "annotations": {"level": "LOW", "saturation": "EARLY"},
            },
            "channeling": {"channeling_risk": "LOW", "annotations": {}},
            "temperature": {"overshoot_c": 0.1, "annotations": {"stability": "STABLE"}},
        },
        "has_pressure": True,
        "score": {
            "score": 9.3,
            "confidence": "high",
            "reason": "Clean execution",
            "components": {},
        },
    }
)


async def _old_archive(db: Database, tmp_path: Path) -> dict[str, int]:
    """A populated archive at the version before 0045, in the shapes that version wrote."""
    below = await _migrate_below(db, tmp_path, "0045")
    assert below[-1] == "0044"

    bean = await BeansRepository(db).create(BeanWrite(name="Guji", roast_level="light"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche", step_unit="numbers"))
    sets = SetsRepository(db)
    row = await sets.create(
        SetWrite(name="Guji", bean_id=bean.id, grinder_id=grinder.id),
        SetVersionWrite(dose_g=18.0, target_yield_g=TARGET_YIELD_G, grind_setting="14"),
    )
    assert row.current_version_id is not None

    profile, _ = await ProfilesRepository(db).ensure_version(Profile.model_validate(LEVER_PROFILE))
    shots = ShotsRepository(db)
    ids: dict[str, int] = {}
    sources = {path.stem: path.read_bytes() for path in sorted(SLOG_FIXTURES.glob("*.slog"))}
    lever = lever_shot()
    sources["lever"] = slog_to_raw(lever)
    for number, (name, raw) in enumerate(sources.items()):
        derived = derive_shot(
            parse_slog(raw),
            raw,
            device_id=f"{number + 1:06d}",
            profile=LEVER_PROFILE if name == "lever" else None,
        )
        ids[name] = await shots.insert(derived.shot, derived.samples)
        assert await sets.assign_shot(ids[name], row.current_version_id)
        if name == "lever":
            await db.execute(
                "UPDATE shots SET profile_version_id = ? WHERE id = ?", (profile.id, ids[name])
            )
        # What the previous version stored: its score, its sentence, its blob and its version.
        await db.execute(
            "UPDATE shots SET execution_score = 9.3, execution_reason = 'Clean execution', "
            "diagnostics_json = ?, derivation_version = 7 WHERE id = ?",
            (OLD_BLOB, ids[name]),
        )
    await JudgementsRepository(db).upsert(
        ids["lever"], JudgementWrite(rating=1, dose_in_g=18.0, dose_out_g=42.2)
    )
    # A person's choices about items that were renamed, retired and kept.
    for key, tier in (
        ("resistance_erosion", "base"),
        ("execution_score", "excluded"),
        ("channeling_risk", "excluded"),
        ("rating", "excluded"),
        ("phase_ramp", "base"),
    ):
        await db.execute("INSERT INTO shot_info_tiers (item_key, tier) VALUES (?, ?)", (key, tier))
    # Seeded rules: a band explanation nobody touched, one a person edited, one that stays.
    rules = RulesRepository(db)
    for category, key in (
        ("band_meanings", "channeling_risk:LOW"),
        ("band_meanings", "resistance_level:HIGH"),
        ("telemetry_to_cause", "flow_jitter"),
        ("telemetry_to_cause", "first_drip_fast"),
    ):
        await rules.insert(
            RuleWrite(category=category, key=key, value={"text": "seeded"}, source="crema")
        )
    await db.execute(
        'UPDATE knowledge_rules SET value_json = \'{"text": "mine"}\' '
        "WHERE key = 'resistance_level:HIGH'"
    )
    return ids


async def test_the_migration_keeps_every_row_and_drops_only_what_it_retires(
    data_dir: Path, tmp_path: Path
) -> None:
    db = Database(data_dir / "old.db")
    await db.connect()
    try:
        ids = await _old_archive(db, tmp_path)
        before = [
            tuple(r)
            for r in await db.fetch_all(
                "SELECT id, device_id, raw_slog, duration_ms, final_weight_g, set_version_id "
                "FROM shots ORDER BY id"
            )
        ]
        samples_before = await db.fetch_value("SELECT COUNT(*) FROM shot_samples")

        assert await run_migrations(db) == ["0045"]

        columns = {r["name"] for r in await db.fetch_all("PRAGMA table_info(shots)")}
        assert not columns & {"execution_score", "execution_reason"}
        assert before == [
            tuple(r)
            for r in await db.fetch_all(
                "SELECT id, device_id, raw_slog, duration_ms, final_weight_g, set_version_id "
                "FROM shots ORDER BY id"
            )
        ]
        assert await db.fetch_value("SELECT COUNT(*) FROM shot_samples") == samples_before
        assert await db.fetch_value("PRAGMA integrity_check") == "ok"
        assert await db.fetch_all("PRAGMA foreign_key_check") == []

        # The view answers, without the score and with the other columns as they were.
        view = await db.fetch_all("SELECT * FROM v_shots ORDER BY shot_id")
        assert [r["shot_id"] for r in view] == sorted(ids.values())
        assert not {"execution_score", "execution_reason"} & set(view[0].keys())
        assert {"volume_g", "ratio", "set_dose_g", "target_yield_g", "diagnostics_json"} <= set(
            view[0].keys()
        )
        lever = next(r for r in view if r["shot_id"] == ids["lever"])
        assert round(lever["ratio"], 2) == 2.34  # the judgement's 42.2 g over its 18 g
        unjudged = next(r for r in view if r["shot_id"] == ids["shot_196_baseline_high"])
        assert round(unjudged["ratio"], 2) == round(31.5 / 18.0, 2)  # the scale's, the version's
    finally:
        await db.close()


async def test_the_choices_and_the_rules_follow_or_go(data_dir: Path, tmp_path: Path) -> None:
    db = Database(data_dir / "old.db")
    await db.connect()
    try:
        await _old_archive(db, tmp_path)
        await run_migrations(db)

        tiers = {
            r["item_key"]: r["tier"] for r in await db.fetch_all("SELECT * FROM shot_info_tiers")
        }
        assert tiers == {"resistance_slope": "base", "rating": "excluded", "phase_ramp": "base"}
        rules = {
            (r["category"], r["key"])
            for r in await db.fetch_all("SELECT category, key FROM knowledge_rules")
        }
        assert rules == {
            ("band_meanings", "resistance_level:HIGH"),  # edited by a person: theirs, kept
            ("telemetry_to_cause", "first_drip_fast"),
        }
    finally:
        await db.close()


async def test_the_next_boot_derives_every_shot_again_without_the_score(
    env: EnvSettings, tmp_path: Path
) -> None:
    db = Database(env.database_path)
    await db.connect()
    try:
        ids = await _old_archive(db, tmp_path)
    finally:
        await db.close()

    async with running_app(env) as (app, client):
        stored = await app.state.db.fetch_all("SELECT id, derivation_version FROM shots")
        assert {r["derivation_version"] for r in stored} == {DERIVATION_VERSION}
        for shot_id in ids.values():
            body = (await client.get(f"/api/shots/{shot_id}")).json()["data"]["shot"]
            blob = body["diagnostics"]
            assert "score" not in blob
            assert "channeling" not in blob["diagnostics"]
            assert blob["metrics"]["per_phase"] is True
            assert "execution_score" not in body
        # Served as fields, with the lever shot's own warnings (it is filed under 36 g).
        fields = (await client.get(f"/api/shots/{ids['lever']}/fields")).json()["data"]
        assert [w["fault"] for w in fields["warnings"]] == ["fast flow", "skipped", "over target"]


async def _decline_rule_after_migrating(
    data_dir: Path, tmp_path: Path, *, edited: bool
) -> list[tuple[str, str]]:
    db = Database(data_dir / ("edited.db" if edited else "plain.db"))
    await db.connect()
    try:
        await _migrate_below(db, tmp_path, "0045")
        await RulesRepository(db).insert(
            RuleWrite(
                category="pressure_matrix",
                key="decline_compensates_erosion",
                value={"text": "seeded"},
                source="crema",
            )
        )
        if edited:
            await db.execute(
                'UPDATE knowledge_rules SET value_json = \'{"text": "mine"}\' '
                "WHERE key = 'decline_compensates_erosion'"
            )
        await run_migrations(db)
        # The boot after the migration seeds the shipped rules: one row for the rule either way.
        await seed_rules(RulesRepository(db))
        return [
            (r["key"], r["value_json"])
            for r in await db.fetch_all(
                "SELECT key, value_json FROM knowledge_rules WHERE category = 'pressure_matrix'"
            )
        ]
    finally:
        await db.close()


async def test_a_person_s_edit_of_the_erosion_rule_moves_to_its_new_key_with_their_text(
    data_dir: Path, tmp_path: Path
) -> None:
    rows = await _decline_rule_after_migrating(data_dir, tmp_path, edited=True)
    assert [key for key, _ in rows if key.startswith("decline")] == ["decline_holds_flow"]
    assert ("decline_holds_flow", '{"text": "mine"}') in rows


async def test_an_untouched_erosion_rule_is_removed_for_the_seed_to_add_again(
    data_dir: Path, tmp_path: Path
) -> None:
    rows = await _decline_rule_after_migrating(data_dir, tmp_path, edited=False)
    assert [key for key, _ in rows if key.startswith("decline")] == ["decline_holds_flow"]
    assert ("decline_holds_flow", '{"text": "mine"}') not in rows
