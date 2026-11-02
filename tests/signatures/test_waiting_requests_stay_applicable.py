"""A waiting request is always one Approve could apply.

The invariant, after every step of a randomised run over the real service and repositories: no
waiting request has a problem the guard would refuse it for, and no two waiting requests name one
row. The steps are the ways a row changes under a request: another request asked or approved or
declined, the person rejecting, restoring or moving a row, an override proposed, rejected or
restored, and a tool adding a row that duplicates a waiting replacement.

A fixed set of seeds, a few seconds. The cases that found the holes are written out beside it.
"""

from __future__ import annotations

import copy
import random
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput, SignatureRefused
from gaggiclanker.domain.signature_changes import rows_named
from gaggiclanker.signatures.change_requests import ChangeIn, SignatureRequestService
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE
from tests.signatures.helpers import assert_a_sentence, make_recipe_versions, make_thread
from tests.signatures.test_carry import _version
from tests.signatures.test_change_requests import Regio, regio, want

__all__ = ["regio"]

REASON = "Because the floor was written under an older setpoint of the profile."
TIERS = ("critical", "important", "context")


def temperature(op: str, compare: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "channel": "temperature",
        "op": op,
        "window": {"phase": "soak"},
        "compare": compare,
        **extra,
    }


#: Limits to start from and to ask for; the first four are in force to begin with.
POOL: list[tuple[str, dict[str, Any]]] = [
    ("important", temperature("min", {"op": ">=", "value": 93})),
    ("critical", temperature("min", {"op": ">=", "value": 91})),
    ("context", temperature("min", {"op": ">=", "value": -2}, offset_from="profile_temperature")),
    ("important", temperature("max", {"op": "<=", "value": 95})),
    ("important", temperature("mean", {"op": "between", "low": 91.5, "high": 94})),
    ("critical", temperature("min", {"op": ">=", "value": 92})),
    ("important", temperature("min", {"op": ">=", "value": 91.5})),
    ("context", temperature("mean", {"op": "between", "low": 90, "high": 95})),
]
INITIAL = 4


def expectation(index: int, tier: str | None = None) -> ExpectationInput:
    pooled_tier, expression = POOL[index]
    return ExpectationInput(
        tier=tier or pooled_tier,  # type: ignore[arg-type]
        kind="measure",
        phase="soak",
        expression=copy.deepcopy(expression),
    )


