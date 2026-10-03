"""0039 gives an insight its Set, and agent-written insights are placed on the one Set they fit.

The move is not SQL, because "fits" is the live matching rule and a copy of its
logic in SQL would be a second rule. So these tests build databases like the
maintainer's — several Sets on one bean and one grinder, an archived one, a Set
being designed, insights from analyses and chats with and without evidence,
hand-written ones — and check the placement against an oracle that is written
here from raw rows, calling only the live `scope_matches`. Every placed insight
must fit exactly the Set it landed on, every unplaced one must fit none or
several, hand-written ones never move, and nothing is lost.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import MIGRATIONS_DIR, run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.insight_placement import InsightPlacementBuilder
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
    scope_matches,
    set_attributes,
)
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from tests.sets.conftest import make_shot


@pytest.fixture
async def db(data_dir: Path) -> AsyncIterator[Database]:
    database = Database(data_dir / "test.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@dataclass(slots=True)
class Kitchen:
    db: Database
    guji: int
    huila: int
    grinder: int
    a: int
    a_v1: int
    a_v2: int
    b: int
    b_v1: int
    c_archived: int
    c_v1: int
    d: int
    d_v1: int
    designing: int
    shots: dict[str, int]


async def _set(sets: SetsRepository, name: str, bean: int, grinder: int | None) -> tuple[int, int]:
    row = await sets.create(
        SetWrite(name=name, bean_id=bean, grinder_id=grinder),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
    )
    assert row.current_version_id is not None
    return row.id, row.current_version_id


@pytest.fixture
async def kitchen(db: Database) -> Kitchen:
    guji = (
        await BeansRepository(db).create(
            BeanWrite(name="Guji", roaster="Hasbean", roast_level="light", process="natural")
        )
    ).id
    huila = (
        await BeansRepository(db).create(
            BeanWrite(name="Huila", roaster="Hasbean", roast_level="dark", process="washed")
        )
    ).id
    grinder = (
        await GrindersRepository(db).create(
            GrinderWrite(name="Niche Zero", burr_type="conical", step_unit="numbers")
        )
    ).id
    sets = SetsRepository(db)
    a, a_v1 = await _set(sets, "Guji A", guji, grinder)
    a_v2_row = await sets.add_version(a, SetVersionPatch(grind_setting="21", intent="finer"))
    assert a_v2_row is not None
    b, b_v1 = await _set(sets, "Guji B", guji, grinder)
    c, c_v1 = await _set(sets, "Guji, last bag", guji, grinder)
    d, d_v1 = await _set(sets, "Huila", huila, grinder)
    designing = (
        await sets.create_design(
            SetWrite(name="Designing", bean_id=guji, grinder_id=grinder), DesignBrief()
        )
    ).id

    # Versions have known dates, so "the version current when it was written" is testable.
    await db.execute(
        "UPDATE set_versions SET created_at = '2026-01-01T08:00:00.000Z' WHERE id = ?", (a_v1,)
    )
    await db.execute(
        "UPDATE set_versions SET created_at = '2026-02-01T08:00:00.000Z' WHERE id = ?",
        (a_v2_row.id,),
    )
    for version_id in (b_v1, c_v1, d_v1):
        await db.execute(
            "UPDATE set_versions SET created_at = '2026-01-10T08:00:00.000Z' WHERE id = ?",
            (version_id,),
        )

    shots: dict[str, int] = {}
    plan = {
        "a1": (a_v1, "2026-01-02T08:00:00.000Z"),
        "a2": (a_v2_row.id, "2026-02-02T08:00:00.000Z"),
        "a3": (a_v1, "2026-01-05T08:00:00.000Z"),
        "b1": (b_v1, "2026-01-12T08:00:00.000Z"),
        "c1": (c_v1, "2026-01-13T08:00:00.000Z"),
        "d1": (d_v1, "2026-01-14T08:00:00.000Z"),
    }
    for number, (key, (version_id, started)) in enumerate(plan.items()):
        shot_id = await make_shot(db, f"0008{number:02d}", started_at=started)
        assert await sets.assign_shot(shot_id, version_id)
        shots[key] = shot_id
    # Archived last: a Set that is archived takes no more shots.
    await sets.archive(c)
    return Kitchen(
        db=db,
        guji=guji,
        huila=huila,
        grinder=grinder,
        a=a,
        a_v1=a_v1,
        a_v2=a_v2_row.id,
        b=b,
        b_v1=b_v1,
        c_archived=c,
        c_v1=c_v1,
        d=d,
        d_v1=d_v1,
        designing=designing,
        shots=shots,
    )


async def _insight(
    kitchen: Kitchen,
    text: str,
    *,
    source: str = "chat",
    scope: dict[str, Any] | None = None,
    evidence: list[int] | None = None,
    confirmed: bool = True,
    created_at: str = "2026-01-20T08:00:00.000Z",
) -> int:
    insight_id = await InsightsRepository(kitchen.db).insert(
        InsightWrite(
            text=text,
            source=source,  # type: ignore[arg-type]
            scope=InsightScope(**(scope or {})),
            evidence_shot_ids=evidence or [],
            confirmed=confirmed,
        )
    )
    await kitchen.db.execute(
        "UPDATE knowledge_insights SET created_at = ?, updated_at = ?, "
        "confirmed_at = CASE WHEN confirmed = 1 THEN ? END WHERE id = ?",
        (created_at, created_at, created_at, insight_id),
    )
    return insight_id


async def _oracle(db: Database, row: dict[str, Any]) -> list[int]:
    """The Sets an insight fits, from raw rows. Only `scope_matches` is the live function."""
    scope = {
        key: value for key, value in json.loads(row["scope_json"]).items() if value is not None
    }
    evidence = {int(shot) for shot in json.loads(row["evidence_shot_ids_json"])}
    fits: list[int] = []
    # About the coffee: a bean in the scope, or no scope and evidence shots.
    # Anything else is equipment, a kind of coffee or the kitchen, and stays general.
    if not ("bean_id" in scope or (not scope and evidence)):
        return fits
    for candidate in await db.fetch_all(
        "SELECT s.id, s.bean_id, s.grinder_id, b.roast_level, b.process, b.origin "
        "FROM sets s LEFT JOIN beans b ON b.id = s.bean_id WHERE s.designing = 0 ORDER BY s.id"
    ):
        attributes = set_attributes(
            bean_id=candidate["bean_id"],
            grinder_id=candidate["grinder_id"],
            roast_level=candidate["roast_level"],
            process=candidate["process"],
            origin=candidate["origin"],
        )
        if not scope_matches(scope, attributes):
            continue
        if evidence:
            filed = {
                int(shot["id"])
                for shot in await db.fetch_all(
                    "SELECT sh.id FROM shots sh JOIN set_versions v ON v.id = sh.set_version_id "
                    "WHERE v.set_id = ?",
                    (candidate["id"],),
                )
            }
            if not (evidence & filed):
                continue
        fits.append(int(candidate["id"]))
    return fits


async def _rows(db: Database) -> dict[int, dict[str, Any]]:
    return {
        int(row["id"]): dict(zip(row.keys(), tuple(row), strict=True))
        for row in await db.fetch_all("SELECT * FROM knowledge_insights ORDER BY id")
    }


class TestTheUpgrade:
    async def test_existing_insights_survive_as_general_ones(
        self, data_dir: Path, tmp_path: Path
    ) -> None:
        database = Database(data_dir / "old.db")
        await database.connect()
        try:
            directory = tmp_path / "below-0039"
            directory.mkdir()
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name < "0039":
                    shutil.copy(path, directory / path.name)
            await run_migrations(database, directory)
            await database.execute(
                "INSERT INTO knowledge_insights "
                "(scope_json, text, source, confirmed, confirmed_at) "
                "VALUES ('{\"bean_id\": 1}', 'Kept.', 'chat', 1, '2026-01-01T00:00:00Z'), "
                "('{}', 'Waiting.', 'analysis', 0, NULL)"
            )

            # Later migrations run too; this file is about 0039 being the first of them.
            assert (await run_migrations(database))[0] == "0039"
            rows = await _rows(database)

            assert [
                (r["text"], r["set_id"], r["set_version_id"], r["dismissed"]) for r in rows.values()
            ] == [
                ("Kept.", None, None, 0),
                ("Waiting.", None, None, 0),
            ]
            assert rows[1]["confirmed"] == 1 and rows[1]["confirmed_at"] == "2026-01-01T00:00:00Z"
            assert await database.fetch_all("PRAGMA foreign_key_check") == []
            assert await database.fetch_value("SELECT COUNT(*) FROM insight_placement_build") == 0
        finally:
            await database.close()


class TestWhereEachOneLands:
    async def test_the_rule_on_every_kind_of_insight(self, kitchen: Kitchen) -> None:
        s = kitchen.shots
        ids = {
            # One Set: evidence only in A, scope matches A (and B, C by attributes).
            "a_only": await _insight(
                kitchen,
                "Guji wants it finer.",
                scope={"bean_id": kitchen.guji},
                evidence=[s["a1"], s["a2"]],
            ),
            # Evidence in two live Sets: ambiguous.
            "a_and_b": await _insight(
                kitchen,
                "Guji pours fast.",
                scope={"bean_id": kitchen.guji},
                evidence=[s["a1"], s["b1"]],
            ),
            # No evidence, scope fits three Sets: ambiguous.
            "no_evidence_guji": await _insight(
                kitchen, "Guji likes it hot.", scope={"bean_id": kitchen.guji}
            ),
            # No evidence, scope fits exactly one Set (the only Huila).
            "huila_scope": await _insight(
                kitchen,
                "Huila wants it coarser.",
                source="analysis",
                scope={"bean_id": kitchen.huila},
                created_at="2026-01-20T08:00:00.000Z",
            ),
            # Empty scope, evidence in one Set: fits that one.
            "empty_scope_evidence": await _insight(
                kitchen, "Pre-infuse longer.", scope={}, evidence=[s["d1"]]
            ),
            # Scope names Guji, evidence is Huila's only: fits none.
            "scope_vs_evidence": await _insight(
                kitchen,
                "Guji, but about Huila's shot.",
                scope={"bean_id": kitchen.guji},
                evidence=[s["d1"]],
            ),
            # Hand-written, fits exactly one Set: never moves.
            "hand_written": await _insight(
                kitchen,
                "My own note on Huila.",
                source="user",
                scope={"bean_id": kitchen.huila},
                evidence=[s["d1"]],
            ),
            # Evidence only in the archived Set: archived counts as a candidate.
            "archived_only": await _insight(
                kitchen, "Last bag note.", scope={"bean_id": kitchen.guji}, evidence=[s["c1"]]
            ),
            # Live and archived: ambiguous.
            "live_and_archived": await _insight(
                kitchen,
                "Guji, both bags.",
                scope={"bean_id": kitchen.guji},
                evidence=[s["a1"], s["c1"]],
            ),
            # Evidence that names no shot at all: fits none.
            "ghost_evidence": await _insight(
                kitchen,
                "From a shot that is gone.",
                scope={"bean_id": kitchen.guji},
                evidence=[99999],
            ),
            # Unconfirmed stays unconfirmed when it moves.
            "waiting": await _insight(
                kitchen,
                "Huila, waiting.",
                scope={"bean_id": kitchen.huila},
                evidence=[s["d1"]],
                confirmed=False,
            ),
        }
        before = await _rows(kitchen.db)

        counts = await InsightPlacementBuilder(kitchen.db).build()

        after = await _rows(kitchen.db)
        placed = {name: after[i]["set_id"] for name, i in ids.items()}
        assert placed == {
            "a_only": kitchen.a,
            "a_and_b": None,
            "no_evidence_guji": None,
            "huila_scope": kitchen.d,
            "empty_scope_evidence": kitchen.d,
            "scope_vs_evidence": None,
            "hand_written": None,
            "archived_only": kitchen.c_archived,
            "live_and_archived": None,
            "ghost_evidence": None,
            "waiting": kitchen.d,
        }
        assert counts == {"considered": 10, "moved": 5, "ambiguous": 3, "left_general": 5}

        # The version it was learned at: the newest evidence shot in that Set,
        # else the version current when it was written, else none.
        versions = {name: after[i]["set_version_id"] for name, i in ids.items()}
        assert versions["a_only"] == kitchen.a_v2, "the newest evidence shot is in v1.1"
        assert versions["huila_scope"] == kitchen.d_v1, "no evidence: current at created_at"
        assert versions["empty_scope_evidence"] == kitchen.d_v1
        assert versions["archived_only"] == kitchen.c_v1
        assert versions["hand_written"] is None

        # Nothing is lost: the same rows, texts, confirmations, evidence and dates.
        assert set(after) == set(before)
        for insight_id, was in before.items():
            now = after[insight_id]
            for column in (
                "text",
                "confirmed",
                "confirmed_at",
                "evidence_shot_ids_json",
                "scope_json",
                "source",
                "created_at",
                "updated_at",
            ):
                assert now[column] == was[column], (insight_id, column)
            assert now["dismissed"] == 0
        assert after[ids["waiting"]]["confirmed"] == 0

    async def test_every_placed_one_fits_its_set_and_every_unplaced_one_fits_none_or_several(
        self, kitchen: Kitchen
    ) -> None:
        s = kitchen.shots
        scopes: list[dict[str, Any]] = [
            {},
            {"bean_id": kitchen.guji},
            {"bean_id": kitchen.huila},
            {"grinder_id": kitchen.grinder},
            {"process": "natural"},
            {"roast_level": "dark"},
            {"bean_id": kitchen.guji, "grinder_id": kitchen.grinder},
            {"origin": "Ethiopia"},
        ]
        evidences: list[list[int]] = [
            [],
            [s["a1"]],
            [s["b1"]],
            [s["c1"]],
            [s["d1"]],
            [s["a1"], s["a2"]],
            [s["a1"], s["d1"]],
            [s["b1"], s["c1"]],
            [4242],
        ]
        number = 0
        for source in ("chat", "analysis", "user"):
            for scope in scopes:
                for evidence in evidences:
                    number += 1
                    await _insight(
                        kitchen,
                        f"Insight {number}.",
                        source=source,
                        scope=scope,
                        evidence=evidence,
                        confirmed=number % 3 != 0,
                    )
        before = await _rows(kitchen.db)

        await InsightPlacementBuilder(kitchen.db).build()

        after = await _rows(kitchen.db)
        moved = 0
        for insight_id, row in before.items():
            fits = await _oracle(kitchen.db, row)
            placed = after[insight_id]["set_id"]
            if row["source"] == "user":
                assert placed is None, "hand-written insights never move"
            elif placed is not None:
                moved += 1
                assert fits == [placed], (insight_id, fits, placed)
            else:
                assert len(fits) != 1, (insight_id, fits)
        assert moved > 0, "a seeded database in which nothing moves would prove nothing"
        # Nothing lost, nothing confirmed or unconfirmed by the move.
        assert len(after) == len(before)
        assert sum(r["confirmed"] for r in after.values()) == sum(
            r["confirmed"] for r in before.values()
        )
        assert [r["text"] for r in after.values()] == [r["text"] for r in before.values()]
        assert await kitchen.db.fetch_value("PRAGMA integrity_check") == "ok"
        assert await kitchen.db.fetch_all("PRAGMA foreign_key_check") == []

    async def test_a_placed_insight_is_about_the_coffee_its_text_names(
        self, kitchen: Kitchen
    ) -> None:
        """The stop condition, on coherent data: no insight lands on another coffee's Set."""
        s = kitchen.shots
        await _insight(
            kitchen, "Guji: go finer.", scope={"bean_id": kitchen.guji}, evidence=[s["a1"]]
        )
        await _insight(
            kitchen, "Huila: go coarser.", scope={"bean_id": kitchen.huila}, evidence=[s["d1"]]
        )
        await _insight(kitchen, "Huila: lower the dose.", scope={"bean_id": kitchen.huila})
        await _insight(kitchen, "Guji: shorter pre-infusion.", scope={"bean_id": kitchen.guji})
        await InsightPlacementBuilder(kitchen.db).build()

        for row in (await _rows(kitchen.db)).values():
            if row["set_id"] is None:
                continue
            bean = await kitchen.db.fetch_value(
                "SELECT b.name FROM sets s JOIN beans b ON b.id = s.bean_id WHERE s.id = ?",
                (row["set_id"],),
            )
            assert str(row["text"]).startswith(str(bean)), row["text"]

    async def test_a_designing_set_is_never_a_candidate(self, kitchen: Kitchen) -> None:
        insight_id = await _insight(
            kitchen, "Everything.", scope={"bean_id": kitchen.guji, "grinder_id": kitchen.grinder}
        )
        builder = InsightPlacementBuilder(kitchen.db)
        row = await InsightsRepository(kitchen.db).get(insight_id)
        assert row is not None
        fitting = await builder.fitting_sets(row)
        assert kitchen.designing not in fitting
        assert fitting == [kitchen.a, kitchen.b, kitchen.c_archived]

    async def test_a_version_before_the_sets_first_is_none(self, kitchen: Kitchen) -> None:
        """Written before the Set had a version: "learned before versions were recorded"."""
        insight_id = await _insight(
            kitchen,
            "Early Huila note.",
            scope={"bean_id": kitchen.huila},
            created_at="2025-12-01T08:00:00.000Z",
        )
        await InsightPlacementBuilder(kitchen.db).build()
        row = (await _rows(kitchen.db))[insight_id]
        assert row["set_id"] == kitchen.d and row["set_version_id"] is None

    async def test_the_version_follows_the_newest_evidence_shot_not_the_newest_id(
        self, kitchen: Kitchen
    ) -> None:
        s = kitchen.shots
        # a2 (in v1.1) is the newest by date; a3 has the highest id but is older.
        insight_id = await _insight(
            kitchen,
            "Guji, across versions.",
            scope={"bean_id": kitchen.guji},
            evidence=[s["a3"], s["a2"], s["a1"]],
        )
        await InsightPlacementBuilder(kitchen.db).build()
        assert (await _rows(kitchen.db))[insight_id]["set_version_id"] == kitchen.a_v2


