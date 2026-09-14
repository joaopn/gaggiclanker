"""Notes write-back: the judgement going the other way, onto the machine's notes card.

Three things are being checked, and they fail in different ways when they are
wrong. The **composition** is silent: a float `doseOut` is stored by the firmware
and ignored by its index, so the shot's volume simply never updates and nothing
says so. The **conflict rule** is destructive in one direction: a write-back that
does not check the device's timestamp overwrites a note somebody typed on the
machine. And the **read path** is destructive in the other: the rule that
seeding is an insert-or-nothing is what stops a re-pull from clobbering a verdict
typed here, and the write-back must not weaken it.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite, ShotJudgementRow
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import SHOT_FLAG_HAS_NOTES, ShotNotes
from gaggiclanker.notes.writeback import NotesWritebackService, compose_device_notes
from tests.cleanup.conftest import FIRST_ID, NOTES_ID, data, drain_tasks, error

ALL_FIELDS = ["rating", "balance", "doseIn", "doseOut", "grindSetting", "notes"]


def _judgement(**overrides: object) -> ShotJudgementRow:
    values: dict[str, object] = {
        "shot_id": 1,
        "rating": 4,
        "balance": "balanced",
        "taste_tags_json": "[]",
        "dose_in_g": 18.0,
        "dose_out_g": 36.5,
        "grind_setting": "2.4",
        "notes": "chocolate, long finish",
        "updated_at": "2026-01-01T00:00:00.000Z",
    }
    values.update(overrides)
    return ShotJudgementRow.model_validate(values)


# ── composition ──────────────────────────────────────────────────────


def test_the_document_uses_the_string_forms_the_firmware_reads() -> None:
    """`doseOut` as a number is stored and silently ignored by the index."""
    notes = compose_device_notes(_judgement(), None, device_id="000100", fields=ALL_FIELDS)
    document = notes.to_device()
    assert document["id"] == "000100"
    assert document["doseIn"] == "18"
    assert document["doseOut"] == "36.5"
    assert isinstance(document["doseOut"], str)
    assert document["ratio"] == "2.03"
    assert document["rating"] == 4
    assert document["balanceTaste"] == "balanced"
    assert document["grindSetting"] == "2.4"


def test_a_long_note_is_truncated_rather_than_refused() -> None:
    """The firmware's schema caps `notes` at 200; losing the note would be worse."""
    long = "x" * 400
    notes = compose_device_notes(
        _judgement(notes=long[:200]), None, device_id="000100", fields=ALL_FIELDS
    )
    assert len(notes.notes) == 200


def test_keys_the_machine_carries_that_we_do_not_model_survive() -> None:
    """`saveNotes` stores the object verbatim, so another client's field is already there."""
    existing = ShotNotes.model_validate(
        {"id": "000100", "beanType": "Ethiopia", "somebodyElsesKey": "keep me"}
    )
    notes = compose_device_notes(_judgement(), existing, device_id="000100", fields=ALL_FIELDS)
    document = notes.to_device()
    assert document["somebodyElsesKey"] == "keep me"
    assert document["beanType"] == "Ethiopia"


def test_a_field_left_out_of_the_policy_keeps_what_the_machine_has() -> None:
    existing = ShotNotes.model_validate({"id": "000100", "grindSetting": "typed on the machine"})
    notes = compose_device_notes(
        _judgement(), existing, device_id="000100", fields=["rating", "doseOut"]
    )
    document = notes.to_device()
    assert document["grindSetting"] == "typed on the machine"
    assert document["rating"] == 4


def test_an_empty_dose_is_the_empty_string_the_card_uses() -> None:
    notes = compose_device_notes(
        _judgement(dose_in_g=None, dose_out_g=None), None, device_id="000100", fields=ALL_FIELDS
    )
    document = notes.to_device()
    assert document["doseIn"] == ""
    assert document["doseOut"] == ""