class World:
    def __init__(self, db: Database, rng: random.Random) -> None:
        self.db = db
        self.rng = rng
        self.repo = SignatureRepository(db)
        self.service = SignatureRequestService(db)
        self.log: list[str] = []

    async def setup(self) -> None:
        self.version = await _version(self.db, LEVER_PROFILE)
        self.rv1, self.rv2 = await make_recipe_versions(self.db, self.version)
        recipe_id = await self.repo.recipe_of_version(self.rv1)
        assert recipe_id is not None
        self.thread = await make_thread(self.db, recipe_id)
        await SignatureService(self.db).propose(
            self.version, [expectation(i) for i in range(INITIAL)], reason="What it is for."
        )

    async def invariant(self, step: str) -> None:
        # Whatever staled a request, the card says why in one whole sentence.
        for stale in await self.db.fetch_all(
            "SELECT stale_reason FROM signature_change_requests WHERE status = 'stale'"
        ):
            assert_a_sentence(stale["stale_reason"])
        named: dict[Any, int] = {}
        for request in await self.service.requests.waiting_without_drafts():
            assert request.changes is not None
            problems = await self.service.problems(
                request.profile_version_id, request.changes.changes
            )
            assert problems == [], (
                step,
                "waiting but not applicable",
                request.id,
                problems,
                self.log[-6:],
            )
            for change in request.changes.changes:
                for key in rows_named(change):
                    assert key not in named, (step, "two waiting requests name", key, request.id)
                    named[key] = request.id

    async def rows(self) -> list[Any]:
        return await self.repo.for_version(self.version)

    async def overrides(self) -> list[Any]:
        return list(
            await self.db.fetch_all("SELECT id, status FROM recipe_version_signature_overrides")
        )

    async def ask(self) -> None:
        rows = await self.rows()
        overrides = await self.overrides()
        changes: list[ChangeIn] = []
        rejected = [o for o in overrides if o["status"] == "rejected"]
        for _ in range(self.rng.randint(1, 3)):
            kind = self.rng.choice(["replace", "replace", "retire", "tier", "put_back"])
            if rejected and self.rng.random() < 0.4:
                changes.append(
                    want(
                        "put_back_override",
                        reason=REASON,
                        override_id=int(self.rng.choice(rejected)["id"]),
                    )
                )
                continue
            if not rows:
                continue
            row = self.rng.choice(rows)
            fields: dict[str, Any] = {"reason": REASON, "expectation_id": row.id}
            if kind == "replace":
                fields["expectation"] = expectation(
                    self.rng.randrange(len(POOL)), self.rng.choice(TIERS)
                )
            elif kind == "tier":
                fields["tier"] = self.rng.choice(TIERS)
            changes.append(want(kind, **fields))
        if not changes:
            return
        try:
            await self.service.request(
                profile_version_id=self.version,
                changes=changes,
                reason="The limits are wrong now.",
                thread_id=self.thread,
                recipe_version_id=self.rv1,
            )
            self.log.append(f"ask {[c.kind for c in changes]}")
        except SignatureRefused:
            self.log.append("ask refused")

    async def ask_put_back(self) -> None:
        """Only a put back of a rejected override: the request the other steps rarely make."""
        rejected = [o for o in await self.overrides() if o["status"] == "rejected"]
        if not rejected:
            return
        try:
            await self.service.request(
                profile_version_id=self.version,
                changes=[
                    want("put_back_override", reason=REASON, override_id=int(rejected[0]["id"]))
                ],
                reason="The limit was fine.",
                thread_id=self.thread,
                recipe_version_id=self.rv1,
            )
            self.log.append("ask put_back_override")
        except SignatureRefused:
            self.log.append("ask put_back_override refused")

    async def person_on_an_overrides_row(self) -> None:
        """The person answers the expectation an override is a limit for."""
        overrides = await self.db.fetch_all(
            "SELECT expectation_id FROM recipe_version_signature_overrides"
        )
        if not overrides:
            return
        target = int(self.rng.choice(overrides)["expectation_id"])
        if self.rng.random() < 0.6:
            await self.repo.reject(target, reason="too tight")
        else:
            await self.repo.set_tier(target, self.rng.choice(TIERS))  # type: ignore[arg-type]
        self.log.append(f"person on #{target}")

    async def answer(self) -> None:
        waiting = await self.service.requests.waiting_without_drafts()
        if not waiting:
            return
        request = self.rng.choice(waiting)
        if self.rng.random() < 0.7:
            result = await self.service.approve(request.id)
            self.log.append(f"approve #{request.id} {result.refused}")
        else:
            await self.service.decline(request.id, "keep it")
            self.log.append(f"decline #{request.id}")

    async def person(self) -> None:
        rows = await self.rows()
        if not rows:
            return
        row = self.rng.choice(rows)
        verb = self.rng.choice(["reject", "restore", "tier"])
        if verb == "reject":
            await self.repo.reject(row.id, reason="too tight")
        elif verb == "restore":
            await self.repo.restore(row.id)
        else:
            await self.repo.set_tier(row.id, self.rng.choice(TIERS))  # type: ignore[arg-type]
        self.log.append(f"person {verb} #{row.id}")

    async def override(self) -> None:
        overrides = await self.overrides()
        if overrides and self.rng.random() < 0.6:
            chosen = self.rng.choice(overrides)
            if self.rng.random() < 0.65:
                await self.repo.reject_override(int(chosen["id"]), reason="no")
            else:
                await self.repo.restore_override(int(chosen["id"]))
            self.log.append(f"override {chosen['id']} answered")
            return
        confirmed = [r for r in await self.rows() if r.status == "confirmed"]
        measures = [
            r
            for r in confirmed
            if r.expression is not None
            and r.expression.compare is not None
            and r.expression.compare.op != "between"
        ]
        if not measures:
            return
        row = self.rng.choice(measures)
        assert row.expression is not None and row.expression.compare is not None
        try:
            await SignatureService(self.db).propose_override(
                recipe_version_id=self.rv1,
                profile_version_id=self.version,
                expectation_id=row.id,
                compare={
                    "op": row.expression.compare.op,
                    "value": (row.expression.compare.value or 0) + self.rng.choice([-0.5, 0.3]),
                },
                reason="This version's beans sag a bit more than the profile assumes.",
                thread_id=self.thread,
            )
            self.log.append(f"override proposed on #{row.id}")
        except SignatureRefused:
            self.log.append("override refused")

    async def propose(self) -> None:
        waiting = await self.service.requests.waiting_without_drafts()
        replaces = [
            c for r in waiting if r.changes for c in r.changes.changes if c.kind == "replace"
        ]
        if replaces and self.rng.random() < 0.7:
            new = self.rng.choice(replaces).expectation
            assert new.expression is not None
            item = ExpectationInput(
                tier=new.tier,
                kind=new.kind,
                phase=new.phase,
                expression=new.expression.model_dump(mode="json", exclude_none=True),
            )
        else:
            item = expectation(self.rng.randrange(len(POOL)))
        try:
            await SignatureService(self.db).propose(
                self.version, [item], reason="What it is for.", thread_id=self.thread
            )
            self.log.append("propose added")
        except SignatureRefused:
            self.log.append("propose refused")

    async def step(self) -> None:
        action = self.rng.choices(
            [
                self.ask,
                self.ask_put_back,
                self.person_on_an_overrides_row,
                self.answer,
                self.person,
                self.override,
                self.propose,
            ],
            weights=[20, 14, 8, 12, 12, 16, 6],
        )[0]
        await action()
        await self.invariant(action.__name__)


