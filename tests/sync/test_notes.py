"""The device's notes card, mirrored — and re-pulled when the index says it moved.

The re-pull signal is the trap. The notes document is a file on the machine and
nothing announces an edit, but saving the notes card rewrites the shot's **index
entry** (`rating`, and `doseOut` into `volume`). Watching those two numbers costs
no request; anything else would mean re-fetching every notes file on every pass.
"""

from __future__ import annotations

from gaggiclanker.domain.ids import pad6
from tests.sync.conftest import NOTES_ID, Archive


async def test_notes_are_pulled_verbatim_and_parsed(small_archive: Archive) -> None:
    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(NOTES_ID))
    assert shot is not None
    notes = await small_archive.engine.notes.get(shot.id)
    assert notes is not None

    # Verbatim: the firmware stores whatever object it was handed and writes
    # every number as a string, so the document is the record…
    assert notes.document is not None
    assert notes.document["doseOut"] == "36.5"
    # …and the typed columns beside it are the convenience.
    assert notes.dose_out_g == 36.5
    assert notes.dose_in_g == 18.0
    assert notes.rating == 4
    assert notes.bean_type == "Test Roaster Ethiopia"
    assert notes.balance_taste == "balanced"


async def test_the_shot_row_carries_the_rating(small_archive: Archive) -> None:
    """The list view shows a rating without a second request per row."""
    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(NOTES_ID))
    assert shot is not None
    assert shot.rating == 4
    assert shot.has_notes is True


async def test_only_entries_flagged_has_notes_are_fetched(small_archive: Archive) -> None:
    """One notes request per shot that has any, not one per shot."""
    await small_archive.engine.sync_shots(trigger="test")

    json_requests = [r for r in small_archive.device.requests if r.endswith(".json")]
    assert json_requests == [f"/api/history/{pad6(NOTES_ID)}.json"]


async def test_a_second_pass_does_not_re_fetch_unchanged_notes(small_archive: Archive) -> None:
    await small_archive.engine.sync_shots(trigger="test")
    before = len([r for r in small_archive.device.requests if r.endswith(".json")])

    run = await small_archive.engine.sync_shots(trigger="test")

    after = len([r for r in small_archive.device.requests if r.endswith(".json")])
    assert after == before
    notes_run = await small_archive.engine.runs.last_runs()
    assert notes_run["notes"].notes_synced == 0
    assert run.status == "ok"


async def test_a_changed_index_rating_triggers_a_re_pull(small_archive: Archive) -> None:
    await small_archive.engine.sync_shots(trigger="test")
    fake_shot = small_archive.device.shots[NOTES_ID]
    fake_shot.entry.rating = 2
    assert fake_shot.notes is not None
    fake_shot.notes["rating"] = 2
    fake_shot.notes["notes"] = "second thoughts"

    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(NOTES_ID))
    assert shot is not None
    notes = await small_archive.engine.notes.get(shot.id)
    assert notes is not None
    assert notes.rating == 2
    assert notes.notes == "second thoughts"
    assert notes.synced_rating == 2, "the re-pull trigger is rearmed at the new value"


async def test_a_changed_dose_out_triggers_a_re_pull(small_archive: Archive) -> None:
    """`doseOut` from the notes card overrides `volume` in the index entry."""
    await small_archive.engine.sync_shots(trigger="test")
    fake_shot = small_archive.device.shots[NOTES_ID]
    fake_shot.entry.volume_g = 40.0
    assert fake_shot.notes is not None
    fake_shot.notes["doseOut"] = "40"

    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(NOTES_ID))
    assert shot is not None
    notes = await small_archive.engine.notes.get(shot.id)
    assert notes is not None
    assert notes.dose_out_g == 40.0
    assert notes.synced_volume_g == 40.0


async def test_notes_that_appear_later_are_picked_up(small_archive: Archive) -> None:
    """A shot is usually rated minutes after it is pulled."""
    await small_archive.engine.sync_shots(trigger="test")
    fake_shot = small_archive.device.shots[101]
    fake_shot.notes = {"id": pad6(101), "rating": 5, "doseIn": "18", "doseOut": "36"}
    fake_shot.entry.flags |= 0x04  # HAS_NOTES
    fake_shot.entry.rating = 5

    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(101))
    assert shot is not None
    stored = await small_archive.engine.notes.get(shot.id)
    assert stored is not None
    assert stored.rating == 5


async def test_the_notes_run_is_its_own_ledger_entry(small_archive: Archive) -> None:
    await small_archive.engine.sync_shots(trigger="startup")

    runs = await small_archive.engine.runs.last_runs()
    assert runs["notes"].kind == "notes"
    assert runs["notes"].notes_synced == 1
    assert runs["notes"].status == "ok"
