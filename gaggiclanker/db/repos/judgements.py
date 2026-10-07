"""`shot_judgements` — what the person thought of the cup.

The other half of every shot. The machine's numbers say how the shot ran; this
says whether the coffee was any good, and the two are kept apart deliberately: a
flawless extraction of stale beans runs to its profile and tastes of cardboard,
and an archive that blended them could not tell you so.

One row per shot, keyed on the shot id. The machine's own notes card holds the
same five fields, so a shot that arrives with notes and no judgement is seeded
from them — **once**. The rule that keeps that safe is in
:meth:`JudgementsRepository.seed_from_device_notes`: it is an insert that does
nothing on conflict, so no sync pass can ever overwrite a verdict somebody
typed.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field, field_validator

from gaggiclanker.db.repos.base import dumps, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.models import ShotNotes
from gaggiclanker.domain.vocab import FLAVOR_NOTES, Balance, Decision

__all__ = [
    "JudgementWrite",
    "JudgementsRepository",
    "ShotJudgementRow",
]

log = structlog.get_logger(__name__)

#: The firmware's own cap on a notes string (`ShotNotes.notes`, max_length=200).
#: Matched here so a judgement stays writable back to the device if a later feature
#: ever enables that, and so the counter in the UI is counting against a real
#: limit rather than a made-up one.
NOTES_MAX = 200


def _within(value: float | None, low: float, high: float) -> float | None:
    """The value, or ``None`` when the machine gave us one we do not believe.

    Used only on the seeding path. A figure outside the bounds is dropped rather
    than clamped: the bounds exist because a dose of 600 g is not a dose, and a
    clamped 500 would be a number the ratio and the chat would go on to
    reason from as though somebody had meant it.
    """
    if value is None or not (low < value <= high):
        return None
    return value


class JudgementWrite(BaseModel):
    """A verdict as the API accepts it. Every field optional; all of them mean something.

    There is no "empty judgement" guard: a row with nothing but a decision is a
    legitimate thing to record ("discard, I knocked the portafilter"), and
    refusing it would make the form argue with the user.
    """

    model_config = ConfigDict(extra="forbid")

    #: 1..5. The device writes 0 for "unrated"; that is translated to ``None``
    #: on the way in, because 0 is a number somebody would average.
    rating: int | None = Field(default=None, ge=1, le=5)
    balance: Balance | None = None
    #: Flavour-wheel slugs from :data:`gaggiclanker.domain.vocab.FLAVOR_NOTES`,
    #: what the cup tasted of and what it smelt of. Validated here rather than
    #: in the schema, so a bad note is an error naming the note instead of a
    #: CHECK constraint failure naming a column.
    taste_notes: list[str] = Field(default_factory=list)
    aroma_notes: list[str] = Field(default_factory=list)
    dose_in_g: float | None = Field(default=None, gt=0, le=100)
    dose_out_g: float | None = Field(default=None, gt=0, le=500)
    notes: str = Field(default="", max_length=NOTES_MAX)
    decision: Decision | None = None

    @field_validator("taste_notes", "aroma_notes")
    @classmethod
    def _known_notes(cls, value: list[str]) -> list[str]:
        unknown = [note for note in value if note not in FLAVOR_NOTES]
        if unknown:
            raise ValueError(f"unknown flavour notes: {', '.join(sorted(set(unknown)))}")
        # De-duplicated, order preserved: the chips are a set, but the order the
        # user picked them in is the order they read back best.
        return list(dict.fromkeys(value))


class ShotJudgementRow(BaseModel):
    """One row of `shot_judgements`, as read back."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    rating: int | None = None
    balance: Balance | None = None
    taste_notes: list[str] = Field(default_factory=list, validation_alias="taste_notes_json")
    aroma_notes: list[str] = Field(default_factory=list, validation_alias="aroma_notes_json")
    dose_in_g: float | None = None
    dose_out_g: float | None = None
    notes: str = ""
    decision: Decision | None = None
    #: True while this judgement is still exactly what the machine's notes card
    #: said. Any edit through the API clears it, and that is what makes "a
    #: device note becomes the initial judgement, and user edits are never
    #: overwritten" a property of the data rather than of a code path.
    seeded_from_device_note: bool = False
    device_synced_at: str | None = None
    updated_at: str

    @field_validator("taste_notes", "aroma_notes", mode="before")
    @classmethod
    def _decode_notes(cls, value: Any) -> Any:
        if isinstance(value, str):
            decoded = json.loads(value)
            if not isinstance(decoded, list):
                raise ValueError("a note column is not a JSON array")
            return decoded
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ratio(self) -> float | None:
        """Brew ratio from the judgement's own doses, when both are known.

        Computed here rather than stored: it is a division, and a stored copy is
        a third number that can disagree with the two it came from. The device's
        notes carry their own `ratio` string, which is what the *machine* was
        told — a different fact, kept in its own table.
        """
        if not self.dose_in_g or not self.dose_out_g:
            return None
        return round(self.dose_out_g / self.dose_in_g, 2)


def _values(shot_id: int, judgement: JudgementWrite) -> dict[str, Any]:
    payload = judgement.model_dump()
    return {
        "shot_id": shot_id,
        "rating": payload["rating"],
        "balance": payload["balance"],
        "taste_notes_json": dumps(payload["taste_notes"]),
        "aroma_notes_json": dumps(payload["aroma_notes"]),
        "dose_in_g": payload["dose_in_g"],
        "dose_out_g": payload["dose_out_g"],
        "notes": payload["notes"],
        "decision": payload["decision"],
    }


