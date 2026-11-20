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


# ── notes the machine's web page saved under the unpadded name ───────────────

LEGACY_ID = 103


def _give_legacy_notes(archive: Archive, **fields: str | int) -> None:
    """Notes saved from the machine's web page: the file is `/h/103.json`.

    The document carries the id the page wrote, as the index shows it.
    """
    notes = {"id": str(LEGACY_ID), "rating": 4, "doseIn": "18", "doseOut": "36", **fields}
    shot = archive.device.shots[LEGACY_ID]
    shot.notes = notes
    shot.legacy_notes = True
    shot.entry.flags |= 0x04  # HAS_NOTES
    shot.entry.rating = int(notes["rating"])


def _notes_requests(archive: Archive) -> list[str]:
    return [r for r in archive.device.requests if r.endswith(".json")]


async def test_notes_saved_under_the_unpadded_name_reach_the_archive(
    small_archive: Archive,
) -> None:
    """The web page of firmware 1.9 saves `/h/103.json`; the display's name is `/h/000103.json`.

    The index flags the shot as having notes either way, so the pass has to look
    further than the padded file before it gives up.
    """
    _give_legacy_notes(small_archive, notes="from the machine's page")

    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(LEGACY_ID))
    assert shot is not None
    stored = await small_archive.engine.notes.get(shot.id)
    assert stored is not None
    assert stored.rating == 4
    assert stored.notes == "from the machine's page"
    # And, as with any notes that arrive with a shot, they seed its judgement.
    judgement = await small_archive.engine.judgements.get(shot.id)
    assert judgement is not None
    assert (judgement.rating, judgement.dose_in_g) == (4, 18.0)
    assert judgement.seeded_from_device_note is True
    runs = await small_archive.engine.runs.last_runs()
    assert (runs["notes"].status, runs["notes"].errors) == ("ok", 0)


async def test_the_legacy_notes_are_not_asked_for_again_once_held(
    small_archive: Archive,
) -> None:
    _give_legacy_notes(small_archive)

    await small_archive.engine.sync_shots(trigger="test")
    await small_archive.engine.sync_shots(trigger="test")

    legacy = [r for r in _notes_requests(small_archive) if r.endswith(f"/{LEGACY_ID}.json")]
    assert len(legacy) == 1


async def test_a_notes_file_the_machine_does_not_have_is_no_notes_not_an_error(
    small_archive: Archive,
) -> None:
    """The machine answers a missing file with its web page and a 200, not a 404.

    The index flags the shot as having notes but neither name is on the machine.
    Nothing is stored, no error is counted, and the next pass asks again.
    """
    small_archive.device.shots[LEGACY_ID].entry.flags |= 0x04  # HAS_NOTES, no file anywhere

    await small_archive.engine.sync_shots(trigger="test")
    first = _notes_requests(small_archive)
    await small_archive.engine.sync_shots(trigger="test")
    second = _notes_requests(small_archive)

    shot = await small_archive.engine.shots.get_by_device_id(pad6(LEGACY_ID))
    assert shot is not None
    assert await small_archive.engine.notes.get(shot.id) is None
    assert await small_archive.engine.judgements.get(shot.id) is None
    runs = await small_archive.engine.runs.last_runs()
    assert (runs["notes"].status, runs["notes"].errors) == ("ok", 0)
    for name in (pad6(LEGACY_ID), str(LEGACY_ID)):
        assert first.count(f"/api/history/{name}.json") == 1
    # Both names again on the second pass.
    for name in (pad6(LEGACY_ID), str(LEGACY_ID)):
        assert second.count(f"/api/history/{name}.json") == 2


async def test_an_error_on_the_unpadded_file_is_one_error_and_the_pass_goes_on(
    small_archive: Archive,
) -> None:
    """The padded file is not there (the web page), then the unpadded one fails.

    That is a failed request, not an absent file: one error, nothing stored for
    the shot, and the shots after it still get their notes.
    """
    small_archive.device.shots[LEGACY_ID].entry.flags |= 0x04  # HAS_NOTES, no file
    small_archive.device.error_paths[f"/api/history/{LEGACY_ID}.json"] = 500
    after = small_archive.device.shots[LEGACY_ID + 1]
    after.notes = {"id": pad6(LEGACY_ID + 1), "rating": 3, "doseIn": "18"}
    after.entry.flags |= 0x04
    after.entry.rating = 3

    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(LEGACY_ID))
    assert shot is not None
    assert await small_archive.engine.notes.get(shot.id) is None
    later = await small_archive.engine.shots.get_by_device_id(pad6(LEGACY_ID + 1))
    assert later is not None
    assert await small_archive.engine.notes.get(later.id) is not None
    runs = await small_archive.engine.runs.last_runs()
    assert runs["notes"].errors == 1
    assert runs["notes"].notes_synced == 2  # the fixture's own card and the one after


async def test_a_notes_file_that_is_not_json_is_an_error_not_no_notes(
    small_archive: Archive,
) -> None:
    shot_on_device = small_archive.device.shots[LEGACY_ID]
    shot_on_device.notes_body = b'{"rating": 4, "doseIn": '
    shot_on_device.entry.flags |= 0x04  # HAS_NOTES

    await small_archive.engine.sync_shots(trigger="test")

    shot = await small_archive.engine.shots.get_by_device_id(pad6(LEGACY_ID))
    assert shot is not None
    assert await small_archive.engine.notes.get(shot.id) is None
    runs = await small_archive.engine.runs.last_runs()
    assert runs["notes"].errors == 1
    assert "not JSON" in (runs["notes"].error or "")
