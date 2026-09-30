"""A machine whose shot counter restarted: new shots reuse numbers the archive holds.

The firmware's counter (`hi`) lives in NVS with the machine's settings, so a
reflash, a factory reset or a replacement board numbers its shots from the start
again while the clock keeps going. A shot is therefore its number **and** its
start time (every index row and `.slog` header carries the same `startEpoch`),
and these tests play that reset against the fake machine: what the archive holds
of the old shots must not move, and the new shots must arrive as their own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gaggiclanker.device.fake import FakeDevice, default_notes, synthetic_slog_bytes
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import SHOT_FLAG_DELETED
from tests.sync.conftest import Archive, archive_for, corrupt_bytes

FIRST_LIFE = 1_760_000_000
SECOND_LIFE = 1_770_000_000


def brew(
    device: FakeDevice,
    number: int,
    epoch: int,
    *,
    rating: int = 0,
    volume: float = 36.0,
    notes: dict[str, Any] | None = None,
    raw: bytes | None = None,
) -> None:
    """A shot the way the firmware writes it: the index row's time is the header's."""
    device.add_shot(
        number,
        raw if raw is not None else synthetic_slog_bytes(shot_id=number, start_epoch=epoch),
        timestamp=epoch,
        notes=notes,
    )
    entry = device.shots[number].entry
    entry.rating = rating
    entry.volume_g = volume


def erase(device: FakeDevice) -> None:
    """The machine's settings are erased: its history is gone and the counter restarts."""
    device.reset_history()


async def rows(archive: Archive) -> list[dict[str, Any]]:
    found = await archive.db.fetch_all(
        "SELECT id, device_id, start_epoch, index_rating, index_volume_g, index_flags, "
        "deleted_on_device, quarantined FROM shots ORDER BY id"
    )
    return [dict(row) for row in found]


async def test_a_shot_that_reuses_an_archived_number_is_archived_as_its_own_shot(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600)

        run = await archive.engine.sync_shots(trigger="test")

        assert run.status == "ok" and run.shots_inserted == 1
        held = await rows(archive)
        assert [(row["device_id"], row["start_epoch"]) for row in held] == [
            ("000001", FIRST_LIFE + 600),
            ("000001", SECOND_LIFE + 600),
        ]
        fetched = [path for path in empty_device.requests if path.endswith(".slog")]
        assert len(fetched) == 2, "the reused number was never fetched"


async def test_an_archived_shot_keeps_its_index_fields_when_its_number_is_reused(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600, rating=5, volume=38.0)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        before = (await rows(archive))[0]
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600, rating=2, volume=44.0)
        empty_device.shots[1].entry.max_pressure_bar = 6.5

        await archive.engine.sync_shots(trigger="test")

        old, new = await rows(archive)
        for column in ("index_rating", "index_volume_g", "index_flags"):
            assert old[column] == before[column], column
        assert (new["index_rating"], new["index_volume_g"]) == (2, 44.0)
        stored_old = await archive.engine.shots.get(old["id"])
        assert stored_old is not None and stored_old.index_max_pressure_bar == 9.1


async def test_the_archived_shot_is_marked_gone_from_the_machine_never_overwritten(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    brew(empty_device, 2, FIRST_LIFE + 1200)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600)

        await archive.engine.sync_shots(trigger="test")

        by_time = {row["start_epoch"]: row for row in await rows(archive)}
        assert by_time[FIRST_LIFE + 600]["deleted_on_device"] == 1
        assert by_time[FIRST_LIFE + 1200]["deleted_on_device"] == 1
        assert by_time[SECOND_LIFE + 600]["deleted_on_device"] == 0
        events = {
            row["shot_id"]: row
            for row in await archive.db.fetch_all(
                "SELECT shot_id, message, data_json FROM sync_events WHERE kind = 'shot_updated'"
            )
        }
        reused = events[by_time[FIRST_LIFE + 600]["id"]]
        gone = events[by_time[FIRST_LIFE + 1200]["id"]]
        assert reused["message"] == "the machine numbered a later shot the same"
        assert json.loads(reused["data_json"])["number_reused"] is True
        assert gone["message"] == "gone from the machine's index"
        assert json.loads(gone["data_json"])["number_reused"] is False
        assert by_time[SECOND_LIFE + 600]["id"] not in events


