"""A shot's checks in every place it is read: the list's badge, the sort, the fields, the chat.

The shots are constructed from a real fixture (`tests/lever_shot.py`); an absent sensor is a
shot with its fields zeroed and its flag cleared, never a synthetic null.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import CHECK_SORT, ShotListItem, ShotsRepository
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.shotinfo import load_shots
from gaggiclanker.shotinfo.catalogue import default_tiers
from gaggiclanker.shotinfo.fields import shot_fields
from gaggiclanker.shotinfo.render import render_shot
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE, lever_shot, without_pressure, without_scale
from tests.signatures.helpers import (
    add_shot,
    make_set_versions,
    turbo_profile,
    turbo_shot,
)
from tests.signatures.test_carry import RAMP_CUP, _version

SOAK_CUP = {
    "channel": "cup_weight",
    "op": "at_end",
    "window": {"phase": "soak"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.05},
}


def _lever_signature() -> list[ExpectationInput]:
    return [
        ExpectationInput(tier="critical", kind="reached", phase="decline"),
        ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
        ExpectationInput(tier="important", kind="measure", expression=SOAK_CUP),
        ExpectationInput(
            tier="context",
            kind="free_text",
            text="pressure and flow fall together through the decline",
            fault="unstable",
        ),
    ]


class Lever:
    def __init__(self, db: Database, version: int, set_version: int, other_version: int, shot: int):
        self.db = db
        self.version = version
        self.set_version = set_version
        self.other_version = other_version
        self.shot = shot


async def _lever(db: Database) -> Lever:
    version = await _version(db, LEVER_PROFILE)
    set_version, other = await make_set_versions(db, version)
    shot = await add_shot(db, set_version_id=set_version, profile_version_id=version)
    return Lever(db, version, set_version, other, shot)


async def _propose(
    lever: Lever, *, confirm: bool, items: list[ExpectationInput] | None = None
) -> list[ExpectationRow]:
    rows = await SignatureService(lever.db).propose(
        lever.version, items or _lever_signature(), reason="what the lever is for"
    )
    if confirm:
        await SignatureRepository(lever.db).confirm_all(lever.version)
    return rows


async def _row(db: Database, shot: int) -> ShotListItem:
    page = await ShotsRepository(db).list_shots(limit=50)
    return next(row for row in page.items if row.id == shot)


async def test_a_confirmed_lever_signature_makes_the_badge_red_and_names_the_fault(
    db: Database,
) -> None:
    lever = await _lever(db)
    await _propose(lever, confirm=True)

    row = await _row(db, lever.shot)

    assert row.checks.badge == "ramp: early yield +4"
    assert [(w.phase, w.fault, w.severity, w.status, w.tier) for w in row.checks.entries] == [
        ("ramp", "early yield", "red", "failed", "critical"),
        ("decline", "skipped", "red", "failed", "critical"),
        ("soak", "early yield", "amber", "failed", "important"),
        ("ramp", "fast flow", "amber", "warning", None),
        ("Shot", "over target", "amber", "warning", None),
    ]


async def test_the_same_shot_with_no_confirmed_signature_reads_as_it_always_did(
    db: Database,
) -> None:
    lever = await _lever(db)
    before = await _row(db, lever.shot)
    assert before.checks.badge == "ramp: fast flow +2"
    assert {w.severity for w in before.checks.entries} == {"amber"}

    # Proposed is not confirmed: the row, the badge and the order are what they were.
    await _propose(lever, confirm=False)
    proposed = await _row(db, lever.shot)
    assert proposed.model_dump() == before.model_dump()

    # A rejected one is no more a check than a proposed one.
    for row in await SignatureRepository(db).for_version(lever.version):
        await SignatureRepository(db).answer(row.id, confirm=False, reject_reason="no")
    assert (await _row(db, lever.shot)).model_dump() == before.model_dump()


async def test_confirming_changes_every_shot_at_once_and_stores_nothing_on_a_shot(
    db: Database,
) -> None:
    lever = await _lever(db)
    second = await add_shot(
        db,
        set_version_id=lever.other_version,
        profile_version_id=lever.version,
        device_id="000901",
    )
    stored = [
        tuple(r)
        for r in await db.fetch_all(
            "SELECT id, phases_json, diagnostics_json, derivation_version, updated_at "
            "FROM shots ORDER BY id"
        )
    ]
    await _propose(lever, confirm=True)

    assert (await _row(db, lever.shot)).checks.badge == "ramp: early yield +4"
    assert (await _row(db, second)).checks.badge == "ramp: early yield +4"
    assert stored == [
        tuple(r)
        for r in await db.fetch_all(
            "SELECT id, phases_json, diagnostics_json, derivation_version, updated_at "
            "FROM shots ORDER BY id"
        )
    ]


async def test_an_override_changes_only_its_own_set_versions_shots(db: Database) -> None:
    lever = await _lever(db)
    other_shot = await add_shot(
        db,
        set_version_id=lever.other_version,
        profile_version_id=lever.version,
        device_id="000901",
    )
    rows = await _propose(lever, confirm=True)
    ramp = next(r for r in rows if r.kind == "measure" and r.phase == "ramp")
    service = SignatureService(db)
    override = await service.propose_override(
        set_version_id=lever.set_version,
        profile_version_id=lever.version,
        expectation_id=ramp.id,
        compare={"op": "<=", "value": 1.3},
        reason="a coarser bean",
        thread_id=None,
    )
    # Proposed: nothing moves.
    assert (await _row(db, lever.shot)).checks.badge == "ramp: early yield +4"
    await SignatureRepository(db).answer_override(override.id, confirm=True)

    mine, theirs = await _row(db, lever.shot), await _row(db, other_shot)
    assert mine.checks.badge == "decline: skipped +3"  # the ramp now holds at 1.3
    assert (
        theirs.checks.badge == "ramp: early yield +4"
    )  # the other version still reads the profile's
    # A rejected override changes nothing either.
    await SignatureRepository(db).answer_override(
        override.id, confirm=False
    )  # already answered: refused
    assert (await _row(db, lever.shot)).checks.badge == "decline: skipped +3"


async def test_the_curve_check_sort_follows_the_badge_red_amber_grey_then_none(
    db: Database,
) -> None:
    lever = await _lever(db)
    turbo_version = await _version(db, turbo_profile())
    turbo_set, _ = await make_set_versions(db, turbo_version, target=42.0)
    turbo = await add_shot(
        db,
        set_version_id=turbo_set,
        profile_version_id=turbo_version,
        slog=turbo_shot(),
        profile=turbo_profile(),
        device_id="000910",
    )
    no_scale = await add_shot(
        db,
        set_version_id=lever.set_version,
        profile_version_id=lever.version,
        slog=without_scale(lever_shot()),
        device_id="000911",
    )
    await SignatureService(db).propose(
        turbo_version,
        [
            ExpectationInput(
                tier="important", kind="expects_warning", warning="fast flow", phase="main"
            )
        ],
        reason="a turbo is fast on purpose",
    )
    await SignatureRepository(db).confirm_all(turbo_version)
    repo = ShotsRepository(db)

    before = await repo.list_shots(limit=10, sort=CHECK_SORT, descending=True)
    # No signature yet: the lever's amber fast flow in the ramp, the turbo's amber fast flow in
    # the main phase (which the turbo signature already marks expected: grey), the no-scale
    # lever's amber fast flow and skipped.
    by_id = {row.id: row for row in before.items}
    assert by_id[turbo].checks.badge == "main: fast flow"
    assert [w.severity for w in by_id[turbo].checks.entries] == ["grey"]
    order = [row.id for row in before.items]
    assert order.index(turbo) > order.index(lever.shot)  # grey after amber

    await _propose(lever, confirm=True)
    after = await repo.list_shots(limit=10, sort=CHECK_SORT, descending=True)
    assert after.items[0].id in (lever.shot, no_scale)
    assert [row.id for row in after.items][-1] == turbo
    assert (await repo.list_shots(limit=10, sort=CHECK_SORT, descending=False)).items[0].id == turbo


async def test_a_turbo_with_its_fast_flow_expected_shows_no_amber(db: Database) -> None:
    version = await _version(db, turbo_profile())
    set_version, _ = await make_set_versions(db, version, target=42.0)
    shot = await add_shot(
        db,
        set_version_id=set_version,
        profile_version_id=version,
        slog=turbo_shot(),
        profile=turbo_profile(),
    )
    assert (await _row(db, shot)).checks.entries[0].severity == "amber"

    await SignatureService(db).propose(
        version,
        [
            ExpectationInput(
                tier="important", kind="expects_warning", warning="fast flow", phase="main"
            )
        ],
        reason="r",
    )
    await SignatureRepository(db).confirm_all(version)

    row = await _row(db, shot)
    assert (row.checks.badge, [w.severity for w in row.checks.entries]) == (
        "main: fast flow",
        ["grey"],
    )
    document = await shot_fields(db, shot)
    assert document is not None
    assert [(c.status, c.color) for c in document.checks.items] == [("expected", "grey")]


async def test_the_fields_serve_the_ordered_list_with_value_and_state(db: Database) -> None:
    lever = await _lever(db)
    plain = await shot_fields(db, lever.shot)
    assert plain is not None
    assert (plain.signature.confirmed, plain.signature.text) == (0, "read without a signature")
    assert plain.signature.profile_version_id == lever.version
    await _propose(lever, confirm=True)

    document = await shot_fields(db, lever.shot)
    assert document is not None
    assert document.signature.text == "confirmed, 4 expectations"
    first = document.checks.items[0]
    assert (first.kind, first.tier, first.status, first.color, first.fault) == (
        "measure",
        "critical",
        "failed",
        "red",
        "early yield",
    )
    assert first.value == pytest.approx(117.2, abs=0.05) and first.held is False
    assert first.unit == "%" and first.phase == "ramp"
    # The effective limit, served beside the value, and how a person reads it.
    assert first.compare == {"op": "<=", "value": 0.15}
    assert (first.relative_to, first.limit_text) == ("target_yield", "at most 15 % of target")
    assert first.sentence.startswith("cup weight at the end of the ramp")
    assert [c.status for c in document.checks.items][-1] == "unchecked"
    assert document.checks.badge == "ramp: early yield +4"
    assert len(document.checks.entries) == 5
    assert [w.severity for w in document.checks.entries] == [
        "red",
        "red",
        "amber",
        "amber",
        "amber",
    ]


# ── unconfirmed never teaches ────────────────────────────────────────


async def test_nothing_proposed_reaches_a_check_a_render_or_a_field(db: Database) -> None:
    """A proposed (and a rejected) expectation is in no place a confirmed one would be."""
    lever = await _lever(db)
    [plain_facts] = await load_shots(db, [lever.shot])
    plain = {
        tier: render_shot(plain_facts, tier, default_tiers(), curve_points=60)
        for tier in ("base", "extended", "full")
    }
    plain_fields = (await shot_fields(db, lever.shot)).model_dump()  # type: ignore[union-attr]

    rows = await _propose(lever, confirm=False)
    await SignatureRepository(db).answer(rows[0].id, confirm=False, reject_reason="no")

    [facts] = await load_shots(db, [lever.shot])
    assert facts.signature_checks.state.confirmed == 0
    assert [c.badge for c in facts.signature_checks.checks if c.in_badge] == [
        "ramp: fast flow",
        "decline: skipped",
        "Shot: over target",
    ]
    for tier, text in plain.items():
        assert render_shot(facts, tier, default_tiers(), curve_points=60) == text  # type: ignore[arg-type]
        assert "early yield" not in text and "decline begins" not in text
    assert (await shot_fields(db, lever.shot)).model_dump() == plain_fields  # type: ignore[union-attr]
    assert (await _row(db, lever.shot)).checks.badge == "ramp: fast flow +2"


async def test_a_signature_shows_in_every_rendering_once_confirmed(db: Database) -> None:
    lever = await _lever(db)
    await _propose(lever, confirm=True)
    [facts] = await load_shots(db, [lever.shot])
    tiers = default_tiers()
    base = render_shot(facts, "base", tiers, curve_points=60).splitlines()
    assert base[1:3] == ["[Checks]", "signature: confirmed, 4 expectations"]
    assert base[3].startswith(
        "ramp: early yield (red, critical): cup weight at the end of the ramp"
    )
    assert "decline: skipped (red, critical): the decline begins; it never began." in base
    extended = render_shot(facts, "extended", tiers, curve_points=60)
    assert "pressure and flow fall together through the decline" in extended
    assert "checked by the review" in extended
    assert "pressure and flow fall together" not in "\n".join(base)


# ── absent sensors are zeros ─────────────────────────────────────────


async def test_a_shot_with_no_scale_is_not_measured_never_held_or_failed(db: Database) -> None:
    lever = await _lever(db)
    shot = await add_shot(
        db,
        set_version_id=lever.set_version,
        profile_version_id=lever.version,
        slog=without_scale(lever_shot()),
        device_id="000920",
    )
    await _propose(lever, confirm=True)

    document = await shot_fields(db, shot)
    assert document is not None
    cups = [c for c in document.checks.items if c.kind == "measure"]
    assert [(c.status, c.held, c.value) for c in cups] == [("unmeasured", None, None)] * 2
    assert all("scale" in (c.absent or "") for c in cups)
    # Nothing unmeasured is in the badge.
    assert all(c.kind != "measure" for c in document.checks.items if c.color is not None)


async def test_a_shot_with_no_pressure_sensor_cannot_fail_a_flow_or_pressure_expectation(
    db: Database,
) -> None:
    version = await _version(db, LEVER_PROFILE)
    set_version, _ = await make_set_versions(db, version)
    shot = await add_shot(
        db,
        set_version_id=set_version,
        profile_version_id=version,
        slog=without_pressure(lever_shot()),
        device_id="000921",
    )
    await db.execute(
        "UPDATE shots SET diagnostics_json = "
        "json_set(diagnostics_json, '$.has_pressure', json('false')) WHERE id = ?",
        (shot,),
    )
    await SignatureService(db).propose(
        version,
        [
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={
                    "channel": "pressure",
                    "op": "max",
                    "window": {"phase": "ramp"},
                    "compare": {"op": "<=", "value": 9.5},
                },
            ),
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={
                    "channel": "puck_flow",
                    "op": "max",
                    "window": {"phase": "ramp"},
                    "compare": {"op": "<=", "value": 3},
                },
            ),
        ],
        reason="r",
    )
    await SignatureRepository(db).confirm_all(version)

    document = await shot_fields(db, shot)
    assert document is not None
    measures = [c for c in document.checks.items if c.kind == "measure"]
    assert [c.status for c in measures] == ["unmeasured", "unmeasured"]
    assert all("pressure sensor" in (c.absent or "") for c in measures)


# ── the routes ───────────────────────────────────────────────────────


async def test_the_shot_routes_serve_the_same_checks(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    lever = await _lever(app.state.db)
    await _propose(lever, confirm=True)

    fields = (await client.get(f"/api/shots/{lever.shot}/fields")).json()["data"]
    listed = (await client.get("/api/shots?sort=review")).json()["data"]
    row = next(item for item in listed["items"] if item["id"] == lever.shot)

    assert fields["checks"]["badge"] == row["checks"]["badge"] == "ramp: early yield +4"
    assert fields["checks"]["entries"] == row["checks"]["entries"]
    assert fields["signature"] == {
        "profile_version_id": lever.version,
        "confirmed": 4,
        "text": "confirmed, 4 expectations",
    }
    first = fields["checks"]["items"][0]
    assert first["fault"] == "early yield" and first["color"] == "red"
    assert json.dumps(fields["checks"]["entries"][0]).count("severity") == 1


async def test_a_signature_of_only_reached_and_expects_warning_reads_through_the_database(
    db: Database,
) -> None:
    lever = await _lever(db)
    await _propose(
        lever,
        confirm=True,
        items=[
            ExpectationInput(tier="critical", kind="reached", phase="ramp"),
            ExpectationInput(
                tier="important", kind="expects_warning", warning="skipped", phase="decline"
            ),
        ],
    )

    row = await _row(db, lever.shot)
    # The ramp began (held, so nothing on the badge), and the skipped decline is by design: grey,
    # after the warnings nothing marks as expected.
    assert [(w.fault, w.severity, w.status) for w in row.checks.entries] == [
        ("fast flow", "amber", "warning"),
        ("over target", "amber", "warning"),
        ("skipped", "grey", "expected"),
    ]
    assert row.checks.badge == "ramp: fast flow +2"
    document = await shot_fields(db, lever.shot)
    assert document is not None
    reached = next(c for c in document.checks.items if c.kind == "reached")
    assert (reached.status, reached.held, reached.value) == ("held", True, None)