# ── the wire, against the fake machine ───────────────────────────────


async def _judge(app: FastAPI, shot_id: int, **fields: object) -> None:
    payload: dict[str, object] = {"rating": 4, "dose_in_g": 18.0, "dose_out_g": 36.5}
    payload.update(fields)
    await JudgementsRepository(app.state.db).upsert(shot_id, JudgementWrite.model_validate(payload))


async def _shot_id(app: FastAPI, device_id: int) -> int:
    row = await ShotsRepository(app.state.db).get_by_device_id(pad6(device_id))
    assert row is not None
    return row.id


def _service(app: FastAPI) -> NotesWritebackService:
    service: NotesWritebackService = app.state.notes_writeback
    return service


async def test_a_write_back_reaches_the_machine_and_updates_its_index(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The firmware mirrors `rating` into the index and `doseOut` into `volume`."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)

    result = await _service(app).writeback(shot_id)
    assert result.written is True

    shot = fake_device.shots[FIRST_ID]
    assert shot.notes is not None
    assert shot.notes["doseOut"] == "36.5"
    assert shot.notes["timestamp"] > 0
    assert shot.entry.rating == 4
    assert shot.entry.volume_g == pytest.approx(36.5)
    assert shot.entry.flags & SHOT_FLAG_HAS_NOTES


async def test_the_mirror_and_the_judgement_are_updated_after_a_write(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    before = await JudgementsRepository(app.state.db).get(shot_id)
    assert before is not None and before.device_synced_at is None

    await _service(app).writeback(shot_id)

    mirror = await NotesRepository(app.state.db).get(shot_id)
    assert mirror is not None
    assert mirror.dose_out_g == pytest.approx(36.5)
    assert mirror.device_timestamp is not None
    after = await JudgementsRepository(app.state.db).get(shot_id)
    assert after is not None and after.device_synced_at is not None
    # And it is no longer pending.
    assert shot_id not in await _service(app).pending()


async def test_nothing_is_written_while_device_writes_are_off(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The master switch is the one gate; there is no second switch to turn on."""
    app, _ = live
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    result = await _service(app).writeback(shot_id)
    assert result.written is False
    assert result.reason is not None and "switched off" in result.reason
    assert fake_device.shots[FIRST_ID].notes is None
    assert "req:history:notes:save" not in fake_device.ws_requests


async def test_a_verdict_seeded_from_the_machine_is_never_echoed_back(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """It is the machine's own words; echoing them would win every future comparison."""
    app, _ = writes_on
    shot_id = await _shot_id(app, NOTES_ID)
    judgement = await JudgementsRepository(app.state.db).get(shot_id)
    assert judgement is not None
    assert judgement.seeded_from_device_note is True

    result = await _service(app).writeback(shot_id)
    assert result.written is False
    assert result.reason is not None and "came from the machine" in result.reason
    assert shot_id not in await _service(app).pending()


async def test_editing_a_seeded_verdict_makes_it_writable(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The flag is cleared by any edit, which is what makes the rule about *editing*."""
    app, _ = writes_on
    shot_id = await _shot_id(app, NOTES_ID)
    await _judge(app, shot_id, rating=2)
    assert shot_id in await _service(app).pending()
    assert (await _service(app).writeback(shot_id)).written is True


async def test_a_newer_note_on_the_machine_is_left_alone(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The conflict rule, in the direction that would otherwise lose somebody's typing."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    # A note written on the machine a minute from now: newer than our verdict.
    await NotesRepository(app.state.db).upsert(
        shot_id,
        ShotNotes.model_validate(
            {"id": pad6(FIRST_ID), "rating": 5, "timestamp": int(time.time()) + 60}
        ),
    )
    result = await _service(app).writeback(shot_id)
    assert result.written is False
    assert result.reason is not None and "newer than this verdict" in result.reason
    assert fake_device.shots[FIRST_ID].notes is None


async def test_an_older_note_on_the_machine_is_overwritten(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The other direction: our verdict is newer, so it wins."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await NotesRepository(app.state.db).upsert(
        shot_id,
        ShotNotes.model_validate(
            {"id": pad6(FIRST_ID), "rating": 1, "timestamp": int(time.time()) - 3600}
        ),
    )
    await _judge(app, shot_id)
    assert (await _service(app).writeback(shot_id)).written is True
    shot = fake_device.shots[FIRST_ID]
    assert shot.notes is not None and shot.notes["rating"] == 4


async def test_a_shot_the_machine_has_deleted_is_skipped(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """`req:history:notes:save` would recreate a card for a shot whose `.slog` is gone."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    await ShotsRepository(app.state.db).mark_deleted_on_device(shot_id)
    result = await _service(app).writeback(shot_id)
    assert result.written is False
    assert result.reason is not None and "no longer holds" in result.reason
    assert shot_id not in await _service(app).pending()


# ── the read path still wins where it should ─────────────────────────


async def test_a_later_sync_does_not_clobber_a_newer_local_judgement(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The seeding rule, re-asserted: an insert that does nothing on conflict."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id, rating=2, notes="mine")

    seeded = await JudgementsRepository(app.state.db).seed_from_device_notes(
        shot_id,
        ShotNotes.model_validate({"id": pad6(FIRST_ID), "rating": 5, "notes": "the machine's"}),
    )
    assert seeded is False
    row = await JudgementsRepository(app.state.db).get(shot_id)
    assert row is not None
    assert row.rating == 2
    assert row.notes == "mine"


async def test_a_full_sync_pass_after_a_write_back_leaves_the_verdict_alone(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Our own write comes back on the next notes pull; it must not reseed anything."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id, rating=3, notes="mine")
    assert (await _service(app).writeback(shot_id)).written is True

    await app.state.connection.engine.sync_shots(trigger="test")

    row = await JudgementsRepository(app.state.db).get(shot_id)
    assert row is not None
    assert row.rating == 3
    assert row.notes == "mine"
    assert row.seeded_from_device_note is False


# ── the routes ───────────────────────────────────────────────────────


async def test_saving_a_judgement_never_contacts_the_machine(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """Even with device writes on: sending a verdict is an action on the Sync page.

    The PUT used to queue a write-back behind every save. Nothing may leave for
    the machine now — no task, no frame, no audit row — and the verdict simply
    waits in the pending list until a person sends it.
    """
    app, client = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    fake_device.ws_requests.clear()

    response = await client.put(
        f"/api/shots/{shot_id}/judgement", json={"rating": 5, "dose_out_g": 40.0}
    )
    assert response.status_code == 200
    await asyncio.sleep(0.05)

    assert not [name for name in app.state.tasks._tasks if name.startswith("notes-writeback")]
    assert "req:history:notes:save" not in fake_device.ws_requests
    assert fake_device.shots[FIRST_ID].notes is None
    assert await DeviceWritesRepository(app.state.db).list_writes() == []
    assert shot_id in await _service(app).pending()


async def test_there_is_no_per_shot_write_back_route(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """A write from outside the Sync page's send would be a second way in."""
    app, client = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    response = await client.post(f"/api/shots/{shot_id}/notes-writeback")
    assert response.status_code in (404, 405)
    assert "req:history:notes:save" not in fake_device.ws_requests


async def test_sending_every_ticked_judgement_walks_the_backlog(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    first = await _shot_id(app, FIRST_ID)
    second = await _shot_id(app, FIRST_ID + 1)
    await _judge(app, first)
    await _judge(app, second)

    pending = data(await client.get("/api/device/notes/pending"))
    assert {item["shot_id"] for item in pending["items"]} == {first, second}
    listed = next(item for item in pending["items"] if item["shot_id"] == first)
    assert listed["device_id"] == pad6(FIRST_ID)
    assert listed["rating"] == 4

    response = await client.post("/api/device/notes/push", json={"shot_ids": [second, first]})
    assert response.status_code == 202
    assert data(response)["pending"] == 2
    await _drain(app)

    assert fake_device.shots[FIRST_ID].notes is not None
    assert fake_device.shots[FIRST_ID + 1].notes is not None
    assert data(await client.get("/api/device/notes/pending"))["items"] == []


async def test_sending_a_selection_writes_exactly_those_shots(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """Three verdicts waiting, two selected: the third is not touched on the machine."""
    app, client = writes_on
    ids = [await _shot_id(app, FIRST_ID + offset) for offset in range(3)]
    for shot_id in ids:
        await _judge(app, shot_id)
    fake_device.ws_requests.clear()

    response = await client.post("/api/device/notes/push", json={"shot_ids": [ids[2], ids[0]]})
    assert response.status_code == 202
    assert data(response)["pending"] == 2
    await _drain(app)

    assert fake_device.ws_requests.count("req:history:notes:save") == 2
    assert fake_device.shots[FIRST_ID].notes is not None
    assert fake_device.shots[FIRST_ID + 2].notes is not None
    assert fake_device.shots[FIRST_ID + 1].notes is None
    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert sorted(row.device_id or "" for row in rows if row.kind == "notes_save") == [
        pad6(FIRST_ID),
        pad6(FIRST_ID + 2),
    ]
    assert await _service(app).pending() == [ids[1]]


async def test_a_selection_that_is_no_longer_pending_is_refused_and_sends_nothing(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """What is sent is what the person saw selected, or nothing."""
    app, client = writes_on
    judged = await _shot_id(app, FIRST_ID)
    unjudged = await _shot_id(app, FIRST_ID + 1)
    await _judge(app, judged)
    fake_device.ws_requests.clear()

    response = await client.post("/api/device/notes/push", json={"shot_ids": [judged, unjudged]})

    assert response.status_code == 409
    body = error(response)
    assert body["details"] == {"field": "shot_ids", "not_pending": 1}
    assert app.state.tasks.get("notes-writeback") is None
    assert "req:history:notes:save" not in fake_device.ws_requests


@pytest.mark.parametrize(
    "body",
    [None, {}, {"shot_ids": None}, {"shot_ids": []}],
    ids=["no body", "no ids", "null ids", "empty ids"],
)
async def test_a_send_needs_an_explicit_non_empty_selection(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
    fake_device: FakeDevice,
    body: dict[str, object] | None,
) -> None:
    """There is no "everything pending" form: nobody was shown a list for it."""
    app, client = writes_on
    await _judge(app, await _shot_id(app, FIRST_ID))

    if body is None:
        response = await client.post("/api/device/notes/push")
    else:
        response = await client.post("/api/device/notes/push", json=body)

    assert response.status_code == 400
    assert error(response)["code"] == "INVALID_REQUEST"
    assert app.state.tasks.get("notes-writeback") is None
    assert "req:history:notes:save" not in fake_device.ws_requests


async def test_the_service_refuses_an_empty_selection_too(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    from gaggiclanker.infra.errors import BadRequest

    app, _ = writes_on
    with pytest.raises(BadRequest, match="at least one judgement"):
        await _service(app).approve_push([])


async def test_a_send_with_device_writes_off_is_refused_and_audited(
    live: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = live
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)

    response = await client.post("/api/device/notes/push", json={"shot_ids": [shot_id]})

    assert response.status_code == 403
    assert "Device writes enabled" in error(response)["message"]
    assert app.state.tasks.get("notes-writeback") is None
    assert "req:history:notes:save" not in fake_device.ws_requests
    rows = await DeviceWritesRepository(app.state.db).list_writes()
    assert [(row.kind, row.result, row.device_id) for row in rows] == [
        ("notes_save", "refused", None)
    ]


async def test_a_selected_shot_with_a_newer_card_on_the_machine_is_still_left_alone(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """Selecting a shot is consent to send it, not to overwrite somebody's typing."""
    app, client = writes_on
    newer = await _shot_id(app, FIRST_ID)
    older = await _shot_id(app, FIRST_ID + 1)
    await _judge(app, newer)
    await _judge(app, older)
    await NotesRepository(app.state.db).upsert(
        newer,
        ShotNotes.model_validate(
            {"id": pad6(FIRST_ID), "rating": 5, "timestamp": int(time.time()) + 60}
        ),
    )

    response = await client.post("/api/device/notes/push", json={"shot_ids": [newer, older]})
    assert response.status_code == 202
    await _drain(app)

    assert fake_device.shots[FIRST_ID].notes is None
    assert fake_device.shots[FIRST_ID + 1].notes is not None


async def test_the_document_on_the_wire_is_the_one_the_firmware_can_read(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The end-to-end version of the composition tests: what the machine stored."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id, grind_setting="2.4", notes="one" * 5)
    await _service(app).writeback(shot_id)

    stored = fake_device.shots[FIRST_ID].notes
    assert stored is not None
    document = json.loads(json.dumps(stored))
    assert document["id"] == pad6(FIRST_ID)
    assert isinstance(document["doseOut"], str)
    assert isinstance(document["timestamp"], int)


async def _drain(app: FastAPI) -> None:
    await drain_tasks(app, "notes-writeback")


async def test_the_mirror_records_what_the_index_will_actually_hold(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """`synced_*` are the re-pull trigger, so they must follow the firmware's rules.

    The firmware copies `rating` into the index entry whatever it is, and
    overrides `volume` **only** for a non-empty string `doseOut` above zero
    (`ShotHistoryPlugin.cpp:557`). A write-back with no dose therefore leaves the
    entry's volume alone — and a mirror that claimed it now reads nothing would
    make the very next index diff see a difference that is not there and pull the
    card straight back.
    """
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    entry = fake_device.shots[FIRST_ID].entry
    volume_before = entry.volume_g

    # A card already on the machine and already mirrored, as a notes pass leaves
    # it: `synced_volume_g` is what the *index* said, not what the card said.
    await NotesRepository(app.state.db).upsert(
        shot_id,
        ShotNotes.model_validate({"id": pad6(FIRST_ID), "rating": 1}),
        index_rating=1,
        index_volume_g=volume_before,
    )

    await _judge(app, shot_id, rating=3, dose_in_g=18.0, dose_out_g=None)
    assert (await _service(app).writeback(shot_id)).written is True

    mirror = await NotesRepository(app.state.db).get(shot_id)
    assert mirror is not None
    assert mirror.synced_rating == 3
    assert mirror.synced_volume_g == pytest.approx(volume_before)
    assert entry.volume_g == pytest.approx(volume_before), "an empty dose moved the index volume"
    assert entry.rating == 3

    # The assertion that matters: a full pass afterwards does not re-read the
    # card we have just written.
    fake_device.requests.clear()
    await app.state.connection.engine.sync_shots(trigger="test")
    assert f"/api/history/{pad6(FIRST_ID)}.json" not in fake_device.requests


async def test_a_dose_that_is_sent_moves_the_mirror_and_the_index_together(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The other half of the same rule, and the same "no needless re-pull" proof."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id, rating=5, dose_out_g=41.5)
    assert (await _service(app).writeback(shot_id)).written is True

    mirror = await NotesRepository(app.state.db).get(shot_id)
    assert mirror is not None
    assert mirror.synced_volume_g == pytest.approx(41.5)
    assert fake_device.shots[FIRST_ID].entry.volume_g == pytest.approx(41.5)

    fake_device.requests.clear()
    await app.state.connection.engine.sync_shots(trigger="test")
    assert f"/api/history/{pad6(FIRST_ID)}.json" not in fake_device.requests
