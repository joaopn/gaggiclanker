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

import json
import time

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite, ShotJudgementRow
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import SHOT_FLAG_HAS_NOTES, ShotNotes
from gaggiclanker.notes.writeback import NotesWritebackService, compose_device_notes
from tests.cleanup.conftest import FIRST_ID, NOTES_ID, data, drain_tasks

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


@pytest.fixture
async def writeback_on(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> tuple[FastAPI, httpx.AsyncClient]:
    app, client = writes_on
    await app.state.settings_service.apply({"notesWritebackEnabled": True})
    return app, client


def _service(app: FastAPI) -> NotesWritebackService:
    service: NotesWritebackService = app.state.notes_writeback
    return service


async def test_a_write_back_reaches_the_machine_and_updates_its_index(
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The firmware mirrors `rating` into the index and `doseOut` into `volume`."""
    app, _ = writeback_on
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
    writeback_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = writeback_on
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


async def test_nothing_is_written_while_either_switch_is_off(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """`deviceWritesEnabled` alone is not enough: this feature has its own switch."""
    app, _ = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    result = await _service(app).writeback(shot_id)
    assert result.written is False
    assert result.reason is not None and "off" in result.reason
    assert fake_device.shots[FIRST_ID].notes is None


async def test_a_verdict_seeded_from_the_machine_is_never_echoed_back(
    writeback_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """It is the machine's own words; echoing them would win every future comparison."""
    app, _ = writeback_on
    shot_id = await _shot_id(app, NOTES_ID)
    judgement = await JudgementsRepository(app.state.db).get(shot_id)
    assert judgement is not None
    assert judgement.seeded_from_device_note is True

    result = await _service(app).writeback(shot_id)
    assert result.written is False
    assert result.reason is not None and "came from the machine" in result.reason
    assert shot_id not in await _service(app).pending()


async def test_editing_a_seeded_verdict_makes_it_writable(
    writeback_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The flag is cleared by any edit, which is what makes the rule about *editing*."""
    app, _ = writeback_on
    shot_id = await _shot_id(app, NOTES_ID)
    await _judge(app, shot_id, rating=2)
    assert shot_id in await _service(app).pending()
    assert (await _service(app).writeback(shot_id)).written is True


async def test_a_newer_note_on_the_machine_is_left_alone(
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The conflict rule, in the direction that would otherwise lose somebody's typing."""
    app, _ = writeback_on
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
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The other direction: our verdict is newer, so it wins."""
    app, _ = writeback_on
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
    writeback_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """`req:history:notes:save` would recreate a card for a shot whose `.slog` is gone."""
    app, _ = writeback_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    await ShotsRepository(app.state.db).mark_deleted_on_device(shot_id)
    result = await _service(app).writeback(shot_id)
    assert result.written is False
    assert result.reason is not None and "no longer holds" in result.reason
    assert shot_id not in await _service(app).pending()


# ── the read path still wins where it should ─────────────────────────


async def test_a_later_sync_does_not_clobber_a_newer_local_judgement(
    writeback_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The seeding rule, re-asserted: an insert that does nothing on conflict."""
    app, _ = writeback_on
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
    writeback_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Our own write comes back on the next notes pull; it must not reseed anything."""
    app, _ = writeback_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id, rating=3, notes="mine")
    assert (await _service(app).writeback(shot_id)).written is True

    await app.state.sync.sync_shots(trigger="test")

    row = await JudgementsRepository(app.state.db).get(shot_id)
    assert row is not None
    assert row.rating == 3
    assert row.notes == "mine"
    assert row.seeded_from_device_note is False


# ── the routes ───────────────────────────────────────────────────────


async def test_saving_a_judgement_queues_a_write_back(
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The PUT answers with the verdict; the frame goes out behind it."""
    app, client = writeback_on
    shot_id = await _shot_id(app, FIRST_ID)
    response = await client.put(
        f"/api/shots/{shot_id}/judgement", json={"rating": 5, "dose_out_g": 40.0}
    )
    assert response.status_code == 200
    await _drain(app)
    assert fake_device.shots[FIRST_ID].notes is not None
    assert fake_device.shots[FIRST_ID].entry.rating == 5


async def test_the_per_shot_route_reports_why_it_wrote_nothing(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """A refusal is a 200 with a sentence, not a 4xx: the request was not wrong."""
    app, client = writes_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id)
    body = data(await client.post(f"/api/shots/{shot_id}/notes-writeback"))
    assert body["written"] is False
    assert "off" in body["reason"]


async def test_the_bulk_push_walks_the_backlog(
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writeback_on
    first = await _shot_id(app, FIRST_ID)
    second = await _shot_id(app, FIRST_ID + 1)
    await _judge(app, first)
    await _judge(app, second)

    pending = data(await client.get("/api/device/notes/pending"))
    assert set(pending["shot_ids"]) == {first, second}

    response = await client.post("/api/device/notes/push")
    assert response.status_code == 202
    assert data(response)["pending"] == 2
    await _drain(app)

    assert fake_device.shots[FIRST_ID].notes is not None
    assert fake_device.shots[FIRST_ID + 1].notes is not None
    assert data(await client.get("/api/device/notes/pending"))["shot_ids"] == []


async def test_the_document_on_the_wire_is_the_one_the_firmware_can_read(
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The end-to-end version of the composition tests: what the machine stored."""
    app, _ = writeback_on
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
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """`synced_*` are the re-pull trigger, so they must follow the firmware's rules.

    The firmware copies `rating` into the index entry whatever it is, and
    overrides `volume` **only** for a non-empty string `doseOut` above zero
    (`ShotHistoryPlugin.cpp:557`). A write-back with no dose therefore leaves the
    entry's volume alone — and a mirror that claimed it now reads nothing would
    make the very next index diff see a difference that is not there and pull the
    card straight back.
    """
    app, _ = writeback_on
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
    await app.state.sync.sync_shots(trigger="test")
    assert f"/api/history/{pad6(FIRST_ID)}.json" not in fake_device.requests


async def test_a_dose_that_is_sent_moves_the_mirror_and_the_index_together(
    writeback_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    """The other half of the same rule, and the same "no needless re-pull" proof."""
    app, _ = writeback_on
    shot_id = await _shot_id(app, FIRST_ID)
    await _judge(app, shot_id, rating=5, dose_out_g=41.5)
    assert (await _service(app).writeback(shot_id)).written is True

    mirror = await NotesRepository(app.state.db).get(shot_id)
    assert mirror is not None
    assert mirror.synced_volume_g == pytest.approx(41.5)
    assert fake_device.shots[FIRST_ID].entry.volume_g == pytest.approx(41.5)

    fake_device.requests.clear()
    await app.state.sync.sync_shots(trigger="test")
    assert f"/api/history/{pad6(FIRST_ID)}.json" not in fake_device.requests