class TestOnlyInsightsAboutTheCoffeeMove:
    """The scope names a bean, or is empty with evidence; everything else stays general."""

    async def test_equipment_kind_and_kitchen_insights_stay_general_whatever_their_evidence(
        self, db: Database
    ) -> None:
        bean = (
            await BeansRepository(db).create(
                BeanWrite(name="Only", roast_level="light", process="natural", origin="Kenya")
            )
        ).id
        grinder = (
            await GrindersRepository(db).create(
                GrinderWrite(name="Niche", burr_type="conical", step_unit="numbers")
            )
        ).id
        sets = SetsRepository(db)
        set_id, version_id = await _set(sets, "The only Set", bean, grinder)
        shot_id = await make_shot(db, "000001")
        assert await sets.assign_shot(shot_id, version_id)
        kitchen = Kitchen(
            db=db,
            guji=bean,
            huila=0,
            grinder=grinder,
            a=set_id,
            a_v1=version_id,
            a_v2=0,
            b=0,
            b_v1=0,
            c_archived=0,
            c_v1=0,
            d=0,
            d_v1=0,
            designing=0,
            shots={},
        )
        cases: dict[str, tuple[dict[str, Any], list[int]]] = {
            "grinder_no_evidence": ({"grinder_id": grinder}, []),
            "grinder_with_evidence": ({"grinder_id": grinder}, [shot_id]),
            "roast": ({"roast_level": "light"}, [shot_id]),
            "process": ({"process": "natural"}, [shot_id]),
            "origin": ({"origin": "Kenya"}, [shot_id]),
            "empty_no_evidence": ({}, []),
            # The two that do move, with this Set the only candidate:
            "bean": ({"bean_id": bean}, []),
            "legacy_empty_with_evidence": ({}, [shot_id]),
        }
        ids = {
            name: await _insight(kitchen, name, scope=scope, evidence=evidence)
            for name, (scope, evidence) in cases.items()
        }

        await InsightPlacementBuilder(db).build()

        rows = await _rows(db)
        assert {name: rows[i]["set_id"] for name, i in ids.items()} == {
            "grinder_no_evidence": None,
            "grinder_with_evidence": None,
            "roast": None,
            "process": None,
            "origin": None,
            "empty_no_evidence": None,
            "bean": set_id,
            "legacy_empty_with_evidence": set_id,
        }


