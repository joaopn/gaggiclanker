"""A draft says which carried expectations its patch touches, and can revise them.

The changes the agent sends with a draft name the base version's expectations. They wait with
the draft: nothing changes on the draft's version, or ever on the base's, until the person
approves the draft, and declining the draft takes them with it. The base is the machine's
"9 Bar Espresso" (93 °C, one pump phase at 9 bar), so every number is the fixture's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.signature_requests import SignatureRequestsRepository
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.signature import ExpectationInput, SignatureRefused
from gaggiclanker.domain.signature_changes import ReplaceChange
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.signatures.change_requests import ChangeIn, DraftSignatureChanges
from gaggiclanker.signatures.service import SignatureService
from tests.drafts.conftest import BASE_LABEL, base_profile, base_version_id
from tests.drafts.test_board import adopted, put
from tests.signatures.helpers import assert_a_sentence, make_thread

__all__ = ["adopted"]

PRESSURE = {
    "channel": "pressure",
    "op": "mean",
    "window": {"phase": "Pump"},
    "compare": {"op": ">=", "value": 8.5},
}
TEMPERATURE = {
    "channel": "temperature",
    "op": "min",
    "window": {"phase": "Pump"},
    "compare": {"op": ">=", "value": 91},
}
FOLLOWING = {
    **TEMPERATURE,
    "offset_from": "profile_temperature",
    "compare": {"op": ">=", "value": -2},
}
REASON = "The pressure target is lower, so the floor under it should be too."


class Setup:
    def __init__(self, app: FastAPI, client: httpx.AsyncClient, base: int, thread: int) -> None:
        self.app, self.client, self.base, self.thread = app, client, base, thread
        self.db = app.state.db
        self.repo = SignatureRepository(self.db)
        self.requests = SignatureRequestsRepository(self.db)

    @property
    def proposals(self) -> DraftProposals:
        return DraftProposals(self.db, self.app.state.settings_service)

    async def base_rows(self) -> list[ExpectationRow]:
        return await self.repo.for_version(self.base)

    async def draft(
        self, *changes: ChangeIn, pressure: float = 7.5, temperature: float | None = None
    ) -> tuple[ProfileDraftsRepository, int, DraftSignatureChanges]:
        document = (await base_profile(self.app, BASE_LABEL)).model_dump(
            mode="json", exclude={"annotations", "id"}
        )
        document["phases"][0]["pump"] = {"target": "pressure", "pressure": pressure, "flow": 0}
        if temperature is not None:
            document["temperature"] = temperature
        carried = DraftSignatureChanges(
            changes=tuple(changes), reason=REASON, thread_id=self.thread
        )
        row = await self.proposals.create_manual(
            base_version_id=self.base,
            document=document,
            change_summary="A lower pressure.",
            signature_changes=carried if changes else None,
        )
        return ProfileDraftsRepository(self.db), row.id, carried


@pytest.fixture
async def setup(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> tuple[Setup, list[ExpectationRow]]:
    app, client, _ = adopted
    base = await base_version_id(app, BASE_LABEL)
    thread = await make_thread(app.state.db)
    rows = await SignatureService(app.state.db).propose(
        base,
        [
            ExpectationInput(tier="important", kind="measure", phase="Pump", expression=PRESSURE),
            ExpectationInput(tier="critical", kind="measure", phase="Pump", expression=TEMPERATURE),
            ExpectationInput(tier="context", kind="measure", phase="Pump", expression=FOLLOWING),
        ],
        reason="What the profile is for.",
    )
    return Setup(app, client, base, thread), rows


def replace_pressure(row: ExpectationRow, floor: float = 7.0) -> ChangeIn:
    return ChangeIn(
        kind="replace",
        reason=REASON,
        expectation_id=row.id,
        expectation=ExpectationInput(
            tier="important",
            kind="measure",
            phase="Pump",
            expression={**PRESSURE, "compare": {"op": ">=", "value": floor}},
        ),
    )


async def test_a_draft_flags_what_its_patch_touches_and_stores_the_flags_with_it(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    drafts, draft_id, _ = await s.draft(pressure=7.5)
    draft = await drafts.get(draft_id)
    assert draft is not None
    flags = [a.model_dump() for a in draft.affected_expectations]
    assert flags == [
        {
            "expectation_id": rows[0].id,
            "sentence": "mean of pressure over the Pump, at least 8.5 bar",
            "cause": "the Pump pressure target changed from 9 bar to 7.5 bar",
            "revised": False,
            "revision": None,
        }
    ]
    # The route the card already reads serves them.
    served = (await s.client.get(f"/api/profile-drafts/{draft_id}")).json()["data"]["draft"]
    assert served["affected_expectations"][0]["expectation_id"] == rows[0].id
    assert served["signature_request"] is None


async def test_a_temperature_change_flags_the_absolute_floor_and_never_the_offset_one(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    drafts, draft_id, _ = await s.draft(pressure=9, temperature=91.0)
    draft = await drafts.get(draft_id)
    assert draft is not None
    assert [a.expectation_id for a in draft.affected_expectations] == [rows[1].id]
    assert draft.affected_expectations[0].cause == "the temperature changed from 93 to 91 °C"


async def test_the_changes_wait_with_the_draft_and_the_base_is_never_touched(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    before = [r.model_dump_json() for r in await s.base_rows()]
    drafts, draft_id, carried = await s.draft(replace_pressure(rows[0]))
    draft = await drafts.get(draft_id)
    assert draft is not None and draft.draft_version_id is not None

    request = await s.requests.for_draft(draft_id)
    assert request is not None and request.status == "waiting" and request.draft_id == draft_id
    assert carried.request_id == request.id and request.thread_id == s.thread
    # It names the draft's carried copy, not the base row.
    copy = next(
        r
        for r in await s.repo.for_version(draft.draft_version_id)
        if r.carried_from_id == rows[0].id
    )
    assert request.changes is not None and request.changes.expectation_ids() == [copy.id]
    # The flag says it is revised, and how.
    assert draft.affected_expectations[0].revised is True
    stored = request.changes.changes[0]
    assert isinstance(stored, ReplaceChange)
    assert (
        draft.affected_expectations[0].revision == "mean of pressure over the Pump, at least 7 bar"
    )
    assert stored.expectation.sentence == "mean of pressure over the Pump, at least 7"
    # Nothing changed on the draft's version, nor on the base.
    assert all(r.status == "confirmed" for r in await s.repo.for_version(draft.draft_version_id))
    assert [r.model_dump_json() for r in await s.base_rows()] == before
    # The card reads it from the route it already reads.
    served = (await s.client.get(f"/api/profile-drafts/{draft_id}")).json()["data"]["draft"]
    assert served["signature_request"]["status"] == "waiting"
    assert served["signature_request"]["changes"][0]["kind"] == "replace"
    assert (
        served["signature_request"]["changes"][0]["old_sentence"]
        == "mean of pressure over the Pump, at least 8.5 bar"
    )


async def test_approving_the_draft_applies_its_changes_to_the_version_it_became(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    before = [r.model_dump_json() for r in await s.base_rows()]
    _, draft_id, _ = await s.draft(replace_pressure(rows[0]))

    row = await put(s.client, {"id": draft_id})

    request = await s.requests.for_draft(draft_id)
    assert request is not None and request.status == "approved" and request.written
    final = await s.repo.for_version(row["current_version_id"])
    by_status = {r.status for r in final}
    assert by_status == {"confirmed", "rejected"}
    rejected = next(r for r in final if r.status == "rejected")
    assert rejected.reject_reason.startswith("Replaced at the agent's request: ")
    written = next(r for r in final if r.id == request.written[0])
    assert written.status == "confirmed" and written.proposed_by_draft_id == draft_id
    assert written.proposed_by_thread_id == s.thread
    # The base version's rows are exactly what they were.
    assert [r.model_dump_json() for r in await s.base_rows()] == before
    served = (await s.client.get(f"/api/profile-drafts/{draft_id}")).json()["data"]["draft"]
    assert served["signature_request"]["status"] == "approved"


async def test_a_change_that_no_longer_applies_makes_the_request_stale_and_the_approval_goes_on(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    drafts, draft_id, _ = await s.draft(
        replace_pressure(rows[0]),
        ChangeIn(kind="retire", reason=REASON, expectation_id=rows[1].id),
    )
    draft = await drafts.get(draft_id)
    assert draft is not None and draft.draft_version_id is not None
    copy = next(
        r
        for r in await s.repo.for_version(draft.draft_version_id)
        if r.carried_from_id == rows[1].id
    )
    # The person rejects the draft's copy on the Profiles page before approving the draft.
    await s.repo.reject(copy.id, reason="no")

    row = await put(s.client, {"id": draft_id})

    request = await s.requests.for_draft(draft_id)
    assert request is not None and request.status == "stale"
    assert "was rejected, so it is not in force" in request.stale_reason
    assert_a_sentence(request.stale_reason)
    assert (await drafts.get(draft_id)).status == "approved"  # type: ignore[union-attr]
    final = await s.repo.for_version(row["current_version_id"])
    # Nothing of the request was applied: the first change is not applied without the second.
    assert [r.status for r in final if r.carried_from_id == rows[0].id] == ["confirmed"]
    served = (await s.client.get(f"/api/profile-drafts/{draft_id}")).json()["data"]["draft"]
    assert served["signature_request"]["status"] == "stale"
    assert served["signature_request"]["stale_reason"] == request.stale_reason


async def test_declining_the_draft_makes_its_request_stale(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    _, draft_id, _ = await s.draft(replace_pressure(rows[0]))
    assert (await s.client.post(f"/api/profile-drafts/{draft_id}/discard")).status_code == 200
    request = await s.requests.for_draft(draft_id)
    assert request is not None
    assert (request.status, request.stale_reason) == ("stale", "The draft was declined.")
    # The routes of the chat do not answer it: it is the draft's.
    assert (await s.client.post(f"/api/signature-requests/{request.id}/approve")).status_code == 409


@pytest.mark.parametrize(
    ("how", "said"),
    [
        ("discard_unsent", "The draft was declined."),
        ("supersede", "A newer draft replaced it."),
    ],
)
async def test_a_draft_turned_down_or_overtaken_any_other_way_takes_its_request_with_it(
    setup: tuple[Setup, list[ExpectationRow]], how: str, said: str
) -> None:
    """The discard route is one way a draft ends. A Recipe's proposal declined, a pin changed or
    a refinement ends one inside the transaction that does it, and the request goes with it."""
    s, rows = setup
    drafts, draft_id, _ = await s.draft(replace_pressure(rows[0]))
    assert (await s.requests.for_draft(draft_id)).status == "waiting"  # type: ignore[union-attr]
    if how == "discard_unsent":
        assert await drafts.discard_unsent([draft_id]) == 1
    else:
        await drafts.supersede(draft_id)
    request = await s.requests.for_draft(draft_id)
    assert request is not None and (request.status, request.stale_reason) == ("stale", said)
    assert_a_sentence(request.stale_reason)
    # Nothing was applied: the draft's version and the base keep what they carried.
    assert {r.status for r in await s.base_rows()} == {"confirmed"}


async def test_a_drafts_request_has_no_preview_the_draft_is_answered_with_its_numbers(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    from gaggiclanker.signatures.change_requests import SignatureRequestService

    s, rows = setup
    _, draft_id, _ = await s.draft(replace_pressure(rows[0]))
    request = await s.requests.for_draft(draft_id)
    assert request is not None and request.status == "waiting"
    view = await SignatureRequestService(s.db).view(request)
    assert view.draft_id == draft_id
    assert [c.preview for c in view.changes] == [None]
    assert [c.kind for c in view.changes] == ["replace"]


async def test_a_drafts_changes_are_checked_against_the_phases_of_the_draft_not_the_base(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    """The draft adds a phase the base does not have: a replacement may name it. A phase neither
    has is refused."""
    s, rows = setup

    async def with_finish(change: ChangeIn) -> object:
        document = (await base_profile(s.app, BASE_LABEL)).model_dump(
            mode="json", exclude={"annotations", "id"}
        )
        extra = {**document["phases"][0], "name": "Finish"}
        document["phases"].append(extra)
        return await s.proposals.create_manual(
            base_version_id=s.base,
            document=document,
            change_summary="A finishing phase.",
            signature_changes=DraftSignatureChanges(
                changes=(change,), reason=REASON, thread_id=s.thread
            ),
        )

    def into(phase: str) -> ChangeIn:
        return ChangeIn(
            kind="replace",
            reason=REASON,
            expectation_id=rows[0].id,
            expectation=ExpectationInput(
                tier="important",
                kind="measure",
                phase=phase,
                expression={**PRESSURE, "window": {"phase": phase}},
            ),
        )

    assert await with_finish(into("Finish")) is not None
    with pytest.raises(SignatureRefused) as refused:
        await with_finish(into("Nowhere"))
    assert "Nowhere" in str(refused.value)
    # Neither draft touched the base.
    assert {r.status for r in await s.base_rows()} == {"confirmed"}


async def test_a_draft_s_request_is_answered_by_its_draft_not_by_the_chat_routes(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    _, draft_id, _ = await s.draft(replace_pressure(rows[0]))
    request = await s.requests.for_draft(draft_id)
    assert request is not None
    for verb in ("approve", "decline"):
        response = await s.client.post(f"/api/signature-requests/{request.id}/{verb}", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SIGNATURE_REQUEST_IS_A_DRAFTS"
    assert (await s.requests.for_draft(draft_id)).status == "waiting"  # type: ignore[union-attr]


async def test_a_refusal_names_the_problem_and_leaves_no_draft_behind(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    from gaggiclanker.domain.signature import SignatureRefused

    s, rows = setup
    await s.repo.reject(rows[2].id, reason="no")
    count = await s.db.fetch_value("SELECT COUNT(*) FROM profile_drafts")
    with pytest.raises(SignatureRefused, match="does not carry expectation"):
        await s.draft(ChangeIn(kind="retire", reason=REASON, expectation_id=rows[2].id))
    with pytest.raises(SignatureRefused, match="cannot put back an override"):
        await s.draft(ChangeIn(kind="put_back_override", reason=REASON, override_id=1))
    assert await s.db.fetch_value("SELECT COUNT(*) FROM profile_drafts") == count


async def test_the_draft_and_its_request_are_one_write(
    setup: tuple[Setup, list[ExpectationRow]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaggiclanker.db.repos.signature_requests import SignatureRequestsRepository as Repo

    s, rows = setup
    count = await s.db.fetch_value("SELECT COUNT(*) FROM profile_drafts")

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("the request could not be written")

    monkeypatch.setattr(Repo, "create", boom)
    with pytest.raises(RuntimeError):
        await s.draft(replace_pressure(rows[0]))
    assert await s.db.fetch_value("SELECT COUNT(*) FROM profile_drafts") == count
    assert await s.db.fetch_value("SELECT COUNT(*) FROM signature_change_requests") == 0


async def test_the_changes_are_applied_after_the_carry_not_before_it(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    """The version a draft becomes may be another than the one it was stored on (the profile
    takes a copy, and the copy carries the signature). The request names the first version's
    rows and is applied to the second's, found through ``carried_from_id``: applying it before
    the carry finds nothing there."""
    s, rows = setup
    drafts, draft_id, _ = await s.draft(replace_pressure(rows[0]))
    draft = await drafts.get(draft_id)
    assert draft is not None and draft.draft_version_id is not None
    service = SignatureService(s.db)
    other = await _another_version(s)
    # Before the carry the other version has none of the rows: the request cannot be applied.
    assert await s.requests.for_draft(draft_id) is not None
    early = await s.proposals.signature_requests.apply_for_draft(draft_id, other)
    assert early is not None and early.status == "stale"

    # The same request, applied after the carry.
    _, second_id, _ = await s.draft(replace_pressure(rows[0], 7.2), pressure=7.4)
    second = await drafts.get(second_id)
    assert second is not None and second.draft_version_id is not None
    await service.carry(second.draft_version_id, other)
    late = await s.proposals.signature_requests.apply_for_draft(second_id, other)
    assert late is not None and late.status == "approved"
    final = await s.repo.for_version(other)
    assert sorted(r.status for r in final if r.carried_from_id is not None) == [
        "confirmed",
        "confirmed",
        "rejected",
    ]
    # The draft's own version is untouched.
    assert {r.status for r in await s.repo.for_version(second.draft_version_id)} == {"confirmed"}


async def _another_version(s: Setup) -> int:
    from gaggiclanker.db.repos.profiles import ProfilesRepository
    from gaggiclanker.domain.models import Profile

    profile = await base_profile(s.app, BASE_LABEL)
    document = profile.model_dump(mode="json", exclude={"annotations", "id"})
    document["phases"][0]["duration"] = 29
    version, _ = await ProfilesRepository(s.db).ensure_version(
        Profile.model_validate(document), owner=None
    )
    return version.id


async def _revive(
    s: Setup,
    rows: list[ExpectationRow],
    change: ChangeIn,
    *,
    before_the_put: Callable[[int, list[ExpectationRow]], Awaitable[None]] | None = None,
) -> tuple[int, list[ExpectationRow], ExpectationRow | None, list[ExpectationRow]]:
    """The real path through the board's put: a profile deleted by an earlier version is revived
    by a new-profile draft of the same name and brew, so the put attaches the version that
    profile already had and carries the signature onto it. That version holds the original of the
    row the draft's copy was made from, and the carry puts a copy beside it.

    Returns the version the put ended on, its rows, the request's written row (if any), and the
    rows the version held before the second put."""
    from tests.drafts.helpers import tombstone
    from tests.drafts.test_board import pull

    async def new_profile_draft(**extra: object) -> int:
        document = (await base_profile(s.app, BASE_LABEL)).model_dump(
            mode="json", exclude={"annotations", "id"}
        )
        document["phases"][0]["pump"] = {"target": "pressure", "pressure": 7.5, "flow": 0}
        row = await s.proposals.create_manual(
            base_version_id=s.base,
            document=document,
            change_summary="A lower pressure.",
            new_profile_name="Soft Bloom",
            **extra,  # type: ignore[arg-type]
        )
        return row.id

    first = await put(s.client, {"id": await new_profile_draft()})
    revived_version = first["current_version_id"]
    originals = await s.repo.for_version(revived_version)
    assert len([r for r in originals if r.status == "confirmed"]) == len(rows)
    await pull(s.app)
    assert (
        await s.client.put(f"/api/profile-board/{first['id']}/on-machine", json={"on": False})
    ).is_success
    await tombstone(s.client, first["id"])
    if before_the_put is not None:
        await before_the_put(revived_version, originals)

    carried = DraftSignatureChanges(changes=(change,), reason=REASON, thread_id=s.thread)
    second = await new_profile_draft(signature_changes=carried)
    request = await s.requests.for_draft(second)
    assert request is not None and request.status == "waiting"
    answer = await s.client.post("/api/profile-board", json={"draft_id": second})
    assert answer.status_code == 201, answer.text
    final = answer.json()["data"]["current_version_id"]
    # The put attached the version the profile already had, not the draft's own.
    assert final == revived_version
    request = await s.requests.for_draft(second)
    assert request is not None
    on_final = await s.repo.for_version(final)
    written = next((r for r in on_final if r.id in request.written), None)
    return final, on_final, written, originals


async def test_a_put_that_revives_a_profile_applies_the_changes_to_every_row_that_says_it(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    """The person approved "replace this", so neither the original nor the copy stays."""
    s, rows = setup
    _, on_final, written, _ = await _revive(s, rows, replace_pressure(rows[0]))

    request = await s.requests.for_draft(
        int(await s.db.fetch_value("SELECT MAX(id) FROM profile_drafts"))
    )
    assert request is not None and request.status == "approved"
    pressure = [r for r in on_final if r.sentence == rows[0].sentence]
    # The original and the copy the carry put beside it: both said "at least 8.5"; neither stays.
    assert len(pressure) == 2
    assert {r.status for r in pressure} == {"rejected"}
    assert all(r.reject_reason.startswith("Replaced at the agent's request: ") for r in pressure)
    assert written is not None and written.status == "confirmed"
    # Nothing else was touched.
    others = [r for r in on_final if r.sentence != rows[0].sentence and r.id != written.id]
    assert {r.status for r in others} == {"confirmed"}


async def test_a_twin_the_person_rejected_is_left_alone_and_keeps_its_reason(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    """Only rows in force say it: a twin the person rejected themselves is not "replaced", and
    its reason stays theirs. First half, the same revival with the twin in force (above)."""
    s, rows = setup

    async def reject_the_original(version: int, originals: list[ExpectationRow]) -> None:
        mine = next(r for r in originals if r.sentence == rows[1].sentence)
        await s.repo.reject(mine.id, reason="Mine, not the agent's.")

    _, on_final, _, _ = await _revive(
        s, rows, replace_pressure(rows[1], 91.5), before_the_put=reject_the_original
    )
    same = [r for r in on_final if r.sentence == rows[1].sentence]
    assert sorted((r.status, r.reject_reason.split(":")[0]) for r in same) == [
        ("rejected", "Mine, not the agent's."),
        ("rejected", "Replaced at the agent's request"),
    ]


async def test_a_replacement_that_only_moves_the_tier_is_not_a_duplicate_of_its_twin(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    """The replacement says what the rows it takes out say, with another tier. The twin goes too,
    so it is not "already in force"; the request is applied, not staled."""
    s, rows = setup
    change = ChangeIn(
        kind="replace",
        reason=REASON,
        expectation_id=rows[0].id,
        expectation=ExpectationInput(
            tier="critical", kind="measure", phase="Pump", expression=PRESSURE
        ),
    )
    _, on_final, written, _ = await _revive(s, rows, change)
    assert written is not None and (written.status, written.tier) == ("confirmed", "critical")
    assert [
        r.status for r in on_final if r.sentence == rows[0].sentence and r.id != written.id
    ] == [
        "rejected",
        "rejected",
    ]


async def test_a_tier_move_leaves_a_twin_the_person_had_put_elsewhere(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    """ "From here to there" applies to the rows that are here: a twin at another tier already is
    somewhere else. First half: a twin at the copy's tier moves with it."""
    s, rows = setup
    move = ChangeIn(kind="tier", reason=REASON, expectation_id=rows[0].id, tier="context")

    _, same_tier, _, _ = await _revive(s, rows, move)
    assert {r.tier for r in same_tier if r.sentence == rows[0].sentence} == {"context"}