async def test_a_pull_after_the_reset_is_settled_and_a_second_pull_changes_nothing(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600)
        await archive.engine.sync_shots(trigger="test")
        settled = await rows(archive)
        empty_device.requests.clear()

        run = await archive.engine.sync_shots(trigger="test")

        assert (run.shots_inserted, run.shots_updated) == (0, 0)
        assert await rows(archive) == settled
        assert not [path for path in empty_device.requests if path.endswith(".slog")]


async def test_index_edits_reach_the_shot_they_describe_and_no_other(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600, rating=5)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600, rating=2)
        await archive.engine.sync_shots(trigger="test")

        empty_device.shots[1].entry.rating = 4
        await archive.engine.sync_shots(trigger="test")

        old, new = await rows(archive)
        assert (old["index_rating"], new["index_rating"]) == (5, 4)


async def test_the_notes_of_a_reused_number_attach_to_the_new_shot_only(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600, notes=default_notes(1))
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        old_id = (await rows(archive))[0]["id"]
        old_card = await archive.engine.notes.get(old_id)
        old_judgement = await archive.engine.judgements.get(old_id)
        assert old_card is not None and old_judgement is not None
        erase(empty_device)
        new_notes = {**default_notes(1), "rating": 1, "notes": "brewed after the reset"}
        brew(empty_device, 1, SECOND_LIFE + 600, notes=new_notes)

        await archive.engine.sync_shots(trigger="test")

        _, new = await rows(archive)
        assert await archive.engine.notes.get(old_id) == old_card
        assert await archive.engine.judgements.get(old_id) == old_judgement
        new_card = await archive.engine.notes.get(new["id"])
        assert new_card is not None and new_card.notes == "brewed after the reset"
        seeded = await archive.engine.judgements.get(new["id"])
        assert seeded is not None and seeded.notes == "brewed after the reset"


async def test_a_reused_number_without_notes_does_not_inherit_the_old_shots_notes(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600, notes=default_notes(1))
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600)

        await archive.engine.sync_shots(trigger="test")

        _, new = await rows(archive)
        assert await archive.engine.notes.get(new["id"]) is None
        assert await archive.engine.judgements.get(new["id"]) is None


async def test_an_announced_shot_with_a_reused_number_is_fetched_before_the_index_lists_it(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600)
        empty_device.hidden_from_index.add(1)
        archive.engine._pushed_ids.add(1)

        await archive.engine.sync_shots(trigger="test")

        held = await rows(archive)
        assert [row["start_epoch"] for row in held] == [FIRST_LIFE + 600, SECOND_LIFE + 600]