class JudgementsRepository(Repository):
    """Reads and writes the user's verdicts."""

    async def upsert(self, shot_id: int, judgement: JudgementWrite) -> ShotJudgementRow:
        """Write a verdict typed by a person. Replaces whatever was there.

        `seeded_from_device_note` is forced to 0: once somebody has been through
        the form, this row is theirs, whatever it originally came from.
        """
        values = _values(shot_id, judgement)
        values["seeded_from_device_note"] = 0
        # And it is no longer in step with the machine's card either: the two
        # have just diverged, and a stale `device_synced_at` would claim the
        # opposite to anything that later reconciles them.
        values["device_synced_at"] = None
        values["updated_at"] = utc_now()
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        assignments = ", ".join(f"{name} = excluded.{name}" for name in values if name != "shot_id")
        await self.db.execute(
            f"""
            INSERT INTO shot_judgements ({columns}) VALUES ({placeholders})
            ON CONFLICT(shot_id) DO UPDATE SET {assignments}
            """,  # noqa: S608 - column names are the literal keys above, values are bound
            values,
        )
        stored = await self.get(shot_id)
        if stored is None:  # pragma: no cover - the upsert above guarantees it
            raise RuntimeError(f"judgement for shot {shot_id} vanished between write and read")
        return stored

    async def seed_from_device_notes(self, shot_id: int, notes: ShotNotes) -> bool:
        """Create a judgement from the machine's notes card, if there is not one already.

        Returns whether a row was created.

        `ON CONFLICT DO NOTHING` is the whole safety property, and it is why this
        is one statement rather than a read followed by a write: a sync pass and
        a user hitting save race each other by construction — the notes are
        re-pulled whenever the device's index entry changes, which is exactly
        what happens when the user edits the shot on the machine — and a
        check-then-insert would lose the typed verdict about once a year, which
        is the worst possible frequency for a bug like this.

        A note with nothing in it is not a judgement. The firmware creates the
        document as soon as its notes screen is opened, so "rating 0, everything
        empty" is the shape of a card somebody looked at and closed; seeding
        from it would mark the shot judged and hide it from the "not judged yet"
        filter for ever.

        **Never raises.** The machine's notes card validates almost nothing — it
        will happily store `doseIn: "0"`, `doseOut: "600"` or a grind setting
        longer than this column — and those are values our own bounds refuse. A
        `ValidationError` escaping here used to abort the rest of the notes pass
        (swallowed by the shots loop, so the symptom was "some shots have no
        notes and there is nothing in the log"), and in the importer it escaped
        *after* the shot had been stored. Out-of-range fields are dropped, and
        anything still unacceptable is logged as `judgement_seed_skipped` and
        skipped: seeding is a convenience, and losing it must never cost the
        pass it is riding on.
        """
        parsed = notes.parsed
        rating = notes.rating or None
        try:
            judgement = JudgementWrite(
                rating=rating,
                # The device's `balanceTaste` defaults to 'balanced' whether or
                # not anybody touched the control, so it is only trusted when
                # something else on the card says the card was filled in.
                balance=notes.balance_taste,
                # Dropped rather than clamped: a dose of 150 g is not 100 g of
                # coffee, it is somebody's typo or another client's unit, and
                # clamping would turn a nonsense figure into a plausible one
                # that the ratio and the chat would then reason from.
                dose_in_g=_within(parsed.dose_in, 0, 100),
                dose_out_g=_within(parsed.dose_out, 0, 500),
                notes=notes.notes[:NOTES_MAX],
            )
        except ValidationError as exc:
            log.warning("judgement_seed_skipped", shot_id=shot_id, error=str(exc))
            return False
        if not any(
            (
                judgement.rating,
                judgement.dose_in_g,
                judgement.dose_out_g,
                judgement.notes,
            )
        ):
            return False

        values = _values(shot_id, judgement)
        values["seeded_from_device_note"] = 1
        values["device_synced_at"] = values["updated_at"] = utc_now()
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        cursor = await self.db.execute(
            f"""
            INSERT INTO shot_judgements ({columns}) VALUES ({placeholders})
            ON CONFLICT(shot_id) DO NOTHING
            """,  # noqa: S608 - column names are the literal keys above, values are bound
            values,
        )
        return cursor.rowcount > 0

    async def delete(self, shot_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM shot_judgements WHERE shot_id = ?", (shot_id,))
        return cursor.rowcount > 0

    async def get(self, shot_id: int) -> ShotJudgementRow | None:
        row = await self.db.fetch_one("SELECT * FROM shot_judgements WHERE shot_id = ?", (shot_id,))
        return self.to_model(ShotJudgementRow, row)

    async def for_shots(self, shot_ids: list[int]) -> dict[int, ShotJudgementRow]:
        """Judgements for a batch of shots, keyed by shot id.

        One query for a Set page rather than one per shot: a Set with three
        versions and thirty shots would otherwise be thirty round trips to
        answer "which of these did you like".
        """
        if not shot_ids:
            return {}
        placeholders = ", ".join("?" for _ in shot_ids)
        rows = await self.db.fetch_all(
            f"SELECT * FROM shot_judgements WHERE shot_id IN ({placeholders})",  # noqa: S608 - placeholders are generated, ids are bound
            shot_ids,
        )
        return {row.shot_id: row for row in self.to_models(ShotJudgementRow, rows)}