class TestOnce:
    async def test_it_runs_once_and_never_redoes_what_a_person_chose(
        self, kitchen: Kitchen
    ) -> None:
        s = kitchen.shots
        moved = await _insight(
            kitchen, "Guji, in A.", scope={"bean_id": kitchen.guji}, evidence=[s["a1"]]
        )
        builder = InsightPlacementBuilder(kitchen.db)
        assert await builder.built() is False
        first = await builder.build()
        assert first is not None and first["moved"] == 1
        assert await builder.built() is True

        # A person dismissed it, and a new agent-written general insight arrived later.
        await InsightsRepository(kitchen.db).dismiss(moved)
        later = await _insight(
            kitchen, "Huila, later.", scope={"bean_id": kitchen.huila}, evidence=[s["d1"]]
        )

        assert await builder.build() is None
        rows = await _rows(kitchen.db)
        assert rows[moved]["dismissed"] == 1 and rows[moved]["set_id"] == kitchen.a
        assert rows[later]["set_id"] is None

    async def test_an_empty_archive_still_records_that_it_ran(self, db: Database) -> None:
        builder = InsightPlacementBuilder(db)
        counts = await builder.build()
        assert counts == {"considered": 0, "moved": 0, "ambiguous": 0, "left_general": 0}
        assert await builder.build() is None

    async def test_the_app_runs_it_at_boot(self, app: FastAPI) -> None:
        marker = await app.state.db.fetch_one("SELECT moved FROM insight_placement_build")
        assert marker is not None and marker["moved"] == 0
