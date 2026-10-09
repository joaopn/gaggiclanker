"""``GET /api/profile-drafts/{id}/standing``: one read of where a proposal stands.

Every state is reached through the real routes (put, a sync against the fake machine, a round
trip the machine garbles, a file edited on the machine, a reset machine, discard, refine, a
later make-active), and the rule itself is walked over every status and board state to prove the
six states are total and exclusive.
"""

from __future__ import annotations

import copy
import itertools
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.lineage import person_taken_sentence
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.drafts import standing as words
from gaggiclanker.drafts.standing import RowFacts, classify
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import make_set_on, manual_draft, same_name_draft, tombstone
from tests.drafts.test_board import adopted, get_board, pull, put, row_for
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]

STATES = {"waiting", "approved", "on_machine", "not_on_machine", "declined", "replaced"}


async def standing(client: httpx.AsyncClient, draft: dict[str, Any]) -> dict[str, Any]:
    return dict(data(await client.get(f"/api/profile-drafts/{draft['id']}/standing")))


async def test_a_waiting_change_serves_both_documents_and_where_it_would_land(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    change = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    active = data(await client.get(f"/api/profile-versions/{row['current_version_id']}"))

    got = await standing(client, change)

    assert set(got) == {
        "draft",
        "profile",
        "active_profile",
        "landing",
        "state",
        "reason",
        "row_id",
        "row_label",
        "selected",
        "set_version_label",
        "writes_enabled",
    }
    assert got["state"] == "waiting" and got["reason"] is None and got["selected"] is None
    assert got["draft"]["id"] == change["id"] and got["draft"]["change_summary"]
    assert got["profile"]["phases"][0]["pump"]["pressure"] == 8
    assert got["active_profile"] == active["profile"]
    assert (got["row_id"], got["row_label"]) == (row["id"], BASE_LABEL)
    assert got["landing"]["draft_id"] == change["id"]
    assert got["landing"]["plain"]["row_id"] == row["id"]
    assert got["landing"]["plain"]["refused"] is None
    assert got["writes_enabled"] is True


async def test_a_waiting_new_profile_has_no_active_document_and_a_refusal_shows(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    first = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 8)
    second = await manual_draft(app, client, BASE_LABEL, "Soft Bloom", 7)

    got = await standing(client, first)
    assert got["state"] == "waiting" and got["active_profile"] is None
    assert got["row_id"] is None and got["landing"]["plain"]["refused"] is None

    await put(client, first)  # the name is a profile now: the second would be refused
    refused = await standing(client, second)

    assert refused["state"] == "waiting"
    assert refused["landing"]["plain"]["refused"] == person_taken_sentence("Soft Bloom")


async def test_approved_says_why_it_is_not_on_the_machine_yet(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    on = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    row = await put(client, on)

    got = await standing(client, on)
    assert got["state"] == "approved" and got["reason"] == words.AT_NEXT_SYNC
    assert (got["row_id"], got["row_label"]) == (row["id"], row["label"])
    assert got["landing"] is None and got["active_profile"] is None
    assert got["profile"]["phases"][0]["pump"]["pressure"] == 8

    await app.state.settings_service.apply({"deviceWritesEnabled": False})
    assert (await standing(client, on))["reason"] == words.WRITES_OFF
    assert (await standing(client, on))["writes_enabled"] is False


async def test_approved_after_a_sync_that_could_not_reach_the_machine_says_so(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    await fake.stop()
    try:
        run = await pull(app)
        assert run.status == "error"
        got = await standing(client, draft)
    finally:
        await fake.start()

    assert got["state"] == "approved" and got["reason"] == words.SYNC_FAILED


async def test_a_sync_that_put_it_on_the_machine_says_so_and_whether_it_is_selected(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)

    run = await pull(app)
    assert run.status == "ok", run.error
    got = await standing(client, draft)

    assert got["draft"]["status"] == "pushed"
    assert got["state"] == "on_machine" and got["reason"] is None
    assert got["selected"] is False
    held = row_for(await get_board(client), BASE_LABEL)["machine"]["device_id"]
    fake.selected_profile_id = held
    # The mirror marks the selected profile from the status frame's id, which the sync keeps.
    app.state.connection.engine._selected_profile_id = held
    await pull(app)
    assert (await standing(client, draft))["selected"] is True


async def test_a_version_the_machine_read_back_differently_is_not_on_the_machine(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    fake.mutate_on_save = lambda p: {**p, "temperature": float(p.get("temperature", 90)) + 1}

    await pull(app)
    got = await standing(client, draft)

    assert got["state"] == "not_on_machine" and got["reason"] == words.READ_BACK_DIFFERENTLY
    assert got["selected"] is None


async def test_a_profile_edited_on_the_machine_is_a_conflict_and_not_on_the_machine(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    file = next(p for p in fake.profiles if p["label"] == BASE_LABEL)
    file["temperature"] = float(file["temperature"]) + 3
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    await pull(app)
    await pull(app)
    assert row_for(await get_board(client), BASE_LABEL)["in_conflict"] is True

    got = await standing(client, draft)

    assert got["state"] == "not_on_machine" and got["reason"] == words.IN_CONFLICT


async def test_a_machine_that_looks_reset_pauses_it(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    fresh = copy.deepcopy(fake.profiles[0])
    fresh.update(id="reborn", label="Factory default")
    fake.profiles = [fresh]
    fake.favorite_profile_ids.clear()

    await pull(app)
    got = await standing(client, draft)

    assert got["state"] == "not_on_machine" and got["reason"] == words.PAUSED


async def test_a_declined_proposal_is_declined(adopted: Adopted, provider: FakeProvider) -> None:
    app, client, _ = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    assert (await client.post(f"/api/profile-drafts/{draft['id']}/discard")).is_success

    got = await standing(client, draft)

    assert got["state"] == "declined" and got["reason"] is None
    assert got["landing"] is None and got["active_profile"] is None


async def test_a_refined_proposal_is_replaced_by_the_newer_one(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    document = dict((await standing(client, draft))["profile"])
    document["phases"] = [dict(p) for p in document["phases"]]
    document["phases"][0]["pump"] = {"target": "pressure", "pressure": 7, "flow": 0}
    provider.script = [json.dumps({"profile": document, "change_summary": "7 bar."})]
    newer = data(
        await client.post(f"/api/profile-drafts/{draft['id']}/refine", json={"notes": "less"})
    )

    got = await standing(client, draft)

    assert got["state"] == "replaced" and got["reason"] == words.NEWER_PROPOSAL
    assert (await standing(client, newer))["state"] == "waiting"


async def test_a_later_active_version_replaces_an_approved_one(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    first = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, first)
    await pull(app)
    assert (await standing(client, first))["state"] == "on_machine"
    second = await same_name_draft(app, client, provider, BASE_LABEL, 7)
    await put(client, second)

    got = await standing(client, first)

    assert got["state"] == "replaced" and got["reason"] == words.ANOTHER_ACTIVE
    assert (await standing(client, second))["state"] == "approved"


async def test_it_answers_for_any_draft_id_and_never_writes(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    db = app.state.db
    before = await db.fetch_value("SELECT total_changes()")

    await standing(client, draft)
    gone = await client.get("/api/profile-drafts/987654/standing")

    assert gone.status_code == 404 and error(gone)["code"] == "NOT_FOUND"
    assert await db.fetch_value("SELECT total_changes()") == before


async def test_a_pushed_proposal_whose_profile_was_switched_off_and_synced_is_not_declined(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    row = await put(client, draft)
    assert (await pull(app)).status == "ok"
    assert (await standing(client, draft))["state"] == "on_machine"
    assert (
        await client.put(f"/api/profile-board/{row['id']}/on-machine", json={"on": False})
    ).is_success
    assert (await pull(app)).status == "ok"

    got = await standing(client, draft)

    assert got["draft"]["status"] == "discarded", "the sync closed it; nobody declined it"
    assert (got["state"], got["reason"]) == ("not_on_machine", words.TOOK_OFF)

    # Switched back on, it is waiting for the next sync again, not still "took off".
    await client.put(f"/api/profile-board/{row['id']}/on-machine", json={"on": True})
    again = await standing(client, draft)
    assert (again["state"], again["reason"]) == ("approved", words.AT_NEXT_SYNC)
    assert (await pull(app)).status == "ok"
    assert (await standing(client, draft))["state"] == "on_machine"


async def test_going_back_replaces_a_pushed_version_and_making_it_active_again_waits(
    adopted: Adopted, provider: FakeProvider
) -> None:
    """The sync closes the draft when its file goes off the machine. If the person then makes
    that very version active again, the board says it is the active version and the machine
    holds the older one: that is "approved, waiting for a sync", not replaced and not declined."""
    app, client, _ = adopted
    old_version = row_for(await get_board(client), BASE_LABEL)["row"]["current_version_id"]
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    row = await put(client, draft)
    assert (await pull(app)).status == "ok"
    version = row["current_version_id"]
    back = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": old_version}
    )
    assert back.is_success, back.text
    assert (await pull(app)).status == "ok"

    got = await standing(client, draft)

    assert got["draft"]["status"] == "discarded"
    assert (got["state"], got["reason"]) == ("replaced", words.ANOTHER_ACTIVE)

    again = await client.put(
        f"/api/profile-board/{row['id']}/active-version", json={"version_id": version}
    )
    assert again.is_success, again.text
    waiting = await standing(client, draft)
    assert (waiting["state"], waiting["reason"]) == ("approved", words.AT_NEXT_SYNC)
    assert (await pull(app)).status == "ok"
    assert (await standing(client, draft))["state"] == "on_machine"


async def test_a_deleted_profile_whose_file_a_sync_removed_reads_as_gone(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    row = await put(client, draft)
    assert (await pull(app)).status == "ok"
    await tombstone(client, row["id"])
    assert (await pull(app)).status == "ok"

    got = await standing(client, draft)

    assert got["draft"]["status"] == "discarded"
    assert (got["state"], got["reason"]) == ("replaced", words.NOT_IN_LIST)


async def test_a_pushed_proposal_is_not_on_the_machine_while_syncs_are_paused(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    draft = await same_name_draft(app, client, provider, BASE_LABEL, 8)
    await put(client, draft)
    assert (await pull(app)).status == "ok"
    assert (await standing(client, draft))["state"] == "on_machine"
    fresh = copy.deepcopy(fake.profiles[0])
    fresh.update(id="reborn", label="Factory default")
    fake.profiles = [fresh]
    fake.favorite_profile_ids.clear()
    await pull(app)
    assert (await get_board(client))["paused"]

    got = await standing(client, draft)

    assert got["draft"]["status"] == "pushed"
    assert (got["state"], got["reason"]) == ("not_on_machine", words.PAUSED)


async def test_a_waiting_proposal_whose_document_is_already_listed_is_replaced(
    adopted: Adopted,
) -> None:
    app, client, _ = adopted
    row = row_for(await get_board(client), BASE_LABEL)["row"]
    twin = await manual_draft(app, client, BASE_LABEL, BASE_LABEL, 8, same_document=True)

    got = await standing(client, twin)

    assert got["draft"]["status"] == "draft"
    assert got["state"] == "replaced"
    assert got["reason"] == words.already_in_list(BASE_LABEL)
    assert (got["row_id"], got["row_label"]) == (row["id"], BASE_LABEL)
    assert got["landing"] is None


async def test_the_set_version_an_approval_is_or_will_be_recorded_as(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    first = await put(client, await manual_draft(app, client, BASE_LABEL, "For a set", 8))
    set_id = await make_set_on(client, "Label set", first["current_version_id"])

    async def for_set(bar: float) -> dict[str, Any]:
        draft = await same_name_draft(app, client, provider, "For a set", bar)
        await app.state.db.execute(
            "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, draft["id"])
        )
        return draft

    plain = await for_set(7)
    assert (await standing(client, plain))["set_version_label"] is None, "waiting: not yet"
    minor = await put(client, plain, set_id=set_id)
    got = await standing(client, plain)
    assert minor["pending_set_id"] == set_id
    assert got["set_version_label"] == got["draft"]["set_next_minor_label"] == "v1.1"

    assert (await pull(app)).status == "ok"
    recorded = await standing(client, plain)
    assert recorded["set_version_label"] == recorded["draft"]["recorded_version_label"] == "v1.1"

    major = await for_set(6)
    await put(client, major, set_id=set_id, major=True)
    got = await standing(client, major)
    assert got["set_version_label"] == got["draft"]["set_next_major_label"] == "v2"

    other = await same_name_draft(app, client, provider, "For a set", 5)
    await put(client, other)
    assert (await standing(client, other))["set_version_label"] is None, "not for a Set"


async def test_a_set_draft_put_without_its_set_has_no_set_version_label(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    first = await put(client, await manual_draft(app, client, BASE_LABEL, "For a set", 8))
    set_id = await make_set_on(client, "Unlinked set", first["current_version_id"])
    draft = await same_name_draft(app, client, provider, "For a set", 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, draft["id"])
    )
    row = await put(client, draft)  # the put carries no Set
    assert row["pending_set_id"] is None

    assert (await standing(client, draft))["set_version_label"] is None


async def test_a_set_draft_whose_row_moved_on_to_another_pending_draft_has_no_label(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, _ = adopted
    first = await put(client, await manual_draft(app, client, BASE_LABEL, "For a set", 8))
    set_id = await make_set_on(client, "Moved set", first["current_version_id"])
    older = await same_name_draft(app, client, provider, "For a set", 7)
    newer = await same_name_draft(app, client, provider, "For a set", 6)
    for draft in (older, newer):
        await app.state.db.execute(
            "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, draft["id"])
        )
    await put(client, older, set_id=set_id)
    assert (await standing(client, older))["set_version_label"] == "v1.1"

    await put(client, newer, set_id=set_id)  # the row now waits to record this one instead

    assert (await standing(client, older))["set_version_label"] is None


# -- the rule: six states, exactly one for every input ---------------------------------------


def _draft(status: str) -> ProfileDraftRow:
    return ProfileDraftRow.model_validate(
        {
            "id": 1,
            "base_version_id": 1,
            "status": status,
            "created_at": "2026-10-09T00:00:00Z",
            "updated_at": "2026-10-09T00:00:00Z",
        }
    )


def test_the_states_are_total_and_exclusive_over_every_status_and_board_state() -> None:
    statuses = ("draft", "approved", "pushed", "failed", "discarded", "superseded")
    flags = list(itertools.product((False, True), repeat=4))
    seen: set[str] = set()
    for status in statuses:
        rows: list[RowFacts | None] = [None]
        for active, failed, conflict, holds in flags:
            for on in (False, True):
                rows.append(
                    RowFacts(
                        row_id=1,
                        label="x",
                        active_is_this=active,
                        on_machine=on,
                        failed_is_this=failed,
                        in_conflict=conflict,
                        holds_current=holds,
                        selected=None,
                    )
                )
        for row, writes, paused, failed_since in itertools.product(
            rows, (False, True), (False, True), (False, True)
        ):
            got = classify(
                _draft(status),
                row,
                writes_enabled=writes,
                paused=paused,
                sync_failed_since=failed_since,
            )
            assert got.state in STATES
            seen.add(got.state)
            # A sentence goes with exactly the states that need one.
            needs = got.state in ("approved", "not_on_machine", "replaced")
            assert (got.reason is not None) is needs, (status, got)
    assert seen == STATES


@pytest.mark.parametrize(
    ("status", "state"),
    [("draft", "waiting"), ("discarded", "declined"), ("superseded", "replaced")],
)
def test_a_status_that_ends_it_decides_the_state_whatever_the_board_says(
    status: str, state: str
) -> None:
    row = RowFacts(
        row_id=1,
        label="x",
        active_is_this=True,
        on_machine=True,
        failed_is_this=False,
        in_conflict=False,
        holds_current=True,
        selected=True,
    )
    assert (
        classify(
            _draft(status), row, writes_enabled=True, paused=False, sync_failed_since=False
        ).state
        == state
    )


def _facts(**over: Any) -> RowFacts:
    base: dict[str, Any] = {
        "row_id": 1,
        "label": "x",
        "active_is_this": True,
        "on_machine": True,
        "failed_is_this": False,
        "in_conflict": False,
        "holds_current": False,
        "selected": None,
    }
    base.update(over)
    return RowFacts(**base)


def _classified(
    status: str,
    row: RowFacts | None,
    *,
    action: str | None = None,
    writes: bool = True,
    paused: bool = False,
    failed_since: bool = False,
    listed_as: str | None = None,
) -> tuple[str, str | None]:
    draft = _draft(status)
    if action is not None:
        draft = draft.model_copy(update={"outcome": {"action": action}})
    got = classify(
        draft,
        row,
        writes_enabled=writes,
        paused=paused,
        sync_failed_since=failed_since,
        already_listed_as=listed_as,
    )
    return got.state, got.reason


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        # Ended by a person or by a newer proposal, whatever the board says.
        (("discarded", _facts()), ("declined", None)),
        (("discarded", None), ("declined", None)),
        (("discarded", _facts(), {"action": "push"}), ("declined", None)),
        (("superseded", _facts(holds_current=True)), ("replaced", words.NEWER_PROPOSAL)),
        (("failed", None), ("not_on_machine", words.READ_BACK_DIFFERENTLY)),
        # Waiting, and waiting on a document the list already holds.
        (("draft", None), ("waiting", None)),
        (("draft", None, {"listed_as": "Bloom"}), ("replaced", words.already_in_list("Bloom"))),
        # Approved: the row decides, in this order.
        (("approved", None), ("replaced", words.NOT_IN_LIST)),
        (("approved", _facts(active_is_this=False)), ("replaced", words.ANOTHER_ACTIVE)),
        (
            ("approved", _facts(active_is_this=False, in_conflict=True)),
            ("replaced", words.ANOTHER_ACTIVE),
        ),
        (
            ("approved", _facts(active_is_this=False, failed_is_this=True)),
            ("replaced", words.ANOTHER_ACTIVE),
        ),
        (("approved", _facts(in_conflict=True)), ("not_on_machine", words.IN_CONFLICT)),
        (
            ("approved", _facts(in_conflict=True, failed_is_this=True, holds_current=True)),
            ("not_on_machine", words.IN_CONFLICT),
        ),
        (
            ("approved", _facts(failed_is_this=True)),
            ("not_on_machine", words.READ_BACK_DIFFERENTLY),
        ),
        (
            ("approved", _facts(failed_is_this=True), {"paused": True}),
            ("not_on_machine", words.READ_BACK_DIFFERENTLY),
        ),
        (("approved", _facts(), {"paused": True}), ("not_on_machine", words.PAUSED)),
        (
            ("approved", _facts(holds_current=True), {"paused": True}),
            ("not_on_machine", words.PAUSED),
        ),
        (("approved", _facts(holds_current=True)), ("on_machine", None)),
        (("approved", _facts(holds_current=True), {"writes": False}), ("on_machine", None)),
        (("approved", _facts(), {"writes": False}), ("approved", words.WRITES_OFF)),
        (
            ("approved", _facts(on_machine=False), {"writes": False}),
            ("approved", words.WRITES_OFF),
        ),
        (("approved", _facts(on_machine=False)), ("approved", words.SWITCHED_OFF)),
        (("approved", _facts()), ("approved", words.AT_NEXT_SYNC)),
        (("approved", _facts(), {"failed_since": True}), ("approved", words.SYNC_FAILED)),
        (
            ("approved", _facts(), {"failed_since": True, "writes": False}),
            ("approved", words.WRITES_OFF),
        ),
        # Pushed is read from the board the same way: only a file the mirror holds is on it.
        (("pushed", _facts(holds_current=True)), ("on_machine", None)),
        (
            ("pushed", _facts(holds_current=True), {"paused": True}),
            ("not_on_machine", words.PAUSED),
        ),
        (("pushed", _facts()), ("approved", words.AT_NEXT_SYNC)),
        (("pushed", _facts(in_conflict=True)), ("not_on_machine", words.IN_CONFLICT)),
        (("pushed", _facts(active_is_this=False)), ("replaced", words.ANOTHER_ACTIVE)),
        (("pushed", None), ("replaced", words.NOT_IN_LIST)),
        # Closed by a sync because the file went off the machine: not declined.
        (
            ("discarded", _facts(on_machine=False), {"action": "switched_off"}),
            ("not_on_machine", words.TOOK_OFF),
        ),
        (
            ("discarded", _facts(on_machine=True), {"action": "switched_off"}),
            ("approved", words.AT_NEXT_SYNC),
        ),
        (
            ("discarded", _facts(active_is_this=False), {"action": "went_back"}),
            ("replaced", words.ANOTHER_ACTIVE),
        ),
        (
            ("discarded", _facts(active_is_this=False), {"action": "removed"}),
            ("replaced", words.ANOTHER_ACTIVE),
        ),
        (
            ("discarded", _facts(active_is_this=False), {"action": "switched_off"}),
            ("replaced", words.ANOTHER_ACTIVE),
        ),
        (
            ("discarded", _facts(active_is_this=True), {"action": "went_back"}),
            ("approved", words.AT_NEXT_SYNC),
        ),
        (("discarded", None, {"action": "deleted"}), ("replaced", words.NOT_IN_LIST)),
        (
            ("discarded", _facts(), {"action": "removed"}),
            ("approved", words.AT_NEXT_SYNC),
        ),
    ],
)
def test_each_input_gets_its_state(
    inputs: tuple[Any, ...], expected: tuple[str, str | None]
) -> None:
    status, row = inputs[0], inputs[1]
    extra = dict(inputs[2]) if len(inputs) > 2 else {}
    action = extra.pop("action", None)
    assert _classified(status, row, action=action, **extra) == expected
