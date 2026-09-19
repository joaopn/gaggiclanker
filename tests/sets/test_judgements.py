"""The verdict: written, replaced, withdrawn — and never lost to a sync pass.

The acceptance criterion this file exists for is one sentence: *a device note
becomes the initial
judgement exactly once and later user edits are never overwritten by sync*. It
is asserted here at the repository level and again in `test_assignment.py`
against the fake machine, because it is the one bug in this chunk that would
lose data a person typed.
"""

from __future__ import annotations

import pytest

from gaggiclanker.db.repos.judgements import JudgementWrite
from gaggiclanker.domain.models import ShotNotes
from tests.sets.conftest import Fixtures, make_shot


def _note(shot_id: str, **fields: object) -> ShotNotes:
    """A device notes card, as the firmware writes one: numbers as strings."""
    return ShotNotes.model_validate({"id": shot_id, **fields})


class TestJudgement:
    async def test_write_read_and_the_ratio_is_computed(self, wired: Fixtures) -> None:
        shot_id = await make_shot(wired.db, "000300")
        row = await wired.judgements.upsert(
            shot_id,
            JudgementWrite(
                rating=4,
                balance="sour",
                taste_notes=["sour_fermented.sour", "fruity.citrus_fruit.lemon"],
                aroma_notes=["floral.floral.jasmine"],
                dose_in_g=18.0,
                dose_out_g=36.0,
                grind_setting="22",
                notes="a bit sharp on the finish",
                decision="improve",
            ),
        )
        assert row.rating == 4
        assert row.taste_notes == ["sour_fermented.sour", "fruity.citrus_fruit.lemon"]
        assert row.aroma_notes == ["floral.floral.jasmine"]
        assert row.decision == "improve"
        assert row.ratio == 2.0
        assert row.seeded_from_device_note is False

    async def test_a_judgement_with_no_doses_has_no_ratio(self, wired: Fixtures) -> None:
        shot_id = await make_shot(wired.db, "000301")
        row = await wired.judgements.upsert(shot_id, JudgementWrite(rating=3))
        assert row.ratio is None

    async def test_upsert_replaces_rather_than_merges(self, wired: Fixtures) -> None:
        shot_id = await make_shot(wired.db, "000302")
        await wired.judgements.upsert(shot_id, JudgementWrite(rating=2, notes="undrinkable"))
        row = await wired.judgements.upsert(shot_id, JudgementWrite(rating=5))
        assert row.rating == 5
        # The form sends everything it renders, so a field left out is a field
        # the user cleared.
        assert row.notes == ""

    @pytest.mark.parametrize("field", ["taste_notes", "aroma_notes"])
    async def test_unknown_notes_are_refused_by_name(self, field: str) -> None:
        with pytest.raises(ValueError, match="delicious"):
            JudgementWrite.model_validate({field: ["fruity", "delicious"]})

    @pytest.mark.parametrize("field", ["taste_notes", "aroma_notes"])
    async def test_notes_are_de_duplicated_in_the_order_they_were_picked(self, field: str) -> None:
        picked = ["sweet", "fruity.berry", "sweet"]
        written = JudgementWrite.model_validate({field: picked})
        assert getattr(written, field) == ["sweet", "fruity.berry"]

    async def test_the_old_decision_word_is_refused(self) -> None:
        with pytest.raises(ValueError, match="decision"):
            JudgementWrite.model_validate({"decision": "adjust"})

    async def test_notes_are_capped_at_the_firmware_limit(self) -> None:
        with pytest.raises(ValueError, match="200"):
            JudgementWrite(notes="x" * 201)

    async def test_delete_puts_the_shot_back_to_unjudged(self, wired: Fixtures) -> None:
        shot_id = await make_shot(wired.db, "000303")
        await wired.judgements.upsert(shot_id, JudgementWrite(rating=3))
        assert await wired.judgements.delete(shot_id) is True
        assert await wired.judgements.get(shot_id) is None
        assert await wired.judgements.delete(shot_id) is False

    async def test_for_shots_reads_a_batch(self, wired: Fixtures) -> None:
        ids = [await make_shot(wired.db, f"00031{n}") for n in range(3)]
        await wired.judgements.upsert(ids[0], JudgementWrite(rating=5))
        await wired.judgements.upsert(ids[2], JudgementWrite(rating=1))
        found = await wired.judgements.for_shots(ids)
        assert set(found) == {ids[0], ids[2]}
        assert await wired.judgements.for_shots([]) == {}