async def test_a_tier_move_does_not_touch_a_twin_at_another_tier(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    s, rows = setup
    move = ChangeIn(kind="tier", reason=REASON, expectation_id=rows[0].id, tier="context")

    async def critical(version: int, originals: list[ExpectationRow]) -> None:
        mine = next(r for r in originals if r.sentence == rows[0].sentence)
        await s.repo.set_tier(mine.id, "critical")

    _, on_final, _, _ = await _revive(s, rows, move, before_the_put=critical)
    tiers = sorted(r.tier for r in on_final if r.sentence == rows[0].sentence)
    assert tiers == ["context", "critical"]


async def test_a_draft_can_only_change_the_signature_of_the_version_it_makes(
    setup: tuple[Setup, list[ExpectationRow]],
) -> None:
    """Version B1 was made by a draft of B0 and carries copies of B0's rows. A draft of B1 that
    leaves the document as it is makes no new version: its version is B1 itself, so its changes
    would land on B1's own rows (and, when it names one of B0's, on the copy B1 carries). Both
    are refused and say where to ask. The other half: a draft that does change the document
    takes the same names without trouble, and the base's rows stay as they were."""
    s, rows = setup
    drafts, first, _ = await s.draft(pressure=8.0)
    first_draft = await drafts.get(first)
    assert first_draft is not None
    b1 = (await put(s.client, {"id": first}))["current_version_id"]
    on_b1 = await s.repo.for_version(b1)
    assert [r.carried_from_id for r in on_b1 if r.carried_from_id] != []
    before = [r.model_dump_json() for r in on_b1]

    async def draft_of_b1(*changes: ChangeIn, pressure: float | None) -> int:
        document = (await s.proposals.base_profile(b1)).model_dump(
            mode="json", exclude={"annotations", "id"}
        )
        if pressure is not None:
            document["phases"][0]["pump"] = {"target": "pressure", "pressure": pressure, "flow": 0}
        row = await s.proposals.create_manual(
            base_version_id=b1,
            document=document,
            change_summary="A draft of the new version.",
            signature_changes=DraftSignatureChanges(
                changes=tuple(changes), reason=REASON, thread_id=s.thread
            ),
        )
        return row.id

    names_grandparent = ChangeIn(kind="retire", reason=REASON, expectation_id=rows[0].id)
    names_own = ChangeIn(kind="retire", reason=REASON, expectation_id=on_b1[0].id)
    drafts_before = await s.db.fetch_value("SELECT COUNT(*) FROM profile_drafts")
    for change in (names_grandparent, names_own):
        with pytest.raises(SignatureRefused) as refused:
            await draft_of_b1(change, pressure=None)
        assert "request_signature_change" in str(refused.value)
    # Nothing was left behind, and the base is as it was.
    assert await s.db.fetch_value("SELECT COUNT(*) FROM profile_drafts") == drafts_before
    assert [r.model_dump_json() for r in await s.repo.for_version(b1)] == before

    # A document that does change makes a version of its own; the named row must be b1's.
    with pytest.raises(SignatureRefused) as elder:
        await draft_of_b1(names_grandparent, pressure=7.0)
    assert "not one of the base version's" in str(elder.value)
    accepted = await draft_of_b1(names_own, pressure=7.0)
    request = await s.requests.for_draft(accepted)
    assert request is not None and request.status == "waiting"
    assert [r.model_dump_json() for r in await s.repo.for_version(b1)] == before
    # Putting it applies to the version it made, never to b1's rows.
    answered = await put(s.client, {"id": accepted})
    assert answered["current_version_id"] != b1
    assert [r.model_dump_json() for r in await s.repo.for_version(b1)] == before


@pytest.mark.parametrize("seed", range(40))
async def test_no_draft_application_changes_a_row_of_a_version_it_did_not_make(
    setup: tuple[Setup, list[ExpectationRow]], seed: int
) -> None:
    """A randomised walk of drafts of the profile's newest version (documents that change or do
    not, changes that name the version's own rows or any older one's). Some are answered at once
    and some wait while the profile moves on. Around every answer, the only rows that differ are
    rows of the version that answer made."""
    import random

    s, _ = setup
    rng = random.Random(seed)  # noqa: S311 - seeded, nothing secret
    head = s.base
    pending: list[tuple[int, int]] = []

    async def rows_of_all() -> tuple[dict[int, int], dict[int, str]]:
        every = await s.db.fetch_all(
            "SELECT e.id AS id, p.profile_version_id AS version FROM signature_expectations e "
            "JOIN profile_signatures p ON p.id = e.signature_id"
        )
        owner = {int(r["id"]): int(r["version"]) for r in every}
        return owner, {i: (await s.repo.get(i)).model_dump_json() for i in owner}  # type: ignore[union-attr]

    async def answer(draft_id: int, base: int) -> int | None:
        owner, snapshot = await rows_of_all()
        if rng.random() < 0.2:
            await s.client.post(f"/api/profile-drafts/{draft_id}/discard")
            final = None
        else:
            reply = await s.client.post("/api/profile-board", json={"draft_id": draft_id})
            final = reply.json()["data"]["current_version_id"] if reply.is_success else None
        _, after = await rows_of_all()
        changed = [i for i in owner if after[i] != snapshot[i]]
        assert all(owner[i] == final for i in changed), (seed, changed, final, owner)
        # And never a row of the version the draft was made from: that is the one it leaves be.
        assert all(owner[i] != base for i in changed), (seed, changed, base, owner)
        return final

    # The first steps are fixed so every walk has the shape that found a hole: a version made by
    # a draft (carrying copies), a draft of it that changes nothing and waits, and the profile
    # moving on before that one is answered. The rest is random.
    script = {0: (8.0, False), 1: (None, True), 2: (7.0, False)}
    for step in range(10):
        document = (await s.proposals.base_profile(head)).model_dump(
            mode="json", exclude={"annotations", "id"}
        )
        pressure = rng.choice([None, None, 6.5, 7.0, 7.5, 8.0, 8.5])
        waits = rng.random() < 0.6
        if step in script:
            pressure, waits = script[step]
        if pressure is not None:
            document["phases"][0]["pump"] = {"target": "pressure", "pressure": pressure, "flow": 0}
        # Rows the agent might name: the newest version's own, and the older ones it carries.
        on_head = await s.repo.for_version(head)
        nameable = sorted(
            {r.id for r in on_head} | {r.carried_from_id for r in on_head if r.carried_from_id}
        )
        if step == 1:
            # The older version's rows, as the version made from it carries them.
            nameable = sorted({r.carried_from_id for r in on_head if r.carried_from_id})
        named = rng.sample(nameable, k=min(len(nameable), rng.randint(1, 2)))
        if step == 0:
            named = []  # the version the later steps make a draft of keeps all its rows
        changes = tuple(ChangeIn(kind="retire", reason=REASON, expectation_id=i) for i in named)
        try:
            row = await s.proposals.create_manual(
                base_version_id=head,
                document=document,
                change_summary="A walk.",
                signature_changes=DraftSignatureChanges(
                    changes=changes, reason=REASON, thread_id=s.thread
                )
                if changes
                else None,
            )
        except SignatureRefused:
            continue
        if waits:
            pending.append((row.id, head))
        else:
            head = await answer(row.id, head) or head
        if pending and (step == 3 or rng.random() < 0.6):
            later, made_from = pending.pop(rng.randrange(len(pending)))
            head = await answer(later, made_from) or head