@pytest.mark.parametrize("seed", range(40))
async def test_a_waiting_request_is_always_one_approve_could_apply(db: Database, seed: int) -> None:
    world = World(db, random.Random(seed))  # noqa: S311 - seeded, nothing secret
    await world.setup()
    for _ in range(30):
        await world.step()


# ── the cases that found the holes ───────────────────────────────────


async def _rejected_override(regio: Regio, expectation_id: int, value: float) -> int:
    override = await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=expectation_id,
        compare={"op": ">=", "value": value},
        reason="This version's beans sag a bit more than the profile assumes.",
        thread_id=regio.thread,
    )
    assert (await regio.repo.reject_override(override.id, reason="no")).refused is None
    return override.id


async def _state(regio: Regio, request_id: int) -> tuple[str, list[str]]:
    row = await regio.service.requests.get(request_id)
    assert row is not None and row.changes is not None
    return row.status, await regio.service.problems(row.profile_version_id, row.changes.changes)


async def test_the_person_rejecting_the_overrides_expectation_stales_the_put_back(
    regio: Regio,
) -> None:
    override = await _rejected_override(regio, regio.high.id, 92.0)
    request = await regio.ask(want("put_back_override", override_id=override))
    assert await _state(regio, request.id) == ("waiting", [])
    await regio.repo.reject(regio.high.id, reason="too tight")
    status, _ = await _state(regio, request.id)
    assert status == "stale"
    row = await regio.service.requests.get(request.id)
    assert row is not None and row.stale_reason.startswith("You rejected")


async def test_another_override_coming_into_force_stales_the_put_back_by_any_path(
    regio: Regio,
) -> None:
    first = await _rejected_override(regio, regio.high.id, 92.0)
    second = await _rejected_override(regio, regio.low.id, 90.5)
    # By a tool: propose_signature_override puts one in force on the same Recipe version.
    request = await regio.ask(want("put_back_override", override_id=first))
    await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.low.id,
        compare={"op": ">=", "value": 90.4},
        reason="This version's beans sag a bit more than the profile assumes.",
        thread_id=regio.thread,
    )
    status, problems = await _state(regio, request.id)
    assert status == "stale" and problems != []
    stale = await regio.service.requests.get(request.id)
    assert stale is not None and "Another different limit" in stale.stale_reason
    # By the person's Restore of a different one.
    await regio.repo.reject_override(
        (
            await regio.repo.overrides_for_recipe_version(
                regio.recipe_version, statuses=["confirmed"]
            )
        )[0].id,
        reason="no",
    )
    again = await regio.ask(want("put_back_override", override_id=first))
    assert (await regio.repo.restore_override(second)).refused is None
    assert (await _state(regio, again.id))[0] == "stale"


async def test_another_request_retiring_the_overrides_expectation_stales_the_put_back(
    regio: Regio,
) -> None:
    override = await _rejected_override(regio, regio.high.id, 92.0)
    put_back = await regio.ask(want("put_back_override", override_id=override))
    # A newer request naming the same expectation replaces the older one.
    other = await regio.ask(want("retire", expectation_id=regio.high.id))
    assert (await _state(regio, put_back.id))[0] == "stale"
    assert (await regio.service.approve(other.id)).refused is None


async def test_approving_a_request_that_restores_an_override_stales_the_others_on_that_version(
    regio: Regio,
) -> None:
    first = await _rejected_override(regio, regio.high.id, 92.0)
    second = await _rejected_override(regio, regio.low.id, 90.5)
    # Two put-backs cannot both wait on the same version... they name different rows, so they do.
    one = await regio.ask(want("put_back_override", override_id=first))
    two = await regio.ask(want("put_back_override", override_id=second))
    assert (await _state(regio, one.id))[0] == "waiting"
    assert (await regio.service.approve(two.id)).refused is None
    status, problems = await _state(regio, one.id)
    assert status == "stale" and problems != []


async def test_an_override_coming_into_force_on_another_recipe_version_leaves_the_request(
    regio: Regio,
) -> None:
    """A version has one override at a time, but another version's does not matter to it: the
    first half is the same override on this version (stale), the second on the Recipe's other
    version (still waiting)."""
    override = await _rejected_override(regio, regio.high.id, 92.0)
    request = await regio.ask(want("put_back_override", override_id=override))
    other = await regio.db.fetch_value(
        "SELECT id FROM recipe_versions WHERE recipe_id = ? AND id != ?",
        (regio.recipe_id, regio.recipe_version),
    )
    await SignatureService(regio.db).propose_override(
        recipe_version_id=int(other),
        profile_version_id=regio.version,
        expectation_id=regio.low.id,
        compare={"op": ">=", "value": 90.5},
        reason="This version's beans sag a bit more than the profile assumes.",
        thread_id=regio.thread,
    )
    assert await _state(regio, request.id) == ("waiting", [])
    await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.low.id,
        compare={"op": ">=", "value": 90.4},
        reason="This version's beans sag a bit more than the profile assumes.",
        thread_id=regio.thread,
    )
    assert (await _state(regio, request.id))[0] == "stale"