async def test_an_announced_shot_the_archive_holds_is_not_stored_twice(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        empty_device.hidden_from_index.add(1)
        archive.engine._pushed_ids.add(1)

        run = await archive.engine.sync_shots(trigger="test")

        assert (run.status, run.errors, run.shots_inserted) == ("ok", 0, 0)
        assert len(await rows(archive)) == 1


async def test_a_number_can_be_reused_more_than_once(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    async with archive_for(empty_device, tmp_path) as archive:
        for life in (FIRST_LIFE, SECOND_LIFE, SECOND_LIFE + 10_000_000):
            erase(empty_device)
            brew(empty_device, 1, life + 600)
            await archive.engine.sync_shots(trigger="test")

        held = await rows(archive)
        assert len(held) == 3
        assert [row["deleted_on_device"] for row in held] == [1, 1, 0]


async def test_an_unreadable_shot_with_a_reused_number_is_kept_as_its_own_shot(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600, raw=corrupt_bytes())

        await archive.engine.sync_shots(trigger="test")
        await archive.engine.sync_shots(trigger="test")

        old, new = await rows(archive)
        assert (old["quarantined"], new["quarantined"]) == (0, 1)
        assert new["start_epoch"] == SECOND_LIFE + 600


async def test_a_shot_stored_unreadable_before_its_index_row_is_paired_once_it_appears(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    """No header, so no start time: the first index row of its number settles it, once."""
    brew(empty_device, 7, SECOND_LIFE + 600, raw=corrupt_bytes())
    empty_device.hidden_from_index.add(7)
    async with archive_for(empty_device, tmp_path) as archive:
        archive.engine._pushed_ids.add(7)
        await archive.engine.sync_shots(trigger="test")
        (announced,) = await rows(archive)
        assert (announced["quarantined"], announced["start_epoch"]) == (1, 0)

        empty_device.hidden_from_index.clear()
        await archive.engine.sync_shots(trigger="test")

        (settled,) = await rows(archive)
        assert settled["id"] == announced["id"]
        assert settled["start_epoch"] == SECOND_LIFE + 600
        assert settled["deleted_on_device"] == 0
        stored = await archive.engine.shots.get(settled["id"])
        assert stored is not None and stored.started_at is not None


async def test_a_deleted_index_row_of_a_reused_number_archives_nothing(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600)
        empty_device.shots[1].entry.flags |= SHOT_FLAG_DELETED
        empty_device.missing_files.add(1)

        await archive.engine.sync_shots(trigger="test")

        (old,) = await rows(archive)
        assert old["start_epoch"] == FIRST_LIFE + 600 and old["deleted_on_device"] == 1
        assert pad6(1) in {row["device_id"] for row in await rows(archive)}


async def test_an_unreadable_announced_shot_whose_number_is_held_is_not_stored_and_says_why(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 1, FIRST_LIFE + 600)
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 1, SECOND_LIFE + 600, raw=corrupt_bytes())
        empty_device.hidden_from_index.add(1)
        archive.engine._pushed_ids.add(1)

        await archive.engine.sync_shots(trigger="test")

        assert len(await rows(archive)) == 1
        skipped = await archive.db.fetch_all(
            "SELECT device_id, message FROM sync_events WHERE kind = 'shot_skipped'"
        )
        assert [row["device_id"] for row in skipped] == ["000001"]
        assert "already archived" in skipped[0]["message"]

        empty_device.hidden_from_index.clear()
        await archive.engine.sync_shots(trigger="test")
        assert [row["start_epoch"] for row in await rows(archive)] == [
            FIRST_LIFE + 600,
            SECOND_LIFE + 600,
        ]


async def test_a_different_unreadable_shot_under_the_number_is_not_taken_for_the_old_one(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    """The unknown start time is settled only by the very file that was stored."""
    brew(empty_device, 7, SECOND_LIFE, raw=corrupt_bytes())
    empty_device.hidden_from_index.add(7)
    async with archive_for(empty_device, tmp_path) as archive:
        archive.engine._pushed_ids.add(7)
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        brew(empty_device, 7, SECOND_LIFE + 5000, raw=corrupt_bytes() + b"different")

        await archive.engine.sync_shots(trigger="test")

        old, new = await rows(archive)
        assert (old["start_epoch"], old["deleted_on_device"]) == (0, 1)
        assert (new["start_epoch"], new["deleted_on_device"]) == (SECOND_LIFE + 5000, 0)


async def test_a_shot_the_machine_no_longer_holds_is_never_given_a_new_identity(
    empty_device: FakeDevice, tmp_path: Path
) -> None:
    brew(empty_device, 7, SECOND_LIFE, raw=corrupt_bytes())
    empty_device.hidden_from_index.add(7)
    async with archive_for(empty_device, tmp_path) as archive:
        archive.engine._pushed_ids.add(7)
        await archive.engine.sync_shots(trigger="test")
        erase(empty_device)
        await archive.engine.sync_shots(trigger="test")
        (gone,) = await rows(archive)
        assert gone["deleted_on_device"] == 1
        brew(empty_device, 7, SECOND_LIFE + 5000, raw=corrupt_bytes())

        await archive.engine.sync_shots(trigger="test")

        old, new = await rows(archive)
        assert (old["start_epoch"], old["deleted_on_device"]) == (0, 1)
        assert (new["start_epoch"], new["deleted_on_device"]) == (SECOND_LIFE + 5000, 0)