class TestSeedingFromDeviceNotes:
    async def test_a_note_becomes_the_first_judgement(self, wired: Fixtures) -> None:
        shot_id = await make_shot(wired.db, "000320")
        created = await wired.judgements.seed_from_device_notes(
            shot_id,
            _note(
                "000320",
                rating=4,
                balanceTaste="bitter",
                doseIn="18",
                doseOut="36.5",
                grindSetting="3.2",
                notes="harsh at the end",
            ),
        )
        assert created is True
        row = await wired.judgements.get(shot_id)
        assert row is not None
        assert (row.rating, row.balance) == (4, "bitter")
        assert (row.dose_in_g, row.dose_out_g) == (18.0, 36.5)
        assert row.grind_setting == "3.2"
        assert row.notes == "harsh at the end"
        assert row.seeded_from_device_note is True
        assert row.device_synced_at is not None

    async def test_seeding_happens_exactly_once(self, wired: Fixtures) -> None:
        shot_id = await make_shot(wired.db, "000321")
        note = _note("000321", rating=3, doseIn="18")
        assert await wired.judgements.seed_from_device_notes(shot_id, note) is True
        assert await wired.judgements.seed_from_device_notes(shot_id, note) is False

    async def test_a_user_edit_is_never_overwritten_by_a_later_note(self, wired: Fixtures) -> None:
        """The one that would lose data.

        The device re-serves a note whenever its index entry changes, which is
        exactly what happens when somebody edits the shot on the machine. If
        seeding were a read-then-write, the verdict typed here would be replaced
        by the machine's version of it.
        """
        shot_id = await make_shot(wired.db, "000322")
        await wired.judgements.seed_from_device_notes(shot_id, _note("000322", rating=2))

        await wired.judgements.upsert(
            shot_id, JudgementWrite(rating=5, notes="actually excellent", decision="keep")
        )

        # Sync comes round again with a different note. Nothing moves.
        assert (
            await wired.judgements.seed_from_device_notes(
                shot_id, _note("000322", rating=1, notes="the machine disagrees")
            )
            is False
        )
        row = await wired.judgements.get(shot_id)
        assert row is not None
        assert (row.rating, row.notes) == (5, "actually excellent")
        assert row.seeded_from_device_note is False

    async def test_an_empty_note_is_not_a_judgement(self, wired: Fixtures) -> None:
        """The firmware writes the card as soon as its screen is opened.

        `rating: 0, everything empty` is what "somebody looked at it and closed
        it" looks like. Seeding from that would mark the shot judged and hide it
        from every "not judged yet" view for ever.
        """
        shot_id = await make_shot(wired.db, "000323")
        assert await wired.judgements.seed_from_device_notes(shot_id, _note("000323")) is False
        assert await wired.judgements.get(shot_id) is None

    async def test_rating_zero_means_unrated_not_zero(self, wired: Fixtures) -> None:
        shot_id = await make_shot(wired.db, "000324")
        await wired.judgements.seed_from_device_notes(shot_id, _note("000324", doseIn="18"))
        row = await wired.judgements.get(shot_id)
        assert row is not None and row.rating is None


class TestNotesTheFirmwareAcceptsAndWeDoNot:
    """The machine's notes card validates almost nothing.

    `saveNotes` stores whatever object it is handed, so `doseIn: "0"`,
    `doseIn: "150"`, `doseOut: "600"` and a grind setting longer than our column
    are all things a real machine can serve. Each one used to raise a
    `ValidationError` out of the seeding call: in the sync engine that aborted
    the rest of the notes pass (swallowed by the shots loop, so the symptom was
    "some shots have no notes and nothing in the log"), and in the importer it
    escaped after the shot had already been stored.
    """

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("doseIn", "0"),
            ("doseIn", "150"),
            ("doseOut", "600"),
            ("grindSetting", "x" * 250),
        ],
        ids=["zero dose", "implausible dose in", "implausible dose out", "very long grind"],
    )
    async def test_seeding_never_raises_and_drops_what_it_cannot_believe(
        self, wired: Fixtures, field: str, value: str
    ) -> None:
        shot_id = await make_shot(wired.db, "000330")
        # A rating too, so there is something left to seed from once the
        # unbelievable field has been dropped.
        created = await wired.judgements.seed_from_device_notes(
            shot_id, _note("000330", rating=4, **{field: value})
        )

        assert created is True
        row = await wired.judgements.get(shot_id)
        assert row is not None
        assert row.rating == 4
        if field == "doseIn":
            assert row.dose_in_g is None
        elif field == "doseOut":
            assert row.dose_out_g is None
        else:
            # Truncated rather than dropped: a grind setting is a label, so the
            # prefix is the part that means something.
            assert row.grind_setting is not None
            assert len(row.grind_setting) == 100

    async def test_a_note_with_nothing_believable_left_is_skipped_not_raised(
        self, wired: Fixtures
    ) -> None:
        shot_id = await make_shot(wired.db, "000331")

        assert (
            await wired.judgements.seed_from_device_notes(shot_id, _note("000331", doseIn="0"))
            is False
        )
        assert await wired.judgements.get(shot_id) is None

    async def test_a_user_edit_stops_claiming_to_be_in_step_with_the_machine(
        self, wired: Fixtures
    ) -> None:
        shot_id = await make_shot(wired.db, "000332")
        await wired.judgements.seed_from_device_notes(shot_id, _note("000332", rating=3))
        seeded = await wired.judgements.get(shot_id)
        assert seeded is not None and seeded.device_synced_at is not None

        edited = await wired.judgements.upsert(shot_id, JudgementWrite(rating=5))

        # The two have just diverged, so a stale timestamp would claim the
        # opposite to anything that later reconciles them.
        assert edited.device_synced_at is None
