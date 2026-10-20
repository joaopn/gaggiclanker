"""`recipes` and `recipe_versions` — what you were trying, and what you changed.

A **Recipe** is a stable identity: this bean, on the machine, through this
grinder. A **recipe version** is one concrete recipe that was actually brewed with
— a profile version, a grind, a dose, a target yield — plus the one sentence
saying why it differs from the version before it. The brew temperature is not
among them: the machine heats to what the profile says, so it is read from the
profile this version names and changing it means changing the profile.

Four properties follow from that split, and every method here exists to keep
one of them true:

* **the recipe is immutable.** Nothing updates the recipe half of a
  `recipe_versions` row. Changing anything appends a new version whose
  `parent_version_id` is the one it came from, because the product's whole
  question is "what did changing this do" and an edited row answers it with
  today's value for every shot ever attached.
* **a prediction is written before the shots or not at all.** What a version was
  expected to do differently can be typed and re-typed while the version has no
  shots, and is refused afterwards: a prediction written once the cup has been
  tasted is a memory, not a prediction. It is refused again once a grade has
  been recorded, which the grade has to be taken back first. The **outcome** —
  somebody's grade of that prediction — is the opposite: it is only recordable
  once there is something to grade, and it can be changed or taken back at any
  time.

  Both windows live here, in :meth:`RecipesRepository.set_prediction` and
  :meth:`RecipesRepository.set_outcome`, beside the other rules that depend on
  another table's rows. A trigger could enforce the first one, and that is
  exactly why it does not: the rule would then exist in two places with two
  wordings, the refusal would arrive as a constraint failure rather than as an
  error code a route can turn into a sentence, and a test of it would have to go
  through SQL rather than through the method everything else calls.

  The window is honest, not airtight: somebody who unfiles every shot from a
  version and clears its grade can write a fresh prediction on it. That is
  accepted. The rule guards against the habit of writing a prediction down after
  the fact, not against somebody setting out to deceive themselves.
* **a Recipe says whether shots may be filed under it** (`automatch`), and any
  number of them may say yes: a kitchen with several grinders has several
  coffees loaded at once, and each Recipe has a profile of its own. The Recipe whose
  current version names the shot's profile version files it; none leaves it in
  the inbox.
* **a shot's Recipe is never guessed twice.** The matcher only ever writes over a
  NULL, so a correction made by hand survives every later sync pass.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import version_name_sql
from gaggiclanker.db.repos.shots import NEEDS_RECIPE_SQL
from gaggiclanker.db.repos.version_names import (
    label_sql,
    names_from_state,
    next_names,
    next_numbers,
    state_sql,
)
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.ratio import brew_ratio
from gaggiclanker.domain.recipes import version_label
from gaggiclanker.domain.slog import FIELD_DEFS
from gaggiclanker.domain.spread import CountedShot
from gaggiclanker.domain.vocab import (
    VERSION_OUTCOMES,
    Decision,
    OutcomeState,
    SetVersionOrigin,
    VersionOutcome,
)

log = structlog.get_logger(__name__)

__all__ = [
    "RECIPE_FIELDS",
    "SEARCH_COLUMNS",
    "VERSION_FIELDS",
    "DesignBrief",
    "DesignRefusal",
    "FieldChange",
    "LiveRecipe",
    "ProfileMatch",
    "ProfileMatchSummary",
    "RecipeShotRow",
    "RecipeVersionChange",
    "RecipeVersionPatch",
    "RecipeWrite",
    "RecipesRepository",
    "RollbackWrite",
    "SetRevertRow",
    "SetRow",
    "SetTrackRecord",
    "SetTrends",
    "SetVersionRow",
    "SetVersionWrite",
    "VersionLabelCounts",
    "VersionLink",
    "VersionNode",
    "VersionOutcomeWrite",
    "VersionPredictionWrite",
    "VersionRefusal",
    "VersionRefused",
    "VersionWriteResult",
    "dead_end_ids",
    "track_record",
    "version_changes",
]

#: How long a prediction or an outcome note may be. Generous for a sentence or
#: three and short of an essay: this is read beside a shot, not filed.
TEXT_MAX = 1000

#: A prediction or an outcome note as it is accepted: stripped, then capped.
#: Stripped on the model rather than at each call site, so a field somebody left
#: as three spaces is "no prediction" everywhere at once — including in
#: `outcome_state`, which asks whether the text is empty.
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=TEXT_MAX)]


class RecipeWrite(BaseModel):
    """The identity half of a Recipe: what it is made of, not what it is set to."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    bean_id: int
    #: Nullable: pre-ground coffee and a grinder nobody has got round to
    #: recording are both real, and refusing the Recipe over it helps nobody.
    grinder_id: int | None = None


class DesignBrief(BaseModel):
    """What the person asked for when they set out to design a Set in chat.

    Stored on the Set (`sets.design_brief`) rather than only sent as the first
    message, because the conversation reads it on every turn: a long design
    discussion trims its oldest messages, and the profile to fork from and the
    goal must not go with them. It stays after the design is done, as the
    record of what was asked for.
    """

    model_config = ConfigDict(extra="forbid")

    #: The profile version the new profile is derived from. Never the Recipe's
    #: own profile: forking means a new profile, and the source and the Recipe
    #: that brews it are left as they are.
    fork_profile_version_id: int | None = None
    #: Where they grind espresso on this grinder, in its own units. The one
    #: anchor that lets a proposed grind be a number on the dial.
    usual_grind: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    #: "What do you want from it?", in their words.
    goal: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    #: The recipe numbers the person already filled in on the form, each empty when they left it
    #: to the agent. They are the person's choice, so the design keeps them and works out the
    #: rest; a brief stored before these existed reads as all three left open, which it was.
    #: Same limits as a version's recipe, so a kept number can always be accepted.
    grind_setting: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    dose_g: float | None = Field(default=None, gt=0, le=100)
    target_yield_g: float | None = Field(default=None, gt=0, le=500)


class SetVersionWrite(BaseModel):
    """A complete recipe. What `POST /api/sets` stores as version 1."""

    model_config = ConfigDict(extra="forbid")

    #: Which profile version this Recipe's first recipe brews, which is how the Recipe gets its
    #: profile (a profile that has a Recipe already is refused). NULL means the Recipe names no
    #: profile, and then no shot is ever filed here on its own — the Recipe is picked by hand.
    profile_version_id: int | None = None
    #: The grinder's own reading, as text: a Niche says "22", a Mazzer says
    #: "between 3 and 4". `grind_value` is the same thing as a number when there
    #: is one, so a chart has something to plot.
    grind_setting: str | None = Field(default=None, max_length=100)
    grind_value: float | None = Field(default=None, ge=0, le=10000)
    dose_g: float | None = Field(default=None, gt=0, le=100)
    target_yield_g: float | None = Field(default=None, gt=0, le=500)
    intent: str = Field(default="", max_length=500)
    #: What this version is expected to do differently. On a Recipe's first version
    #: there is nothing earlier to compare against, so there is no
    #: `compares_to_version_id` here: the prediction is graded against the
    #: numbers this version itself states.
    prediction: LongText = ""
    origin: SetVersionOrigin = "manual"
    #: The analysis whose accepted suggestion produced this version, for a
    #: version made before the per-shot analysis was retired; the carried
    #: review of the same id when that analysis finished. Nothing sets it now
    #: but a push of such an older draft.
    origin_analysis_id: int | None = None


class RecipeVersionChange(BaseModel):
    """What a person or an agent may change on a Recipe version: only what changed.

    Every field is optional *and* "not sent" is distinguishable from "sent as
    null", which is the whole point — omitting `dose_g` inherits the parent's
    dose, sending `null` clears it. That is what ``exclude_unset`` in
    :meth:`RecipesRepository.add_version` reads. There is no profile among them: a Recipe
    brews one profile, and a different brew is a new version of that profile, which the
    Recipe follows (:meth:`RecipesRepository.follow_profile`).
    """

    model_config = ConfigDict(extra="forbid")

    grind_setting: str | None = Field(default=None, max_length=100)
    grind_value: float | None = Field(default=None, ge=0, le=10000)
    dose_g: float | None = Field(default=None, gt=0, le=100)
    target_yield_g: float | None = Field(default=None, gt=0, le=500)
    #: Not inherited, and asked for on every new version: a change with no
    #: stated intent is indistinguishable from a typo three weeks later.
    intent: str = Field(default="", max_length=500)
    #: Not inherited either, and for a stronger reason: a prediction belongs to
    #: one version's one change, and carrying the last one forward would put a
    #: guess nobody made on the record.
    prediction: LongText = ""
    #: Which version the prediction is measured against. **Omitted** it is the
    #: parent — the version this one was changed from, which is what "less
    #: bitter than before" means nine times in ten. Sent as **null** it is
    #: nothing: the prediction is graded on the numbers this version states.
    #: The two are told apart by ``exclude_unset``, like every other field here.
    compares_to_version_id: int | None = None
    origin: SetVersionOrigin = "manual"
    origin_analysis_id: int | None = None


class RecipeVersionPatch(RecipeVersionChange):
    """A version to append: the change, and the profile version it brews when that moved.

    Only the code that makes a Recipe follow its profile (and a design's first recipe) names a
    profile version; nothing a person or an agent sends does.
    """

    profile_version_id: int | None = None


class SetVersionRow(BaseModel):
    """One row of `set_versions`, with the labels a reader needs beside it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    #: The version's name, v<major>.<minor>. A dial-in change bumps the minor,
    #: a functional change starts the next major (`domain/recipes.py`).
    version_major: int
    version_minor: int = 0
    parent_version_id: int | None = None
    #: The parent's name, joined in: "from v1.2" in the log.
    parent_version_label: str | None = None
    #: Whether this is the version the Recipe is on now. A revert moves it, so it is
    #: not "the newest": it is read off the Recipe's pointer.
    is_current: bool = False
    profile_version_id: int | None = None
    #: The profile's name, joined in (``version_name_sql``: the name of the profile that owns
    #: the version, never the name the version's document was first stored with). A version that
    #: names a profile the mirror has since dropped still has its id; the label is then NULL.
    profile_label: str | None = None
    grind_setting: str | None = None
    grind_value: float | None = None
    dose_g: float | None = None
    target_yield_g: float | None = None
    #: The brew temperature, read from the profile this version names rather
    #: than typed on the Recipe: the machine heats to what the document says, so a
    #: number stored here could only ever disagree with the cup. NULL when the
    #: version names no profile, or when the profile states none (the firmware
    #: writes 0 for "not set"). Read-only — changing it means changing the
    #: profile.
    profile_temperature_c: float | None = None
    intent: str = ""
    origin: SetVersionOrigin = "manual"
    origin_analysis_id: int | None = None
    #: What this version was expected to do differently, in the person's words.
    #: Empty is the common case and means exactly that: nobody committed to a
    #: guess, so there is nothing here to be right or wrong about.
    prediction: str = ""
    compares_to_version_id: int | None = None
    #: The compared-to version's name, joined in. A reader — and a model reading
    #: the curated view — thinks in "v1.1", never in a row id.
    compares_to_version_label: str | None = None
    #: The version whose recipe this one restores, when it came from a roll
    #: back made before a revert became a move of the Recipe's pointer (nothing
    #: writes it any more), and its name beside it.
    restores_version_id: int | None = None
    restores_version_label: str | None = None
    prediction_at: str | None = None
    outcome: VersionOutcome | None = None
    outcome_note: str = ""
    outcome_at: str | None = None
    created_at: str
    shot_count: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def version_label(self) -> str:
        """ "v1", "v1.1": the name every screen, tool and prompt shows."""
        return version_label(self.version_major, self.version_minor)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def outcome_state(self) -> OutcomeState:
        """What the experiment log shows for this version.

        Derived rather than stored, because it is two columns read together and
        a stored copy would be a third value that can disagree with both. The
        distinction that matters is between "nobody predicted anything" and
        "somebody predicted something and has not said how it went": the first
        is not a gap in the record, the second is.
        """
        if not self.prediction:
            return "no_prediction"
        return self.outcome or "open"


class VersionPredictionWrite(BaseModel):
    """`PATCH .../versions/{id}/prediction`: the guess, and what it is against.

    ``prediction`` is **required**, with no default: an empty string is how one
    is taken back, and that has to be something a caller says rather than
    something a caller omits. A body with neither is a mistake, not a removal,
    and it is answered as one. The compared-to version and the timestamp go with
    the text when it goes, because a comparison with nothing to compare would be
    a dangling reference nobody can read.

    ``compares_to_version_id`` follows :class:`SetVersionPatch`: omitted is the
    parent, an explicit null is "nothing to compare against".
    """

    model_config = ConfigDict(extra="forbid")

    prediction: LongText
    compares_to_version_id: int | None = None


class VersionOutcomeWrite(BaseModel):
    """`PUT .../versions/{id}/outcome`: the grade, and why it was given."""

    model_config = ConfigDict(extra="forbid")

    outcome: VersionOutcome
    note: LongText = ""


class RollbackWrite(BaseModel):
    """`POST /api/sets/{id}/rollback`: go back to a version that worked.

    A revert writes no version: the Set is on the target again, as it was, with
    its prediction, shots and outcome untouched. So it takes no prediction (the
    target already has one, or has shots), and ``extra="forbid"`` makes a body
    that still carries one a 422 rather than a silently dropped field.

    Nothing is written to the machine by this, ever. It is a statement about the
    archive — "this is what I am brewing again" — and putting a different
    profile on the machine stays a separate, deliberate act.
    """

    model_config = ConfigDict(extra="forbid")

    to_version_id: int
    #: Why, for the log. Optional.
    note: str = Field(default="", max_length=500)


class SetRevertRow(BaseModel):
    """One time a Set went back to another version: a line of the log, not a version."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    from_version_id: int
    from_version_label: str
    to_version_id: int
    to_version_label: str
    note: str = ""
    created_at: str


class VersionLabelCounts(BaseModel):
    """How the shots on one version were labelled, for the log's one line."""

    model_config = ConfigDict(extra="forbid")

    keep: int = 0
    improve: int = 0
    discard: int = 0
    #: Shots on this version with no decision yet — judged or not.
    unlabelled: int = 0


class SetTrackRecord(BaseModel):
    """How often this Set's predictions turned out right.

    ``graded`` is the four recorded outcomes together, which is the denominator
    of the sentence the page leads with. The un-graded two are counted as well
    rather than folded away: "four of six held" reads very differently beside
    "and nine versions predicted nothing".
    """

    model_config = ConfigDict(extra="forbid")

    no_prediction: int = 0
    open: int = 0
    held: int = 0
    partly_held: int = 0
    failed: int = 0
    inconclusive: int = 0
    graded: int = 0


#: Why a guarded write to a version was refused. A slug rather than an HTTP
#: status: the repository has no opinion about statuses, and the route that
#: does is the one place the mapping is written down.
type VersionRefusal = Literal[
    "no_version",
    "has_shots",
    "has_outcome",
    "bad_compare",
    "no_prediction",
    "nothing_to_grade",
    "no_target",
    "current_version",
    "designing",
    "design_has_versions",
    "design_has_shots",
    "profile_is_a_pin",
    "profile_has_a_set",
    "other_profile",
    "not_the_active_version",
    "profile_deleted",
]

#: Why a Recipe being designed cannot take the write asked of it. The last two
#: are shared with :data:`VersionRefusal`, because a version written to a Recipe
#: being designed is refused for exactly the reasons discarding it would be.
type DesignRefusal = Literal[
    "no_recipe",
    "not_designing",
    "design_has_versions",
    "design_has_shots",
]

#: What each design refusal says, once, for every caller that has to say it:
#: the Add a version route, a push for a Recipe, an accepted suggestion, an
#: accepted initial recipe and the discard route all meet the same two.
_DESIGN_REFUSALS: dict[str, str] = {
    "design_has_versions": (
        "This Set is being designed but already has more than one version, so there is no "
        "empty version 1 left to fill"
    ),
    "design_has_shots": (
        "This Set is being designed and a shot is already filed on it, so its version 1 is no "
        "longer an empty recipe; unfile the shot first"
    ),
    "designing": "This Set is being designed: there is nothing to go back to yet",
    "not_designing": "This Set is not being designed",
    "no_recipe": "There is no such Set",
    "profile_is_a_pin": (
        "That profile is the frozen copy of a pinned version. A Set version names the profile the "
        "copy was made from, never the copy"
    ),
    "profile_has_a_set": "That profile already has a Set",
    "other_profile": "A Set brews one profile: that profile version belongs to another",
    "profile_deleted": "That profile was deleted from the list: pick one that is on it",
    "not_the_active_version": (
        "A Set starts on a profile's active version: pick a profile that has no Set"
    ),
}


class VersionRefused(Exception):
    """A version write refused from **inside** the caller's transaction.

    :meth:`RecipesRepository.append_version` runs in a transaction somebody else
    opened, alongside their own reads and writes (accepting a proposal marks
    the proposal in the same one). A refusal returned as a value there would
    leave the caller to remember to roll its own writes back; raised, it rolls
    the whole transaction back by construction. The slug is the same kind of
    answer the version writes give as values — one code, one sentence — and
    the caller turns it into its status.
    """

    def __init__(self, refused: VersionRefusal, message: str | None = None) -> None:
        self.refused: VersionRefusal = refused
        self.message = message or _DESIGN_REFUSALS.get(refused, refused)
        super().__init__(self.message)

    @property
    def code(self) -> str:
        """The error code a route answers with: the slug, shouted."""
        return self.refused.upper()


def design_refusal_message(refused: DesignRefusal | VersionRefusal) -> str:
    """The sentence a design refusal is said in, for callers answering with a value."""
    return _DESIGN_REFUSALS[refused]


@dataclass(frozen=True, slots=True)
class VersionWriteResult:
    """A guarded write: the row it produced, or the reason there is none.

    Deliberately not an exception. Every refusal here is an ordinary answer to
    an ordinary request — the version already has shots, the prediction it would
    grade does not exist — and a route turns each one into its own status and
    message. Exactly one of the two fields is set.
    """

    version: SetVersionRow | None = None
    refused: VersionRefusal | None = None


@dataclass(frozen=True, slots=True)
class LiveRecipe:
    """A profile's Recipe, for a page that warns before the profile changes."""

    set_id: int
    name: str
    #: The profile version the Recipe's current version brews.
    brewing_version_id: int | None
    #: The Recipe's current version's name, "v1.2".
    version_label: str
    #: What a change of the profile's active version would make the Recipe's next version.
    next_minor_label: str


class ProfileMatch(BaseModel):
    """What :meth:`RecipesRepository.profile_match` did with one shot."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["matched", "unmatched"]
    #: The version the shot was filed under; only set when ``matched``.
    set_version_id: int | None = None


class ProfileMatchSummary(BaseModel):
    """What one press of "Match by profile" did, counted by outcome."""

    model_config = ConfigDict(extra="forbid")

    #: Filed under the Recipe that brews their profile.
    matched: int = 0
    #: No Recipe brews their profile, or the archive does not know which it was.
    unmatched: int = 0


class CopiedFrom(BaseModel):
    """The "copied from" note of a Set: the version its first recipe was copied from, by name."""

    model_config = ConfigDict(extra="forbid")

    set_id: int
    set_name: str
    version_id: int
    version_label: str


class SetRow(BaseModel):
    """One row of `sets`, with the current version and the joined names."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    bean_id: int
    bean_name: str | None = None
    grinder_id: int | None = None
    grinder_name: str | None = None
    #: Finished with — the bean is done with. An archived Recipe receives no shots.
    archived: bool = False
    #: Whether the matcher may file a shot under this Recipe. Any number of Recipes
    #: hold it; archiving clears it.
    automatch: bool = False
    #: Created by the design path and waiting for its first recipe: version 1
    #: states nothing yet and its conversation is answered with the design
    #: prompt. Cleared by the first version written to the Recipe.
    designing: bool = False
    #: What the person asked for when the design started. Empty on every Recipe
    #: made any other way.
    design_brief: DesignBrief = Field(default_factory=DesignBrief)
    created_at: str
    #: The version the Recipe is on: the pointer on the Recipe's own row, moved by
    #: every append and every revert.
    current_version_id: int | None = None
    #: The current version's name, "v1.2"; empty only for a Recipe with no
    #: version, which does not exist outside a half-written transaction.
    current_version_label: str = ""
    #: What the next version would be called as a minor ("v1.3") and as a
    #: major ("v2"), for every button and form that says which version it will
    #: record. Worked out in this row's own query with the arithmetic the
    #: insert uses, so the web never numbers anything. Both v1 while the Recipe is
    #: being designed: the next write fills version 1.
    next_minor_label: str = ""
    next_major_label: str = ""
    version_count: int = 0
    shot_count: int = 0
    profile_version_id: int | None = None
    #: The Recipe's profile: the one profile whose versions every version of the Recipe brews, and
    #: that no other Recipe has. ``None`` while the Recipe is being designed, and for an old Recipe
    #: that never named a profile.
    board_id: int | None = None
    #: The profile's name (the Recipe's profile row; for a Recipe with no profile, what the current
    #: version is called: ``version_name_sql``).
    profile_label: str | None = None
    #: Where this Recipe's first recipe was copied from, when it was started as a copy of another
    #: Recipe's version. Display only: it is never read as evidence, never handed to an agent
    #: (:func:`~gaggiclanker.db.repos.version_names.named_dump` leaves it out), and it is empty
    #: once that version's Recipe is discarded.
    copied_from: CopiedFrom | None = None

    @model_validator(mode="before")
    @classmethod
    def _name_the_next_version(cls, data: Any) -> Any:
        """Turn the three numbering columns of the row into the two names.

        The columns are selected flat (there is no other way to get them out of
        one query) and are not part of the row anybody reads: what a reader
        needs is the names, and the arithmetic stays in one function.
        """
        if not isinstance(data, dict) or "current_major" not in data:
            return data
        payload = dict(data)
        names = names_from_state(
            designing=bool(payload.get("designing")),
            current_major=payload.pop("current_major"),
            current_major_minor_max=payload.pop("current_major_minor_max", None),
            highest_major=payload.pop("highest_major", None),
        )
        payload["next_minor_label"] = names.minor
        payload["next_major_label"] = names.major
        return payload

    @model_validator(mode="before")
    @classmethod
    def _name_the_note(cls, data: Any) -> Any:
        """Turn the columns that name where the Recipe was copied from into one note, or none."""
        if not isinstance(data, dict) or "copied_from_set_id" not in data:
            return data
        payload = dict(data)
        set_id = payload.pop("copied_from_set_id")
        set_name = payload.pop("copied_from_recipe_name", None)
        version_label = payload.pop("copied_from_version_label", None)
        version_id = payload.pop("copied_from_version_id", None)
        payload["copied_from"] = (
            None
            if set_id is None or version_id is None
            else {
                "set_id": set_id,
                "set_name": set_name,
                "version_id": version_id,
                "version_label": version_label,
            }
        )
        return payload

    @field_validator("design_brief", mode="before")
    @classmethod
    def _decode_brief(cls, value: Any) -> Any:
        """The stored JSON, back as the brief model — or an empty brief and a log line.

        Not a validation error, for the reason a damaged proposal is not one:
        this row is read by the Recipes list, and one hand-edited brief must not
        be a page that will not load. What it costs is the brief itself, and
        the log line is how somebody finds out it happened.
        """
        if not isinstance(value, str):
            return value
        try:
            return DesignBrief.model_validate_json(value)
        except ValidationError:
            log.warning("recipe_design_brief_unreadable")
            return DesignBrief()


class FieldChange(BaseModel):
    """One difference between a version and its parent."""

    model_config = ConfigDict(extra="forbid")

    field: str
    label: str
    before: str | None = None
    after: str | None = None
    #: True for a change nobody typed on the Recipe: it came with the profile this
    #: version switched to. The log says so, because "Temperature 93 → 94 °C"
    #: with no such field on the form reads as a bug otherwise.
    from_profile: bool = False


class RecipeShotRow(BaseModel):
    """One shot of a Recipe as it is read back one line at a time.

    The eight measures the spread is worked out from, the person's verdict, and
    the two flags that say a shot's numbers are not to be trusted. Everything
    optional, because every one of them is something that may not have been
    recorded — and a missing number is a fact, never a zero.
    """

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    version_id: int
    version_label: str
    started_at: str | None = None
    #: Never parsed; its numbers are whatever the header happened to hold.
    quarantined: bool = False
    #: Stopped early, so its shot time measures the interruption.
    incomplete: bool = False

    shot_time_s: float | None = None
    cup_first_drip_s: float | None = None
    first_drip_s: float | None = None
    yield_g: float | None = None
    peak_pressure_bar: float | None = None
    cup_flow_g_s: float | None = None
    brew_flow_ml_s: float | None = None

    rating: int | None = None
    balance: str | None = None
    decision: Decision | None = None
    notes: str = ""
    taste_notes: list[str] = Field(default_factory=list, validation_alias="taste_notes_json")
    aroma_notes: list[str] = Field(default_factory=list, validation_alias="aroma_notes_json")

    @field_validator("notes", mode="before")
    @classmethod
    def _no_notes_is_empty(cls, value: Any) -> Any:
        """A shot with no judgement row at all joins to NULL, not to ''."""
        return "" if value is None else value

    @field_validator("taste_notes", "aroma_notes", mode="before")
    @classmethod
    def _decode_notes(cls, value: Any) -> Any:
        """The JSON array as stored, read the way the judgement row reads it."""
        if value is None:
            return []
        if isinstance(value, str):
            decoded = json.loads(value)
            if not isinstance(decoded, list):
                raise ValueError("a note column is not a JSON array")
            return decoded
        return value


class SetTrendPoint(BaseModel):
    """One shot on the Set's trend chart."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    device_id: str
    set_version_id: int
    version_label: str
    started_at: str | None = None
    duration_s: float | None = None
    #: Yield over dose. The dose is the judgement's when the person entered one and
    #: the filed version's otherwise; the yield is the judgement's when entered
    #: and the scale's otherwise. No dose anywhere, no ratio: the nominal basket
    #: size is not a dose.
    ratio: float | None = None
    rating: int | None = None


class SetTrendCounts(BaseModel):
    """How many shots each of a version's means is over.

    A mean is over the counted shots that have the value: a shot nobody rated
    adds nothing to the rating, a machine with no pressure sensor nothing to the
    first drip. Next to the version's counted total this says which means rest on
    fewer shots than the version has.
    """

    model_config = ConfigDict(extra="forbid")

    duration_s: int = 0
    yield_g: int = 0
    ratio: int = 0
    rating: int = 0
    first_drip_s: int = 0


class SetTrendVersion(BaseModel):
    """One version's averages: what `get_set`'s trajectory and the chat's ledger read.

    The averages are over the version's **counted** shots (the ones the spread
    and the evidence use: not quarantined, not incomplete, not a Discard), so a
    knocked-over cup does not pull a bar down. ``shots`` is every shot filed
    under the version, ``counted_shots`` the ones the means can be over.
    """

    model_config = ConfigDict(extra="forbid")

    set_version_id: int
    version_label: str
    intent: str = ""
    origin: str = "manual"
    created_at: str
    shots: int = 0
    counted_shots: int = 0
    avg_duration_s: float | None = None
    avg_yield_g: float | None = None
    avg_ratio: float | None = None
    avg_rating: float | None = None
    avg_first_drip_s: float | None = None
    averaged_over: SetTrendCounts = SetTrendCounts()


class SetTrends(BaseModel):
    """`GET /api/sets/{id}/trends`: the per-version summary and every shot."""

    model_config = ConfigDict(extra="forbid")

    set_id: int
    versions: list[SetTrendVersion]
    shots: list[SetTrendPoint]


#: The recipe fields, with the label the timeline shows and how to render a
#: value. One table, because the diff, the inheritance in `add_version` and the
#: UI's own wording all have to agree on what "the recipe" is.
VERSION_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("profile_version_id", "Profile", "profile"),
    ("grind_setting", "Grind", "text"),
    ("grind_value", "Grind value", "number"),
    ("dose_g", "Dose", "g"),
    ("target_yield_g", "Target yield", "g"),
)


def _render(value: Any, kind: str, labels: dict[int, str] | None = None) -> str | None:
    if value is None:
        return None
    if kind == "profile":
        label = (labels or {}).get(int(value))
        return label or f"#{int(value)}"
    if kind == "g":
        return f"{float(value):g} g"
    if kind == "c":
        return f"{float(value):g} °C"
    if kind == "number":
        return f"{float(value):g}"
    return str(value)


def version_changes(
    version: SetVersionRow,
    parent: SetVersionRow | None,
    profile_labels: dict[int, str] | None = None,
) -> list[FieldChange]:
    """What this version changed, against the one it came from.

    Computed rather than stored. A stored diff is a third copy of two values
    that can disagree with either, and this one is cheap: five fields compared
    in memory on a list the page already holds.

    The first version of a Recipe has no parent and therefore no changes — it is
    the baseline, not a change to anything, and rendering it as "five fields
    set" would bury the one version the reader actually wants to compare
    against.

    The temperature is the sixth line the log can show and the one nobody typed:
    it rides along with the profile. A version whose profile version changed
    changed the brew temperature too whenever the two documents state different
    ones, and that is the change the reader is looking for — "one degree hotter"
    is the profile's doing, so it is only ever reported beside the "Brew changed"
    line that caused it.
    """
    if parent is None:
        return []
    changes: list[FieldChange] = []
    for field, label, kind in VERSION_FIELDS:
        before = getattr(parent, field)
        after = getattr(version, field)
        if before == after:
            continue
        if field == "profile_version_id" and before is not None and after is not None:
            # A Recipe brews one profile, so both are versions of it and carry its name: the log
            # says the brew changed, not "Foo → Foo".
            changes.append(FieldChange(field=field, label="Brew changed", before=None, after=None))
            continue
        changes.append(
            FieldChange(
                field=field,
                label=label,
                before=_render(before, kind, profile_labels),
                after=_render(after, kind, profile_labels),
            )
        )
    if (
        version.profile_version_id != parent.profile_version_id
        and version.profile_temperature_c != parent.profile_temperature_c
    ):
        changes.append(
            FieldChange(
                field="profile_temperature_c",
                label="Temperature",
                before=_render(parent.profile_temperature_c, "c"),
                after=_render(version.profile_temperature_c, "c"),
                from_profile=True,
            )
        )
    return changes


def _prediction_values(prediction: str, compares_to: int | None, *, now: str) -> dict[str, Any]:
    """The three prediction columns, which are written and cleared together.

    An empty prediction takes its comparison and its timestamp with it: a
    `compares_to_version_id` with no prediction beside it is a reference to a
    version nothing says anything about, and the log would have to render it as
    something.

    ``compares_to`` is already resolved by the caller, because only the caller
    can tell "the field was not sent" (inherit the parent) from "the field was
    sent as null" (compare against nothing, and grade the version on the numbers
    it states). Defaulting here would make the two indistinguishable, which is
    the bug this signature exists to prevent.
    """
    if not prediction:
        return {"prediction": "", "compares_to_version_id": None, "prediction_at": None}
    return {
        "prediction": prediction,
        "compares_to_version_id": compares_to,
        "prediction_at": now,
    }


class VersionNode(Protocol):
    """What walking a Recipe's line needs off a version, and nothing else.

    A structural type rather than :class:`SetVersionRow` because two callers
    want two different rows: the Recipe page holds the full version and the Chat
    page's folders want four columns for several Recipes at once. One walk, either
    row — the alternative is a second copy of the rule, and a dead end the log
    shows and the folder does not is exactly the disagreement that would follow.
    """

    id: int
    parent_version_id: int | None
    restores_version_id: int | None


class VersionLink(BaseModel):
    """One version as the line walk reads it, for many Recipes in one query."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    parent_version_id: int | None = None
    restores_version_id: int | None = None


def live_line(versions: Sequence[VersionNode], current_id: int | None) -> list[int]:
    """The versions still on the line being brewed, newest first.

    Walked back from the current version (the Recipe's pointer, which a revert
    moves) rather than filtered: a version is on the line if you can reach it by
    stepping backwards from where the Recipe is now, through the fork history
    (`parent_version_id`), as in version control. A row an old roll back wrote
    (`restores_version_id`) steps to what it restored instead: everything in
    between was stepped over.

    Reaching an id twice would be a cycle in data that should have none, and the
    walk stops rather than spinning: a malformed row must not hang the Recipe page.
    """
    by_id = {version.id: version for version in versions}
    node = by_id.get(current_id or 0)
    line: list[int] = []
    seen: set[int] = set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        line.append(node.id)
        node = by_id.get(node.restores_version_id or node.parent_version_id or 0)
    return line


def dead_end_ids(versions: Sequence[VersionNode], current_id: int | None) -> set[int]:
    """The versions that are not on the live line.

    After a revert from v2.1 to v1.2 the Recipe is on v1.2, and v2 and v2.1 are
    not wrong — they were real attempts — but a branch nobody is on any more, and
    the log mutes them so the line a reader follows is the one still being
    brewed. Walking the line is the definition; a span test is an approximation
    that stops agreeing as soon as reverts overlap.

    Pure, over the list the page already holds.
    """
    live = set(live_line(versions, current_id))
    return {version.id for version in versions if version.id not in live}


def track_record(versions: Sequence[SetVersionRow]) -> SetTrackRecord:
    """How this Recipe's predictions have gone, counted by state.

    ``graded`` is every state that is a grade, derived from the vocabulary
    rather than from a list written out here: an outcome added to
    `VersionOutcome` and forgotten in this sum would quietly shrink the
    denominator of the only number the Recipe page leads with.
    """
    counts: dict[str, int] = dict.fromkeys(SetTrackRecord.model_fields, 0)
    for version in versions:
        counts[version.outcome_state] += 1
    counts["graded"] = sum(counts[outcome] for outcome in VERSION_OUTCOMES)
    return SetTrackRecord(**counts)


#: The eight measures, read out of what is stored, for every caller that wants
#: them. One constant rather than the same CASE expressions written twice: the
#: spread holds a difference against these numbers and a chat reads the same
#: shots one line each, and two copies of "zero means not recorded" would drift
#: the day one of them is fixed. `sh` is the shots row and `j` the judgement.
_PUCK_FLOW_BIT = next(field.bit for field in FIELD_DEFS if field.name == "pf")

#: Whether the shot has a puck flow: a pressure sensor (a Standard board writes the field as
#: zeros, and its stored diagnostics say so) and the field in the log's mask. The same
#: two-part test as `ShotFacts.puck_flow_recorded`, so a number built on puck flow is absent
#: here exactly where the catalogue leaves it out.
_HAS_PUCK_FLOW = (
    "COALESCE(json_type(sh.diagnostics_json, '$.has_pressure'), 'true') != 'false' "
    f"AND (sh.fields_mask IS NULL OR sh.fields_mask & {1 << _PUCK_FLOW_BIT} != 0)"
)

_MEASURE_COLUMNS = f"""
                   CASE WHEN sh.duration_ms > 0 THEN sh.duration_ms / 1000.0 END AS shot_time_s,
                   CASE WHEN json_type(sh.diagnostics_json,
                                       '$.summary.flow.cup_first_drip_s')
                             IN ('integer', 'real')
                        THEN json_extract(sh.diagnostics_json,
                                          '$.summary.flow.cup_first_drip_s')
                        END AS cup_first_drip_s,
                   CASE WHEN json_type(sh.diagnostics_json,
                                       '$.summary.flow.time_to_first_drip_s')
                             IN ('integer', 'real')
                         AND {_HAS_PUCK_FLOW}
                        THEN json_extract(sh.diagnostics_json,
                                          '$.summary.flow.time_to_first_drip_s')
                        END AS first_drip_s,
                   COALESCE(j.dose_out_g, sh.final_weight_g, sh.index_volume_g) AS yield_g,
                   CASE WHEN json_type(sh.diagnostics_json, '$.summary.pressure.max_bar')
                             IN ('integer', 'real')
                         AND json_extract(sh.diagnostics_json, '$.summary.pressure.max_bar') > 0
                        THEN json_extract(sh.diagnostics_json, '$.summary.pressure.max_bar')
                        END AS peak_pressure_bar,
                   CASE WHEN json_type(sh.diagnostics_json,
                                       '$.diagnostics.extraction.cup_flow_avg_brew_g_s')
                             IN ('integer', 'real')
                         AND json_extract(sh.diagnostics_json,
                                          '$.diagnostics.extraction.cup_flow_avg_brew_g_s') > 0
                        THEN json_extract(sh.diagnostics_json,
                                          '$.diagnostics.extraction.cup_flow_avg_brew_g_s')
                        END AS cup_flow_g_s,
                   CASE WHEN json_type(sh.diagnostics_json,
                                       '$.diagnostics.extraction.flow_avg_brew_ml_s')
                             IN ('integer', 'real')
                         AND json_extract(sh.diagnostics_json,
                                          '$.diagnostics.extraction.flow_avg_brew_ml_s') > 0
                         AND {_HAS_PUCK_FLOW}
                        THEN json_extract(sh.diagnostics_json,
                                          '$.diagnostics.extraction.flow_avg_brew_ml_s')
                        END AS brew_flow_ml_s""".strip()

#: The shot search's numbers that are a column, and the expression each one is.
#: Each reads exactly as the catalogue's item does — a zero duration is no
#: time, the final weight is the scale's — so a shot the SQL keeps is one the
#: catalogue would show inside the range.
SEARCH_COLUMNS: dict[str, str] = {
    "rating": "j.rating",
    "shot_time": "CASE WHEN sh.duration_ms > 0 THEN sh.duration_ms / 1000.0 END",
    "yield": "CASE WHEN sh.final_weight_g > 0 THEN sh.final_weight_g END",
    "dose_in": "j.dose_in_g",
    "dose_out": "j.dose_out_g",
}

#: Every column of `recipe_versions` that a new version copies from its parent —
#: which is exactly the recipe. Named and exported because a proposed change is
#: stored as a patch of these and nothing else, and two lists of "what the
#: recipe is" would disagree the day a sixth field appears.
RECIPE_FIELDS: tuple[str, ...] = (
    "profile_version_id",
    "grind_setting",
    "grind_value",
    "dose_g",
    "target_yield_g",
)

_RECIPE_SELECT = f"""
    SELECT s.*,
           b.name AS bean_name,
           g.name AS grinder_name,
           COALESCE({label_sql("cur")}, '') AS current_version_label,
           {state_sql("cur")},
           cur.profile_version_id AS profile_version_id,
           COALESCE(pb.label, {version_name_sql("pv")}) AS profile_label,
           cfs.id AS copied_from_set_id,
           cfs.name AS copied_from_recipe_name,
           {label_sql("cf")} AS copied_from_version_label,
           (SELECT COUNT(*) FROM recipe_versions v WHERE v.recipe_id = s.id) AS version_count,
           (SELECT COUNT(*) FROM shots sh
             JOIN recipe_versions v2 ON v2.id = sh.recipe_version_id
            WHERE v2.recipe_id = s.id) AS shot_count
    FROM recipes s
    JOIN beans b ON b.id = s.bean_id
    LEFT JOIN grinders g ON g.id = s.grinder_id
    -- The current version is the Set's pointer, not the newest row: a revert
    -- moves the Set back onto an older version without writing a new one.
    LEFT JOIN recipe_versions cur ON cur.id = s.current_version_id
    LEFT JOIN profile_versions pv ON pv.id = cur.profile_version_id
    LEFT JOIN profile_board pb ON pb.id = s.board_id
    LEFT JOIN recipe_versions cf ON cf.id = s.copied_from_version_id
    LEFT JOIN recipes cfs ON cfs.id = cf.recipe_id
"""  # noqa: S608 - the only interpolation is the version label expression, a constant

#: The self-joins resolve a row id into the version *name* a reader sees.
#: Joined rather than looked up by the caller, because the shot page is handed
#: one version and has no list to resolve it against.
_VERSION_SELECT = f"""
    SELECT v.*, {version_name_sql("pv")} AS profile_label,
           -- The brew temperature, out of the profile's own document: the
           -- firmware's 0 means "not set", which is the same rule
           -- `profile_recipe` applies to the same field. Read here rather than
           -- by loading every profile document into the process, because a Set
           -- page shows a dozen versions and only ever wants this one number.
           CASE WHEN json_type(pv.json, '$.temperature') IN ('integer', 'real')
                 AND json_extract(pv.json, '$.temperature') > 0
                THEN json_extract(pv.json, '$.temperature') END AS profile_temperature_c,
           {label_sql("cmp")} AS compares_to_version_label,
           {label_sql("res")} AS restores_version_label,
           {label_sql("par")} AS parent_version_label,
           (cs.current_version_id = v.id) AS is_current,
           (SELECT COUNT(*) FROM shots sh WHERE sh.recipe_version_id = v.id) AS shot_count
    FROM recipe_versions v
    JOIN recipes cs ON cs.id = v.recipe_id
    LEFT JOIN recipe_versions par ON par.id = v.parent_version_id
    LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
    LEFT JOIN recipe_versions cmp ON cmp.id = v.compares_to_version_id
    LEFT JOIN recipe_versions res ON res.id = v.restores_version_id
"""  # noqa: S608 - the only interpolation is the version label expression, a constant


class RecipesRepository(Repository):
    """Reads and writes Recipes, their versions, and which shot belongs to which."""

    # ── recipes ─────────────────────────────────────────────────────────

    async def create(
        self,
        spec: RecipeWrite,
        version: SetVersionWrite,
        *,
        automatch: bool = True,
        from_a_draft: bool = False,
    ) -> SetRow:
        """Create a Recipe and its first version, atomically.

        One transaction because a Recipe with no versions is not a Recipe — every
        query here joins through `recipe_versions`, and a half-created row would be
        invisible in the list and impossible to add a version to without special
        cases.

        ``automatch`` defaults to true: a Recipe is created by somebody who has
        just put that bean in a hopper, and a new Recipe that did not collect the
        shots pulled on its profile would look broken. It takes nothing away
        from any other Recipe — several coffees are loaded at once and the matcher
        tells them apart by profile.
        """
        async with self.db.transaction():
            set_id = await self.create_in_transaction(
                spec, version, automatch=automatch, from_a_draft=from_a_draft
            )
        stored = await self.get(set_id)
        if stored is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("the set vanished between write and read")
        return stored

    async def create_in_transaction(
        self,
        spec: RecipeWrite,
        version: SetVersionWrite,
        *,
        automatch: bool = True,
        copied_from_version_id: int | None = None,
        from_a_draft: bool = False,
    ) -> int:
        """:meth:`create` inside a transaction the caller already holds; returns the Recipe's id.

        The database allows one transaction at a time and refuses to nest, so a write that must
        be one with the Recipe (a duplicate's new profile and insights) opens it itself and calls
        this. ``copied_from_version_id`` is the "copied from" note, written with the row.
        """
        now = utc_now()
        # The Recipe's profile is the profile its first version brews; a profile that has a Recipe
        # already is refused (another bean on the same profile is a Duplicate, which copies it).
        board_id = await self.profile_to_start_on(
            version.profile_version_id, from_a_draft=from_a_draft
        )
        cursor = await self.db.execute(
            """
            INSERT INTO recipes (name, bean_id, grinder_id, archived, automatch, created_at,
                              copied_from_version_id, board_id)
            VALUES (:name, :bean_id, :grinder_id, 0, :automatch, :created_at, :copied_from,
                    :board_id)
            """,
            {
                "name": spec.name,
                "bean_id": spec.bean_id,
                "grinder_id": spec.grinder_id,
                "automatch": int(automatch),
                "created_at": now,
                "copied_from": copied_from_version_id,
                "board_id": board_id,
            },
        )
        set_id = int(cursor.lastrowid or 0)
        values = version.model_dump()
        # Version 1 has no parent, so a prediction on it compares against
        # nothing: it is graded against the numbers the version states.
        values.update(_prediction_values(version.prediction, None, now=now))
        await self._insert_version(set_id, None, values, now, major=True)
        return set_id

    async def profile_to_start_on(
        self, profile_version_id: int | None, *, from_a_draft: bool = False
    ) -> int | None:
        """The profile a Recipe may start on, which is the one thing New Recipe, filling a design
        and a legacy Recipe's first profile all ask: the active version of a profile that has no
        Recipe.

        Returns that profile (``None`` for no version at all). A profile that has a Recipe is
        refused with the way forward; so is any other version. The starting point alone starts a
        Recipe on its draft: a version no profile has yet that no Recipe names, or a version of a
        profile that is not its active one, which a draft's is until it is put.
        """
        board_id = await self._owner_of(profile_version_id)
        await self._refuse_a_pins_copy(profile_version_id)
        if board_id is not None:
            await self._refuse_a_taken_profile(board_id)
            row = await self.db.fetch_one(
                "SELECT current_version_id, deleted_at FROM profile_board WHERE id = ?",
                (board_id,),
            )
            if row is None or row["deleted_at"] is not None:
                raise VersionRefused("profile_deleted")
            if row["current_version_id"] != profile_version_id and not from_a_draft:
                raise VersionRefused("not_the_active_version")
        elif profile_version_id is not None:
            # A version no profile owns: the starting point's own draft, or the version of a
            # machine file the list has not adopted (Writes off never adopts any), in either case
            # one no Recipe names yet. The Recipe has no profile until the list takes the file over.
            if await self.db.fetch_value(
                "SELECT 1 FROM recipe_versions WHERE profile_version_id = ? LIMIT 1",
                (profile_version_id,),
            ) or not (from_a_draft or await self._is_an_unadopted_file(profile_version_id)):
                raise VersionRefused("not_the_active_version")
        return board_id

    async def _is_an_unadopted_file(self, profile_version_id: int) -> bool:
        """The version a machine file holds, as the last sync read it, that no profile stands on."""
        return bool(
            await self.db.fetch_value(
                "SELECT 1 FROM device_profiles d WHERE d.current_version_id = ? "
                "AND d.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM profile_board b "
                "WHERE b.device_profile_id = d.device_id AND b.deleted_at IS NULL) LIMIT 1",
                (profile_version_id,),
            )
        )

    async def profile_choices(self) -> list[tuple[int, str]]:
        """What a Recipe may start on, as ``(profile version id, label)``: every live profile's
        active version and every mirrored machine file's version, each kept only when
        :meth:`profile_to_start_on` accepts it. One rule answers both what this lists and what New
        Recipe takes, so the picker never offers what the create refuses, nor the other way
        round."""
        candidates = await self.db.fetch_all(
            "SELECT current_version_id AS id, label FROM profile_board WHERE deleted_at IS NULL "
            "ORDER BY label COLLATE NOCASE, id"
        )
        files = await self.db.fetch_all(
            "SELECT current_version_id AS id, label FROM device_profiles WHERE deleted_at IS NULL "
            "ORDER BY label COLLATE NOCASE, device_id"
        )
        chosen: list[tuple[int, str]] = []
        seen: set[int] = set()
        for entry in [*candidates, *files]:
            version_id = int(entry["id"])
            if version_id in seen:
                continue
            seen.add(version_id)
            try:
                await self.profile_to_start_on(version_id)
            except VersionRefused:
                continue
            chosen.append((version_id, str(entry["label"])))
        return chosen

    async def taken_profile_choices(self) -> list[tuple[int, str, int, str]]:
        """The profiles New Recipe cannot start on because each has a Recipe already, as ``(active
        version id, profile name, Recipe id, Recipe name)``: every live profile with a Recipe,
        archived Recipes included (a profile keeps its Recipe when the Recipe is archived). Picking
        one in New Recipe opens Duplicate Profile on its active version. A pin's copy is never
        offered, since it has no Recipe of its own and is not to be copied."""
        rows = await self.db.fetch_all(
            "SELECT b.current_version_id AS id, b.label AS label, s.id AS set_id, "
            "s.name AS set_name FROM profile_board b JOIN recipes s ON s.board_id = b.id "
            "WHERE b.deleted_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM recipe_version_pins p "
            "WHERE p.profile_version_id = b.current_version_id) "
            "ORDER BY b.label COLLATE NOCASE, b.id"
        )
        return [(int(r["id"]), str(r["label"]), int(r["set_id"]), str(r["set_name"])) for r in rows]

    async def create_design(self, spec: RecipeWrite, brief: DesignBrief) -> SetRow:
        """Create a Recipe to be designed in its conversation, atomically.

        The Recipe exists from the start, with a version 1 that states nothing —
        no profile, no grind, no dose, no yield, no intent, no prediction —
        because a conversation lives in a Recipe's folder and on one of its
        versions, and this is the conversation where the recipe is worked out.
        The version is filled in place later (see :meth:`append_version`), so
        the Recipe's history starts with the recipe that was brewed rather than
        with an empty v1 and a v2 after it.

        ``automatch`` is on, as for any new Recipe. It is harmless while version 1
        names no profile — the matcher only files a shot under a Recipe whose
        current version names the shot's profile — and once the recipe is
        accepted and its profile pushed, the shots brewed on it land here with
        no further step.
        """
        now = utc_now()
        async with self.db.transaction():
            cursor = await self.db.execute(
                """
                INSERT INTO recipes (name, bean_id, grinder_id, archived, automatch,
                                  designing, design_brief, created_at)
                VALUES (:name, :bean_id, :grinder_id, 0, 1, 1, :design_brief, :created_at)
                """,
                {
                    "name": spec.name,
                    "bean_id": spec.bean_id,
                    "grinder_id": spec.grinder_id,
                    "design_brief": brief.model_dump_json(),
                    "created_at": now,
                },
            )
            set_id = int(cursor.lastrowid or 0)
            await self._insert_version(set_id, None, {"origin": "manual"}, now, major=True)
        stored = await self.get(set_id)
        if stored is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("the set vanished between write and read")
        return stored

    async def design_refusal(
        self, set_id: int
    ) -> Literal["design_has_versions", "design_has_shots"] | None:
        """Why a version written to this Recipe now would be refused, if it would.

        ``None`` for every Recipe that is not being designed, and for one whose
        version 1 is still empty and alone. Public because a push for a Recipe asks
        before it writes to the machine: finding out afterwards would leave a
        pushed profile and a Recipe that refused to record it.
        """
        row = await self.db.fetch_one(
            """
            SELECT s.designing,
                   (SELECT COUNT(*) FROM recipe_versions v WHERE v.recipe_id = s.id) AS versions,
                   (SELECT COUNT(*) FROM shots sh
                      JOIN recipe_versions v2 ON v2.id = sh.recipe_version_id
                     WHERE v2.recipe_id = s.id) AS shots
              FROM recipes s WHERE s.id = ?
            """,
            (set_id,),
        )
        if row is None or not row["designing"]:
            return None
        if int(row["versions"]) != 1:
            return "design_has_versions"
        if int(row["shots"]) > 0:
            return "design_has_shots"
        return None

    async def discard_design(self, set_id: int) -> DesignRefusal | None:
        """Delete a Recipe that is still being designed. ``None`` when it was deleted.

        Allowed only while the Recipe is being designed and nothing is filed on
        it: that is a Recipe nobody has brewed anything under, abandoned before
        its recipe existed. Anything else is refused and left alone — a Recipe
        with history is archived, never deleted.

        What goes with it is what `ON DELETE CASCADE` takes: its version, its
        conversations and their messages, its proposals. The profile drafts its
        proposals carried are not deleted but **discarded**, in the same
        transaction: a draft is a row on the Profiles page with its own history,
        and one proposed for a Recipe that no longer exists is a draft nobody is
        going to approve. A draft already on the machine is left as it is,
        because a row saying `discarded` about a profile the display holds would
        be the archive lying about the machine.
        """
        now = utc_now()
        async with self.db.transaction():
            designing = await self.db.fetch_value(
                "SELECT designing FROM recipes WHERE id = ?", (set_id,)
            )
            if designing is None:
                return "no_recipe"
            if not designing:
                return "not_designing"
            refusal = await self.design_refusal(set_id)
            if refusal is not None:
                return refusal
            drafts = await self.db.fetch_all(
                "SELECT draft_id FROM recipe_version_proposals "
                "WHERE recipe_id = ? AND draft_id IS NOT NULL ORDER BY id",
                (set_id,),
            )
            await ProfileDraftsRepository(self.db).discard_unsent(
                [int(row["draft_id"]) for row in drafts], now=now, why="recipe_discarded"
            )
            await self.db.execute("DELETE FROM recipes WHERE id = ?", (set_id,))
        log.info("recipe_design_discarded", set_id=set_id)
        return None

    async def get(self, set_id: int) -> SetRow | None:
        row = await self.db.fetch_one(f"{_RECIPE_SELECT} WHERE s.id = ?", (set_id,))
        return self.to_model(SetRow, row)

    async def list_sets(self, *, include_archived: bool = False) -> list[SetRow]:
        """Every Recipe, the ones that collect shots first and the newest after."""
        where: list[str] = []
        params: list[Any] = []
        if not include_archived:
            where.append("s.archived = 0")
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = await self.db.fetch_all(
            f"{_RECIPE_SELECT}{clause} ORDER BY s.automatch DESC, s.created_at DESC, s.id DESC",
            params,
        )
        return self.to_models(SetRow, rows)

    async def set_automatch(self, set_id: int, wanted: bool) -> SetRow | None:
        """Offer this Recipe to the matcher, or take it out of the running.

        Any number of Recipes may be offered: several grinders means several
        coffees loaded at once, and the matcher tells them apart by profile
        rather than by which one somebody flagged last.

        An **archived** Recipe cannot be offered. It receives no shots either way
        (`version_naming_profile` filters on `archived`), so the flag on one
        would be a badge that promises something the archive will not do. The
        route turns the ``None`` into a 409.
        """
        row = await self.get(set_id)
        if row is None or (wanted and row.archived):
            return None
        await self.db.execute(
            "UPDATE recipes SET automatch = ? WHERE id = ?", (int(wanted), set_id)
        )
        return await self.get(set_id)

    async def archive(self, set_id: int) -> SetRow | None:
        """Retire a Recipe. Clears `automatch` too — an archived Recipe collects no shots.

        Its pins are unpinned in the same transaction: a copy left on the machine would brew
        shots nobody can file, so the next sync takes each one off (the pinned shots already
        filed stay where they are).
        """
        async with nullcontext() if self.db.in_transaction else self.db.transaction():
            cursor = await self.db.execute(
                "UPDATE recipes SET archived = 1, automatch = 0 WHERE id = ?", (set_id,)
            )
            if cursor.rowcount == 0:
                return None
            await PinsRepository(self.db).unpin_recipe(set_id)
        return await self.get(set_id)

    # ── versions ─────────────────────────────────────────────────────

    async def add_version(
        self,
        set_id: int,
        patch: RecipeVersionPatch,
        *,
        major: bool | None = None,
    ) -> SetVersionRow | None:
        """Append a version: the current one, with the sent fields changed.

        The parent is whatever is current *now*, read inside the transaction
        that writes the child, so two versions added at once cannot both claim
        the same parent or the same name.
        """
        async with self.db.transaction():
            version_id = await self.append_version(set_id, patch, major=major)
        return None if version_id is None else await self.get_version(version_id)

    async def append_version(
        self,
        set_id: int,
        patch: RecipeVersionPatch,
        *,
        keep_proposal: int | None = None,
        major: bool | None = None,
    ) -> int | None:
        """The append itself, inside a transaction the caller already holds.

        Separate from :meth:`add_version` because accepting a proposed change
        has to do this **and** four other reads and writes in one transaction —
        the proposal is still waiting, the Recipe has not moved on since, the
        current prediction has been graded — and this application shares one
        connection whose ``transaction()`` refuses to nest. A caller that opens
        no transaction of its own wants :meth:`add_version`.

        ``keep_proposal`` is the proposal being accepted, which is the one
        waiting proposal this append does **not** make stale. See
        :meth:`_insert_version`.

        **A Recipe being designed is filled, not appended to.** Its version 1
        states nothing yet, and whatever writes the first recipe — the Add a
        version form, a draft pushed for this Recipe, an accepted initial recipe —
        writes it onto that version 1 instead of putting a v2 after an empty
        one (:meth:`_fill_design`). Decided here, where every one of those
        paths already arrives, so none of them can miss it. When the design
        cannot be filled any more (a shot was filed on it by hand) the write
        raises :class:`VersionRefused` and the caller's transaction rolls back.

        **Its name** is the current version's major and the next minor, or the
        next major: ``major`` is the person's answer when they gave one, and a minor
        when nobody said (a major is a functional change to what the profile does,
        which only a person or an agent's suggestion the person accepts says). A
        design filled in place keeps its v1.

        Returns the new version's id — version 1's own when it was filled — or
        ``None`` when the Recipe has no current version to build on.
        """
        sent = patch.model_dump(exclude_unset=True)
        now = utc_now()
        parent = await self._current_version_row(set_id)
        if parent is None:
            return None
        designing = await self.db.fetch_value(
            "SELECT designing FROM recipes WHERE id = ?", (set_id,)
        )
        if designing:
            refusal = await self.design_refusal(set_id)
            if refusal is not None:
                raise VersionRefused(refusal)
            return await self._fill_design(parent, patch, sent, now, keep_proposal=keep_proposal)
        values = {field: getattr(parent, field) for field in RECIPE_FIELDS}
        for field in RECIPE_FIELDS:
            if field in sent:
                values[field] = sent[field]
        await self._give_a_legacy_recipe_its_profile(set_id, sent.get("profile_version_id"))
        values["intent"] = patch.intent
        values["origin"] = patch.origin
        values["origin_analysis_id"] = patch.origin_analysis_id
        # Omitted is "against the version I changed from"; an explicit null
        # is "against nothing". `sent` is what tells them apart.
        compares_to = (
            sent["compares_to_version_id"] if "compares_to_version_id" in sent else parent.id
        )
        values.update(_prediction_values(patch.prediction, compares_to, now=now))
        return await self._insert_version(
            set_id,
            parent.id,
            values,
            now,
            major=bool(major),
            keep_proposal=keep_proposal,
        )

    async def _give_a_legacy_recipe_its_profile(
        self, set_id: int, profile_version_id: int | None
    ) -> None:
        """A Recipe with no profile that is given one at its next version: under New Recipe's rule.
        Each version no profile owns that its versions name goes through the profile's own
        ``version_for`` like everywhere else (the profile takes it over, or keeps a copy of its
        own), so the Recipe's versions are all that profile's afterwards. A
        version no profile owns gives it none. A Recipe that has a profile is not touched: its
        version follows the profile, and a version naming another is refused by the write."""
        board = await self._owner_of(profile_version_id)
        if board is None:
            return
        if (
            await self.db.fetch_value("SELECT board_id FROM recipes WHERE id = ?", (set_id,))
            is not None
        ):
            return
        await self.profile_to_start_on(profile_version_id)
        # Imported here: the profile list's repository reads this one's neighbours.
        from gaggiclanker.db.repos.profile_board import ProfileBoardRepository

        # The person named the profile, so it is the Recipe's whatever its current version is, and
        # every version no profile owns that the Recipe names is settled for it: taken over, or the
        # profile gets a copy of its own that the Recipe's versions then name. The new version,
        # which names the profile's own, is written by the caller; nothing is followed from here.
        await self.db.execute("UPDATE recipes SET board_id = ? WHERE id = ?", (board, set_id))
        await ProfileBoardRepository(self.db).settle_a_recipes_versions(board, set_id, follow=False)

    async def _insert_version(
        self,
        set_id: int,
        parent_version_id: int | None,
        values: dict[str, Any],
        now: str,
        *,
        major: bool,
        keep_proposal: int | None = None,
    ) -> int:
        """Write one version row, and retire whatever it has overtaken.

        Every path that appends a version to a Recipe comes through here — the Add
        a version form, a pushed profile draft and accepting a proposal (a
        revert writes no version and retires the waiting proposal itself) —
        which is why the retirement lives here rather than in each of them. A change an agent
        proposed was argued against the version that was current when it was
        made; the moment the Recipe moves on, that argument is about a recipe
        nobody is brewing, and leaving the row saying "waiting" would tell the
        next conversation something false and leave the person a button that can
        only answer 409.

        So it becomes `stale`, in the same transaction as the version that
        overtook it, and the agent is free to propose afresh.
        ``keep_proposal`` is the exception and the only one: accepting a
        proposal appends a version too, and that proposal is not overtaken by
        the version it created.

        **The name is numbered here too, and the Recipe's pointer moved to the new
        version**, from the Recipe's rows
        read in this same transaction, so no path numbers a version itself and
        two writers cannot mint one name (the unique index on it is the
        backstop). ``major`` is already decided by the caller's rule.
        """
        await self._refuse_a_pins_copy(values.get("profile_version_id"))
        await self._refuse_another_profile(set_id, values.get("profile_version_id"))
        numbers = await next_numbers(self.db, set_id, major=major)
        payload: dict[str, Any] = {
            "set_id": set_id,
            "version_major": numbers.major,
            "version_minor": numbers.minor,
            "parent_version_id": parent_version_id,
            "intent": values.get("intent", ""),
            "origin": values.get("origin", "manual"),
            "origin_analysis_id": values.get("origin_analysis_id"),
            "prediction": values.get("prediction", ""),
            "compares_to_version_id": values.get("compares_to_version_id"),
            "prediction_at": values.get("prediction_at"),
            "created_at": now,
        }
        for field in RECIPE_FIELDS:
            payload[field] = values.get(field)
        columns = ", ".join("recipe_id" if name == "set_id" else name for name in payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        cursor = await self.db.execute(
            f"INSERT INTO recipe_versions ({columns}) VALUES ({placeholders})",  # noqa: S608 - keys are the literal payload above
            payload,
        )
        version_id = int(cursor.lastrowid or 0)
        await self.db.execute(
            "UPDATE recipes SET current_version_id = ? WHERE id = ?", (version_id, set_id)
        )
        await self._retire_waiting(set_id, now, keep_proposal=keep_proposal)
        log.info(
            "recipe_version_added",
            set_id=set_id,
            set_version_id=version_id,
            version_label=numbers.label,
            major=major,
        )
        return version_id

    async def _owner_of(self, profile_version_id: int | None) -> int | None:
        """The profile a version belongs to; ``None`` for no version or one no profile has."""
        if profile_version_id is None:
            return None
        owner = await self.db.fetch_value(
            "SELECT board_id FROM profile_versions WHERE id = ?", (profile_version_id,)
        )
        return None if owner is None else int(owner)

    async def recipe_of_profile(self, board_id: int) -> tuple[int, str] | None:
        """The Recipe this profile belongs to, as ``(id, name)``; ``None`` for a profile with
        none."""
        row = await self.db.fetch_one(
            "SELECT id, name FROM recipes WHERE board_id = ?", (board_id,)
        )
        return None if row is None else (int(row["id"]), str(row["name"]))

    async def taken_sentence(self, board_id: int) -> str | None:
        """Why a Recipe cannot take this profile, if it has one already: whose it is, and what to do
        instead. ``None`` for a profile with no Recipe."""
        taken = await self.recipe_of_profile(board_id)
        if taken is None:
            return None
        label = await self.db.fetch_value(
            "SELECT label FROM profile_board WHERE id = ?", (board_id,)
        )
        return f"{label} belongs to {taken[1]}. Duplicate {taken[1]} to start a new one on a copy."

    async def _refuse_a_taken_profile(self, board_id: int) -> None:
        """A profile has at most one Recipe."""
        sentence = await self.taken_sentence(board_id)
        if sentence is not None:
            raise VersionRefused("profile_has_a_set", sentence)

    async def _refuse_another_profile(self, set_id: int, profile_version_id: int | None) -> None:
        """Every version of a Recipe brews a version its profile owns (nothing else keeps the rule,
        since no constraint spans the two tables): checked at the one place a version is written.
        A Recipe with no profile may name only a version that no profile owns."""
        if profile_version_id is None:
            return
        row = await self.db.fetch_one(
            "SELECT pv.board_id AS owner, s.board_id AS mine "
            "FROM profile_versions pv, recipes s WHERE pv.id = ? AND s.id = ?",
            (profile_version_id, set_id),
        )
        if row is not None and row["owner"] != row["mine"]:
            raise VersionRefused("other_profile")

    async def _refuse_a_pins_copy(self, profile_version_id: int | None) -> None:
        """No Recipe version names a pin's copy: the copy belongs to the pinned version for ever,
        and a second version brewing it would put two Recipes' shots on one frozen profile. Raised
        from inside the caller's transaction, which it undoes."""
        if profile_version_id is not None and await PinsRepository(self.db).is_copy(
            profile_version_id
        ):
            raise VersionRefused("profile_is_a_pin")

    async def _fill_design(
        self,
        current: SetVersionRow,
        patch: RecipeVersionPatch,
        sent: dict[str, Any],
        now: str,
        *,
        keep_proposal: int | None = None,
    ) -> int:
        """Write the first recipe onto a designed Recipe's empty version 1.

        The one place a version row's recipe is updated rather than appended,
        and it is not an exception to "the recipe is immutable": nothing was
        ever brewed on this version, which :meth:`design_refusal` checked, so
        there is no shot whose recipe this rewrites.

        The recipe, the intent and the origin come from the write, exactly as
        an appended version would take them. The **prediction does not**: a
        version 1 is a baseline, not a change to anything, so there is nothing
        for a prediction to be about — the same reason `create` gives version 1
        no comparison. The flag is cleared in the same transaction, and
        whatever proposal was waiting is retired as any append retires it.
        """
        values = {field: getattr(current, field) for field in RECIPE_FIELDS}
        for field in RECIPE_FIELDS:
            if field in sent:
                values[field] = sent[field]
        await self._refuse_a_pins_copy(values["profile_version_id"])
        # The first recipe gives the Recipe its profile. An accepted card has put its draft first,
        # so it may be a version the profile has moved on from; the person's own choice in the form
        # is held to New Recipe's rule before it arrives (:meth:`profile_to_start_on`).
        owner = await self.profile_to_start_on(values["profile_version_id"], from_a_draft=True)
        if owner is not None:
            await self.db.execute(
                "UPDATE recipes SET board_id = ? WHERE id = ?", (owner, current.set_id)
            )
        await self._refuse_another_profile(current.set_id, values["profile_version_id"])
        await self.db.execute(
            """
            UPDATE recipe_versions
               SET profile_version_id = :profile_version_id,
                   grind_setting = :grind_setting,
                   grind_value = :grind_value,
                   dose_g = :dose_g,
                   target_yield_g = :target_yield_g,
                   intent = :intent,
                   origin = :origin,
                   origin_analysis_id = :origin_analysis_id,
                   prediction = '',
                   compares_to_version_id = NULL,
                   prediction_at = NULL
             WHERE id = :id
            """,
            {
                **values,
                "intent": patch.intent,
                "origin": patch.origin,
                "origin_analysis_id": patch.origin_analysis_id,
                "id": current.id,
            },
        )
        await self.db.execute("UPDATE recipes SET designing = 0 WHERE id = ?", (current.set_id,))
        await self._retire_waiting(current.set_id, now, keep_proposal=keep_proposal)
        log.info("recipe_design_filled", set_id=current.set_id, set_version_id=current.id)
        if owner is not None:
            # The card may name a version its profile has moved on from (a later change was put
            # before the card was accepted): the Recipe follows to what the profile has active.
            active = await self.db.fetch_value(
                "SELECT current_version_id FROM profile_board WHERE id = ?", (owner,)
            )
            if active is not None and active != values["profile_version_id"]:
                await self.follow_profile(owner, int(active), keep_proposal=keep_proposal)
        return current.id

    async def _retire_waiting(
        self, set_id: int, now: str, *, keep_proposal: int | None = None
    ) -> None:
        """Stale whatever proposal was waiting on this Recipe, and discard its draft.

        The draft goes with the proposal because the proposal was the only
        reason for it: an initial recipe's profile draft belongs to no Recipe on
        the draft side, and once the card that carried it is retired nobody is
        going to approve it — left open, the Profiles page would collect one
        orphan per revision of the design. A draft already sent to the machine
        is not touched (:meth:`ProfileDraftsRepository.discard_unsent`).
        """
        params = {"set_id": set_id, "now": now, "keep": keep_proposal}
        drafts = await self.db.fetch_all(
            """
            SELECT draft_id FROM recipe_version_proposals
             WHERE recipe_id = :set_id
               AND status = 'proposed'
               AND draft_id IS NOT NULL
               AND (:keep IS NULL OR id != :keep)
             ORDER BY id
            """,
            params,
        )
        await ProfileDraftsRepository(self.db).discard_unsent(
            [int(row["draft_id"]) for row in drafts], now=now, why="recipe_written_otherwise"
        )
        await self.db.execute(
            """
            UPDATE recipe_version_proposals
               SET status = 'stale', decided_at = :now
             WHERE recipe_id = :set_id
               AND status = 'proposed'
               AND (:keep IS NULL OR id != :keep)
            """,
            params,
        )

    # ── the prediction, the outcome and the roll back ─────────────────
    #
    # Three guarded writes. Each reads the version inside the transaction it
    # writes in, because every guard here is about state that another request
    # can change: a shot lands, a judgement is typed, a version is added.

    async def set_prediction(
        self, set_id: int, version_id: int, spec: VersionPredictionWrite
    ) -> VersionWriteResult:
        """Write what this version is expected to do differently.

        Refused once the version has a shot, and again once its prediction has
        been graded. A prediction typed after the cup was tasted grades itself,
        and an archive that let one in would report a track record nobody could
        trust — which is the only number on the Recipe page worth reading.

        Not airtight, and deliberately so: unfiling every shot and clearing the
        grade opens the window again. The rule is against the habit, not against
        somebody determined to rewrite their own record.
        """
        now = utc_now()
        async with self.db.transaction():
            version = await self.version_of_recipe(set_id, version_id)
            if version is None:
                return VersionWriteResult(refused="no_version")
            if version.shot_count > 0:
                return VersionWriteResult(refused="has_shots")
            # A grade already given is a statement about the prediction as it
            # was written. Rewriting the prediction underneath it would leave
            # the grade attached to something nobody ever predicted, so the
            # grade comes off first, deliberately.
            if version.outcome is not None:
                return VersionWriteResult(refused="has_outcome")
            compares_to = (
                spec.compares_to_version_id
                if "compares_to_version_id" in spec.model_fields_set
                else version.parent_version_id
            )
            if spec.prediction and compares_to is not None:
                if await self.comparison_target(set_id, version.id, compares_to) is None:
                    return VersionWriteResult(refused="bad_compare")
            values = _prediction_values(spec.prediction, compares_to, now=now)
            await self.db.execute(
                """
                UPDATE recipe_versions
                   SET prediction = :prediction,
                       compares_to_version_id = :compares_to_version_id,
                       prediction_at = :prediction_at
                 WHERE id = :id
                """,
                {**values, "id": version_id},
            )
        return VersionWriteResult(version=await self.get_version(version_id))

    async def set_outcome(
        self, set_id: int, version_id: int, spec: VersionOutcomeWrite
    ) -> VersionWriteResult:
        """Grade this version's prediction. Changeable, and never automatic.

        Two things have to exist before there is a grade to give: a prediction,
        and a shot somebody has actually formed a view about. "Judged,
        non-discarded" means a decision of keep or improve — a discarded shot
        says the shot went wrong, not that the recipe did, and grading a
        prediction on one would be reading the wrong signal.
        """
        async with self.db.transaction():
            return await self.grade_in_transaction(set_id, version_id, spec)

    async def grade_in_transaction(
        self, set_id: int, version_id: int, spec: VersionOutcomeWrite
    ) -> VersionWriteResult:
        """The grade itself, inside a transaction the caller already holds.

        Separate from :meth:`set_outcome` for the reason :meth:`append_version`
        is separate from :meth:`add_version`: accepting an agent's proposed
        grade marks the proposal and writes the grade in one transaction, and
        this connection's ``transaction()`` refuses to nest. Both callers go
        through the same guards, so a grade a person typed on the Recipe page and
        one they accepted in the chat are held to one rule.
        """
        now = utc_now()
        version = await self.version_of_recipe(set_id, version_id)
        if version is None:
            return VersionWriteResult(refused="no_version")
        if not version.prediction:
            return VersionWriteResult(refused="no_prediction")
        if not await self._has_gradable_shot(version_id):
            return VersionWriteResult(refused="nothing_to_grade")
        await self.db.execute(
            """
            UPDATE recipe_versions
               SET outcome = :outcome, outcome_note = :note, outcome_at = :now
             WHERE id = :id
            """,
            {"outcome": spec.outcome, "note": spec.note, "now": now, "id": version_id},
        )
        return VersionWriteResult(version=await self.get_version(version_id))

    async def clear_outcome(self, set_id: int, version_id: int) -> VersionWriteResult:
        """Take a grade back. Always allowed: second thoughts are ordinary."""
        async with self.db.transaction():
            version = await self.version_of_recipe(set_id, version_id)
            if version is None:
                return VersionWriteResult(refused="no_version")
            await self.db.execute(
                "UPDATE recipe_versions SET outcome = NULL, outcome_note = '', outcome_at = NULL "
                "WHERE id = ?",
                (version_id,),
            )
        return VersionWriteResult(version=await self.get_version(version_id))

    async def rollback(self, set_id: int, spec: RollbackWrite) -> VersionWriteResult:
        """Put the Recipe back on another version of it. Writes no version.

        The target becomes the Recipe's current version again, exactly as it was:
        its prediction, shots and outcome stay, new shots file under it, and a
        recorded outcome is not reopened. What is written is the Recipe's pointer
        and one row in the revert log (from, to, the note, when), in one
        transaction with the waiting proposal being made stale: a change was
        argued against the version the Recipe was on, and the Recipe has moved.

        The next minor continues the target's major line (v2.1 → back to v1.2
        → v1.3, or the next free minor of major 1), because a minor is named
        from the current version; nothing here renames anything. Ancestry is
        the fork history (`parent_version_id`) and a version made after a
        revert has the target as its parent.

        ``version`` in the result is the version the Recipe is now on.
        """
        now = utc_now()
        async with nullcontext() if self.db.in_transaction else self.db.transaction():
            if await self.db.fetch_value("SELECT designing FROM recipes WHERE id = ?", (set_id,)):
                return VersionWriteResult(refused="designing")
            current = await self._current_version_row(set_id)
            if current is None:
                return VersionWriteResult(refused="no_version")
            target = await self.version_of_recipe(set_id, spec.to_version_id)
            if target is None:
                return VersionWriteResult(refused="no_target")
            if target.id == current.id:
                return VersionWriteResult(refused="current_version")
            await self.db.execute(
                "UPDATE recipes SET current_version_id = ? WHERE id = ?", (target.id, set_id)
            )
            await self.db.execute(
                "INSERT INTO recipe_version_reverts "
                "(recipe_id, from_version_id, to_version_id, note, created_at) VALUES (?, ?, ?, "
                "?, ?)",
                (set_id, current.id, target.id, spec.note.strip(), now),
            )
            await self._retire_waiting(set_id, now)
        log.info(
            "recipe_reverted",
            set_id=set_id,
            from_label=current.version_label,
            to_label=target.version_label,
        )
        return VersionWriteResult(version=await self.get_version(target.id))

    async def reverts(self, set_id: int) -> list[SetRevertRow]:
        """Every time this Recipe went back, newest first, each version by its name."""
        rows = await self.db.fetch_all(
            f"""
            SELECT r.id, r.recipe_id, r.from_version_id,
                   {label_sql("f")} AS from_version_label,
                   r.to_version_id, {label_sql("t")} AS to_version_label,
                   r.note, r.created_at
              FROM recipe_version_reverts r
              JOIN recipe_versions f ON f.id = r.from_version_id
              JOIN recipe_versions t ON t.id = r.to_version_id
             WHERE r.recipe_id = ?
             ORDER BY r.created_at DESC, r.id DESC
            """,  # noqa: S608 - the interpolation is the label expression, the id is bound
            (set_id,),
        )
        return self.to_models(SetRevertRow, rows)

    async def reverts_into(self, version_id: int) -> list[SetRevertRow]:
        """Every revert that put the Recipe back on this version, oldest first."""
        rows = await self.db.fetch_all(
            f"""
            SELECT r.id, r.recipe_id, r.from_version_id,
                   {label_sql("f")} AS from_version_label,
                   r.to_version_id, {label_sql("t")} AS to_version_label,
                   r.note, r.created_at
              FROM recipe_version_reverts r
              JOIN recipe_versions f ON f.id = r.from_version_id
              JOIN recipe_versions t ON t.id = r.to_version_id
             WHERE r.to_version_id = ?
             ORDER BY r.created_at, r.id
            """,  # noqa: S608 - the interpolation is the label expression, the id is bound
            (version_id,),
        )
        return self.to_models(SetRevertRow, rows)

    async def comparison_target(
        self, set_id: int, version_id: int, compares_to: int
    ) -> SetVersionRow | None:
        """The version a prediction on `version_id` may name, or ``None``.

        Two conditions, in one place because both routes that accept a
        comparison have to agree on them:

        * **the same Recipe.** Two Recipes are two coffees, and "less bitter than v2"
          across them compares nothing anybody brewed.
        * **older.** A prediction reads "compared to vN", and a vN that was made
          after this version is not something it could have been expected to
          improve on. Older is `created_at` (the row id only breaks a tie), never
          the id or a name: the names are identifiers, not a sequence. This also
          rules out a version against itself.

        `POST /versions` calls :meth:`version_of_recipe` instead, because there the
        version does not exist yet and every candidate is older by construction.
        """
        target = await self.version_of_recipe(set_id, compares_to)
        version = await self.version_of_recipe(set_id, version_id)
        if target is None or version is None:
            return None
        if (target.created_at, target.id) >= (version.created_at, version.id):
            return None
        return target

    async def version_of_recipe(self, set_id: int, version_id: int) -> SetVersionRow | None:
        """A version, but only if it belongs to the Recipe the route named.

        Public because the route that accepts a compared-to version on a *new*
        version needs the same check, and two copies of "is this id one of this
        Recipe's" is how they come to disagree.
        """
        row = await self.db.fetch_one(
            f"{_VERSION_SELECT} WHERE v.id = ? AND v.recipe_id = ?", (version_id, set_id)
        )
        return self.to_model(SetVersionRow, row)

    async def version_named(self, set_id: int, major: int, minor: int) -> SetVersionRow | None:
        """This Recipe's version called v<major>.<minor>, if it has one.

        For a name somebody typed — the agent's search takes "v1.1" — which is
        only ever a name within one Recipe, never an ordinal and never another
        Recipe's version.
        """
        row = await self.db.fetch_one(
            f"{_VERSION_SELECT} WHERE v.recipe_id = ? AND v.version_major = ? AND v.version_minor "
            f"= ?",
            (set_id, major, minor),
        )
        return self.to_model(SetVersionRow, row)

    async def _has_gradable_shot(self, version_id: int) -> bool:
        return await self.gradable_shot_count(version_id) > 0

    async def gradable_shot_count(self, version_id: int) -> int:
        """How many shots of this version a person judged Keep or Improve.

        The shots a grade rests on: a discarded shot says the shot went wrong,
        not that the recipe did. One definition, read by the outcome guard and by
        the agent's proposed grade, which records how many it was written on.
        """
        return int(
            await self.db.fetch_value(
                """
                SELECT COUNT(*) FROM shots s
                JOIN shot_judgements j ON j.shot_id = s.id
                WHERE s.recipe_version_id = ? AND j.decision IN ('keep', 'improve')
                """,
                (version_id,),
            )
            or 0
        )

    async def label_counts(self, set_id: int) -> dict[int, VersionLabelCounts]:
        """How every version's shots were labelled, in one grouped pass."""
        rows = await self.db.fetch_all(
            """
            SELECT sh.recipe_version_id AS version_id,
                   SUM(j.decision = 'keep') AS keep,
                   SUM(j.decision = 'improve') AS improve,
                   SUM(j.decision = 'discard') AS discard,
                   SUM(j.decision IS NULL) AS unlabelled
            FROM shots sh
            JOIN recipe_versions v ON v.id = sh.recipe_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE v.recipe_id = ?
            GROUP BY sh.recipe_version_id
            """,
            (set_id,),
        )
        return {
            int(row["version_id"]): VersionLabelCounts(
                keep=int(row["keep"] or 0),
                improve=int(row["improve"] or 0),
                discard=int(row["discard"] or 0),
                unlabelled=int(row["unlabelled"] or 0),
            )
            for row in rows
        }

    async def rollback_target(self, set_id: int) -> int | None:
        """The nearest version behind the current one that is worth going back to.

        The nearest **ancestor** of the current version on the fork history (the
        line walked back as :func:`live_line` walks it, so an old roll back row
        steps to what it restored) that has a Keep shot. Not the newest one made:
        after going back, that can sit on a branch the Recipe left, and an offer to
        "go back" must not point forward. Never the current version, and never one
        whose only shots were discarded or never labelled, because "it worked" is
        a thing somebody said, not a thing a shot count implies.
        """
        current = await self.db.fetch_value(
            "SELECT current_version_id FROM recipes WHERE id = ?", (set_id,)
        )
        rows = await self.db.fetch_all(
            "SELECT id, recipe_id, parent_version_id, restores_version_id "
            "FROM recipe_versions WHERE recipe_id = ?",
            (set_id,),
        )
        kept = {
            int(row["id"])
            for row in await self.db.fetch_all(
                """
                SELECT DISTINCT sh.recipe_version_id AS id FROM shots sh
                  JOIN shot_judgements j ON j.shot_id = sh.id
                  JOIN recipe_versions v ON v.id = sh.recipe_version_id
                 WHERE v.recipe_id = ? AND j.decision = 'keep'
                """,
                (set_id,),
            )
        }
        line = live_line(self.to_models(VersionLink, rows), current)
        return next((version_id for version_id in line[1:] if version_id in kept), None)

    async def live_recipes_by_profile(self) -> dict[int, LiveRecipe]:
        """The Recipe each profile has, by profile id: archived Recipes left out (a warning about a
        profile a Recipe no longer uses would only be noise), with the version the Recipe brews now,
        the current version's name and what its next minor version would be called."""
        rows = await self.db.fetch_all(
            f"""
            SELECT s.board_id AS board_id, s.id AS id, s.name AS name,
                   v.profile_version_id AS pv, {label_sql("v")} AS version_label
              FROM recipes s
              JOIN recipe_versions v ON v.id = s.current_version_id
             WHERE s.archived = 0 AND s.board_id IS NOT NULL
            """  # noqa: S608 - the only interpolation is the version label expression, a constant
        )
        found: dict[int, LiveRecipe] = {}
        for r in rows:
            names = await next_names(self.db, int(r["id"]))
            found[int(r["board_id"])] = LiveRecipe(
                set_id=int(r["id"]),
                name=str(r["name"]),
                brewing_version_id=r["pv"],
                version_label=str(r["version_label"]),
                next_minor_label=names.minor,
            )
        return found

    async def follow_profile(
        self,
        board_id: int,
        profile_version_id: int,
        *,
        major: bool | None = None,
        origin: SetVersionOrigin = "manual",
        origin_analysis_id: int | None = None,
        intent: str | None = None,
        prediction: str = "",
        compares_to_version_id: int | None = None,
        keep_proposal: int | None = None,
    ) -> SetVersionRow | None:
        """A profile has a new active version: its Recipe gets a new version naming it.

        The one place a Recipe follows its profile, called in the same transaction as every change
        of a profile's active version (a put, Make active, keeping the machine's side of a
        conflict). Grind, dose and yield come from the Recipe's current version, and so does the
        intent unless one is given; the version is a minor unless ``major`` says otherwise. A
        prediction and the version it is compared to travel together (a draft argued in this
        Recipe's chat). ``None`` when the profile has no Recipe, or the Recipe's current version
        names the version already.
        """
        found = await self.recipe_of_profile(board_id)
        if found is None:
            return None
        parent = await self._current_version_row(found[0])
        if parent is None or parent.profile_version_id == profile_version_id:
            return None
        fields: dict[str, Any] = {
            "profile_version_id": profile_version_id,
            "origin": origin,
            "origin_analysis_id": origin_analysis_id,
            "intent": parent.intent if intent is None else intent,
            "prediction": prediction,
        }
        if prediction:
            fields["compares_to_version_id"] = compares_to_version_id
        version_id = await self.append_version(
            found[0], RecipeVersionPatch(**fields), major=major, keep_proposal=keep_proposal
        )
        return None if version_id is None else await self.get_version(version_id)

    async def get_version(self, version_id: int) -> SetVersionRow | None:
        row = await self.db.fetch_one(f"{_VERSION_SELECT} WHERE v.id = ?", (version_id,))
        return self.to_model(SetVersionRow, row)

    async def versions_by_id(self, version_ids: Iterable[int]) -> dict[int, SetVersionRow]:
        """Several versions, whichever Recipes they belong to, in one query."""
        wanted = sorted({int(version_id) for version_id in version_ids})
        if not wanted:
            return {}
        placeholders = ", ".join("?" * len(wanted))
        rows = await self.db.fetch_all(
            f"{_VERSION_SELECT} WHERE v.id IN ({placeholders})",
            wanted,
        )
        return {row.id: row for row in self.to_models(SetVersionRow, rows)}

    async def dead_end_versions(self, recipe_ids: Iterable[int]) -> set[int]:
        """Which of these Recipes' versions are off the line still being brewed.

        One query for however many Recipes were asked about, then the same walk the
        Recipe page uses, per Recipe. The Chat page's folders need this for a list of
        conversations spread over every Recipe there is, and a query per Recipe would
        make drawing the list cost as much as opening one.
        """
        wanted = sorted({int(set_id) for set_id in recipe_ids})
        if not wanted:
            return set()
        placeholders = ", ".join("?" * len(wanted))
        rows = await self.db.fetch_all(
            "SELECT id, recipe_id, parent_version_id, restores_version_id "  # noqa: S608 - placeholders are generated, the ids are bound
            f"FROM recipe_versions WHERE recipe_id IN ({placeholders})",
            wanted,
        )
        pointers = {
            int(row["id"]): row["current_version_id"]
            for row in await self.db.fetch_all(
                f"SELECT id, current_version_id FROM recipes WHERE id IN ({placeholders})",  # noqa: S608 - placeholders are generated, the ids are bound
                wanted,
            )
        }
        links = self.to_models(VersionLink, rows)
        dead: set[int] = set()
        for set_id in wanted:
            dead |= dead_end_ids(
                [link for link in links if link.set_id == set_id], pointers.get(set_id)
            )
        return dead

    async def versions(self, set_id: int) -> list[SetVersionRow]:
        """Every version of a Recipe, newest made first — the order the timeline reads in."""
        rows = await self.db.fetch_all(
            f"{_VERSION_SELECT} WHERE v.recipe_id = ? ORDER BY v.created_at DESC, v.id DESC",
            (set_id,),
        )
        return self.to_models(SetVersionRow, rows)

    async def _current_version_row(self, set_id: int) -> SetVersionRow | None:
        row = await self.db.fetch_one(
            f"{_VERSION_SELECT} WHERE v.id = cs.current_version_id AND v.recipe_id = ?", (set_id,)
        )
        return self.to_model(SetVersionRow, row)

    async def current_version(self, set_id: int) -> SetVersionRow | None:
        """The version a shot pulled right now would be attached to."""
        return await self._current_version_row(set_id)

    # ── assignment ───────────────────────────────────────────────────

    async def assign_shot(self, shot_id: int, version_id: int | None) -> bool:
        """Attach a shot to a Recipe version, or detach it. A person's decision.

        Unlike :meth:`profile_match` this overwrites whatever was there: it is
        the correction path, and the whole point of it is to fix a wrong guess.
        Returns False when the shot or the version does not exist — `shots`
        carries no foreign key on this column (`db/schema.sql` says why), so this method is
        where the reference is checked.
        """
        if version_id is not None:
            # Not merely "does the version exist": a version of an *archived*
            # Recipe is refused too. Archiving is how a Recipe stops collecting shots,
            # and a hand assignment that could still put one there would be the
            # one path around it.
            exists = await self.db.fetch_value(
                """
                SELECT 1 FROM recipe_versions v
                JOIN recipes s ON s.id = v.recipe_id
                WHERE v.id = ? AND s.archived = 0
                """,
                (version_id,),
            )
            if exists is None:
                return False
        cursor = await self.db.execute(
            "UPDATE shots SET recipe_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, utc_now(), shot_id),
        )
        return cursor.rowcount > 0

    async def _resolved_profile_version(
        self, profile_version_id: int | None, device_profile_id: str
    ) -> int | None:
        """The profile version a shot was brewed with, as far as the archive knows.

        Its own link when it has one; otherwise what its device profile id maps
        to right now, ignoring a tombstoned mapping (the id was freed on the
        machine and may already name a different profile).
        """
        if profile_version_id is not None:
            return profile_version_id
        if not device_profile_id:
            return None
        mapped = await self.db.fetch_value(
            """
            SELECT current_version_id FROM device_profiles
            WHERE device_id = ? AND deleted_at IS NULL
            """,
            (device_profile_id,),
        )
        return None if mapped is None else int(mapped)

    async def _fill(self, shot_id: int, version_id: int) -> bool:
        """File a shot that has no Recipe yet. Never moves one that has."""
        cursor = await self.db.execute(
            """
            UPDATE shots SET recipe_version_id = ?, updated_at = ?
            WHERE id = ? AND recipe_version_id IS NULL
            """,
            (version_id, utc_now(), shot_id),
        )
        return cursor.rowcount > 0

    async def pinned_version(self, profile_version_id: int) -> int | None:
        """The Recipe version a pin's copy belongs to, when its Recipe is open to automatic filing.

        The Recipe's own "file matching shots here" governs a pin as it governs every other way a
        shot is filed by its profile: with it off the Recipe's shots are filed by hand, and the
        Match by profile button files the waiting ones once it is back on. An archived Recipe
        receives none. The pin's own record may be unpinned already: the copy still names the
        version (the file may be on the machine until the next sync removes it).
        """
        found = await self.db.fetch_value(
            """
            SELECT p.recipe_version_id FROM recipe_version_pins p
            JOIN recipe_versions v ON v.id = p.recipe_version_id
            JOIN recipes s ON s.id = v.recipe_id
            WHERE p.profile_version_id = ? AND s.automatch = 1 AND s.archived = 0
            """,
            (profile_version_id,),
        )
        return None if found is None else int(found)

    async def version_naming_profile(self, profile_version_id: int) -> int | None:
        """The current version of the Recipe offered to the matcher that brews this profile version.

        Three filters, and each one is a rule stated elsewhere:

        * **`automatch`** is the Recipe's own answer to "file shots here". It is
          the only thing the flag means, and a Recipe with it off is not a
          candidate.
        * **`archived = 0`**, because a finished Recipe receives no shots.
        * only a Recipe's **current** version counts: an older one that named the
          profile describes what the Recipe used to be, and filing today's shot
          there would be filing it under a recipe the Recipe has moved on from.

        A Recipe that names no profile is not configured for this one, so it is
        not here either — it collects nothing on its own and waits to be picked
        by hand. A profile has at most one Recipe, so there is at most one answer.
        """
        found = await self.db.fetch_value(
            """
            SELECT v.id FROM recipe_versions v
            JOIN recipes s ON s.id = v.recipe_id
            WHERE s.automatch = 1 AND s.archived = 0
              AND v.profile_version_id = ?
              AND v.id = s.current_version_id
            ORDER BY v.id
            """,
            (profile_version_id,),
        )
        return None if found is None else int(found)

    async def profile_match(
        self,
        shot_id: int,
        *,
        profile_version_id: int | None,
        device_profile_id: str,
    ) -> ProfileMatch:
        """File a shot under the Recipe offered to the matcher that brews its profile.

        The only path into a Recipe that is not a person's own decision, for every
        shot alike: one a pull brought in, one an import did, one the button
        was pressed on.

        Only when the Recipe's current version names the shot's profile version.
        None is ``unmatched`` and leaves the shot in the inbox, where a button
        stands next to it: a shot filed under the wrong Recipe pollutes the trend
        chart of a Recipe it was never part of and nobody goes looking for it.

        A shot that already has a Recipe is never moved (the `IS NULL` guard), and
        comes back as ``unmatched`` with no version, since nothing was filed.
        """
        resolved = await self._resolved_profile_version(profile_version_id, device_profile_id)
        if resolved is None:
            return ProfileMatch(outcome="unmatched")
        pinned = await self.pinned_version(resolved)
        if pinned is not None:
            # A pin's copy names exactly one Recipe version, so there is nothing to guess: the
            # shot is that version's (a shot brewed between an unpin and the sync that removes
            # the file included). Like every automatic filing it only fills a NULL.
            if not await self._fill(shot_id, pinned):
                return ProfileMatch(outcome="unmatched")
            return ProfileMatch(outcome="matched", set_version_id=pinned)
        candidate = await self.version_naming_profile(resolved)
        if candidate is None or not await self._fill(shot_id, candidate):
            return ProfileMatch(outcome="unmatched")
        return ProfileMatch(outcome="matched", set_version_id=candidate)

    async def match_unfiled(self, shot_ids: Sequence[int] | None = None) -> ProfileMatchSummary:
        """Run :meth:`profile_match` over every shot that needs a Recipe.

        No ``shot_ids`` is the inbox exactly as the Shots page counts it
        (`NEEDS_RECIPE_SQL`), so a shot labelled Discard is not filed by the
        page's button: it is not on the list the button stands beside.
        ``shot_ids`` narrows it to those shots (the one-shot button on a shot's
        own page), and there a discarded shot is filed like any other, since
        the person asked about that shot by name; a shot in it that already has
        a Recipe, or is quarantined, is skipped rather than counted, since nothing
        was asked of it. Quarantined shots are never offered: their header is
        whatever the broken file happened to hold.
        """
        sql = "SELECT s.id, s.profile_version_id, s.profile_id_on_device FROM shots s WHERE "
        params: list[Any] = []
        if shot_ids is None:
            sql += NEEDS_RECIPE_SQL
        else:
            if not shot_ids:
                return ProfileMatchSummary()
            sql += "s.recipe_version_id IS NULL AND s.quarantined = 0"
            sql += f" AND s.id IN ({', '.join('?' for _ in shot_ids)})"
            params.extend(shot_ids)
        rows = await self.db.fetch_all(sql + " ORDER BY s.id", params)
        summary = ProfileMatchSummary()
        async with self.db.transaction():
            for row in rows:
                match = await self.profile_match(
                    int(row["id"]),
                    profile_version_id=row["profile_version_id"],
                    device_profile_id=row["profile_id_on_device"] or "",
                )
                if match.outcome == "matched":
                    summary.matched += 1
                else:
                    summary.unmatched += 1
        return summary

    # ── the spread ───────────────────────────────────────────────────

    async def counted_shots(self, set_id: int) -> list[CountedShot]:
        """Every shot of this Recipe that counts towards its spread, with its measures.

        "Counts" is three exclusions and no more. A **quarantined** shot never
        parsed, so its numbers are whatever the header happened to hold; an
        **incomplete** one stopped early, and its shot time measures the
        interruption rather than the recipe; a **Discard** says the shot went
        wrong, not that the recipe did, and grading a prediction on one reads
        the wrong signal. A shot nobody labelled counts: it is plain data, and
        excluding it would make the spread depend on how diligent somebody had
        been with the buttons.

        Each measure is read where it already lives and nothing is derived a
        second time. Three of them need a rule, and each rule is one this
        archive already applies somewhere else:

        * **"not recorded" is written as zero in several places**, so zero is
          read as absent where the engine cannot mean it literally. A
          ``duration_ms`` of 0 is a header that never got a time. The
          diagnostics engine writes ``0.0`` for an average over no samples
          (``_safe_mean([])``) and for ``max(pressures)`` over an empty list, so
          an average brew flow or a peak pressure of exactly zero is "nothing to
          average", not a shot that ran at no pressure. The **time to first
          drip** is the exception and is left alone: it is already nullable —
          the engine writes NULL when the flow never rose — so a 0.0 there is a
          real reading, the drip landing on the first sample.
        * **the JSON paths are guarded by type**, the same way a profile's
          temperature is read in `_VERSION_SELECT`: ``json_type(...) IN
          ('integer','real')``. A hand-edited row, an older document or a future
          shape could hold a string or an object at one of these paths, and a
          Recipe page that answered 500 because of one odd blob would be a bad
          trade for a number that is only ever an average.
        * **the yield follows the rule `starting/similar.py` already uses** —
          the judgement's typed dose out first, then the machine's final weight,
          then the device index's volume. A machine with no scale records
          nothing, so the only yield that exists is the one the person wrote
          down; and where both exist the typed one is the person correcting the
          scale, which is the number they would compare against.

        The version's recipe rides along on each shot, because grouping repeats
        is then a pass over one list rather than a second query.
        """
        rows = await self.db.fetch_all(
            f"""
            SELECT sh.id AS shot_id,
                   sh.recipe_version_id AS version_id,
                   {label_sql("v")} AS version_label,
                   v.profile_version_id,
                   v.grind_setting,
                   v.grind_value,
                   v.dose_g,
                   v.target_yield_g,
                   {_MEASURE_COLUMNS},
                   j.rating,
                   j.balance,
                   j.decision
            FROM shots sh
            JOIN recipe_versions v ON v.id = sh.recipe_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE v.recipe_id = ?
              AND sh.quarantined = 0
              AND sh.incomplete = 0
              AND (j.decision IS NULL OR j.decision != 'discard')
            ORDER BY sh.id
            """,  # noqa: S608 - the interpolation is the module constant above, the id is bound
            (set_id,),
        )
        return self.to_models(CountedShot, rows)

    async def shot_ids(self, set_id: int) -> set[int]:
        """Every shot filed under this Recipe, as a set of ids.

        For the callers whose question is "is this shot one of ours" — the
        insight tools, which must not hand another Recipe's shot ids back as
        evidence or accept one as it. Ids only: the answer is a membership
        test, and loading rows to make it would be the expensive way to say no.
        """
        rows = await self.db.fetch_all(
            """
            SELECT sh.id FROM shots sh
            JOIN recipe_versions v ON v.id = sh.recipe_version_id
            WHERE v.recipe_id = ?
            """,
            (set_id,),
        )
        return {int(row["id"]) for row in rows}

    async def search_shot_ids(
        self,
        set_id: int,
        *,
        version_id: int | None = None,
        decision: Decision | None = None,
        balance: str | None = None,
        since: str | None = None,
        until: str | None = None,
        ranges: Mapping[str, tuple[float | None, float | None]] | None = None,
    ) -> list[int]:
        """The ids of this Recipe's shots that pass the filters a column can answer.

        The first half of the shot search: whatever is a plain column — the
        version, the label, the balance, the day, and the ranges on the
        numbers in :data:`SEARCH_COLUMNS` — is filtered here, so the second
        half loads only the shots that can still match. A range on anything
        else is the caller's to apply over the loaded shots. ``since`` and
        ``until`` are ``YYYY-MM-DD`` days, both inclusive, of the UTC start
        time. Ordered by id, because the order is the caller's to decide.
        """
        where = ["v.recipe_id = :set_id"]
        params: dict[str, Any] = {"set_id": set_id}
        if version_id is not None:
            where.append("v.id = :version_id")
            params["version_id"] = version_id
        if decision is not None:
            where.append("j.decision = :decision")
            params["decision"] = decision
        if balance is not None:
            where.append("j.balance = :balance")
            params["balance"] = balance
        if since is not None:
            where.append("date(sh.started_at) >= :since")
            params["since"] = since
        if until is not None:
            where.append("date(sh.started_at) <= :until")
            params["until"] = until
        for index, (key, (low, high)) in enumerate(sorted((ranges or {}).items())):
            column = SEARCH_COLUMNS.get(key)
            if column is None:
                continue
            if low is not None:
                where.append(f"({column}) >= :low{index}")
                params[f"low{index}"] = low
            if high is not None:
                where.append(f"({column}) <= :high{index}")
                params[f"high{index}"] = high
        rows = await self.db.fetch_all(
            f"""
            SELECT sh.id
            FROM shots sh
            JOIN recipe_versions v ON v.id = sh.recipe_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE {" AND ".join(where)}
            ORDER BY sh.id
            """,  # noqa: S608 - the clauses are the literals above and the module constant, every value is bound
            params,
        )
        return [int(row["id"]) for row in rows]

    async def recipe_shots(
        self,
        set_id: int,
        *,
        version_id: int | None = None,
        decision: Decision | None = None,
        limit: int = 50,
    ) -> list[RecipeShotRow]:
        """This Recipe's shots, newest first, with the measures and the verdict.

        What :meth:`counted_shots` leaves out on purpose is here: a quarantined,
        an incomplete and a **Discard** shot are all listed, each saying which
        it is. This is the reading list rather than the arithmetic — "gather
        when it went good and when it went bad" — and a discarded shot is part
        of that story even though it counts towards nothing.

        The measures are read exactly as the spread reads them, from the same
        constant, so a number here and a number in the evidence table can never
        be two different readings of the same shot.
        """
        where = ["v.recipe_id = :set_id"]
        params: dict[str, Any] = {"set_id": set_id, "limit": limit}
        if version_id is not None:
            where.append("v.id = :version_id")
            params["version_id"] = version_id
        if decision is not None:
            where.append("j.decision = :decision")
            params["decision"] = decision
        rows = await self.db.fetch_all(
            f"""
            SELECT sh.id AS shot_id,
                   sh.recipe_version_id AS version_id,
                   {label_sql("v")} AS version_label,
                   sh.started_at,
                   sh.quarantined,
                   sh.incomplete,
                   {_MEASURE_COLUMNS},
                   j.rating,
                   j.balance,
                   j.decision,
                   j.notes,
                   j.taste_notes_json,
                   j.aroma_notes_json
            FROM shots sh
            JOIN recipe_versions v ON v.id = sh.recipe_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE {" AND ".join(where)}
            ORDER BY COALESCE(sh.started_at, '') DESC, sh.id DESC
            LIMIT :limit
            """,  # noqa: S608 - the clauses are the literals above, every value is bound
            params,
        )
        return self.to_models(RecipeShotRow, rows)

    # ── trends ───────────────────────────────────────────────────────

    async def trends(
        self, set_id: int, *, counted: Sequence[CountedShot] | None = None
    ) -> SetTrends:
        """Every shot in the Recipe, in order, plus the per-version averages.

        ``counted`` is the Recipe's counted shots when the caller already holds them
        (the chat's opening context reads them once for the spread, the evidence
        and these averages); left out, they are read here.

        One query for the shots and the averages folded in Python: the point
        series is what the chart draws, the bars are the same numbers grouped,
        and computing them twice in SQL would let a rounding difference put a
        bar off its own points.

        The ratio's dose is the *judgement's* when the person entered one and the
        version's otherwise: the dose exists nowhere on the machine unless somebody
        typed it, and the version is where the recipe says it. A shot with no dose
        anywhere has no ratio, and saying so is more useful than inventing one from
        the nominal basket size.
        """
        rows = await self.db.fetch_all(
            f"""
            SELECT sh.id AS shot_id, sh.device_id, sh.recipe_version_id,
                   {label_sql("v")} AS version_label,
                   sh.started_at, sh.duration_ms,
                   j.dose_in_g, j.dose_out_g, j.rating, v.dose_g AS version_dose_g,
                   COALESCE(sh.final_weight_g, sh.index_volume_g) AS volume_g
            FROM shots sh
            JOIN recipe_versions v ON v.id = sh.recipe_version_id
            LEFT JOIN shot_judgements j ON j.shot_id = sh.id
            WHERE v.recipe_id = ?
            ORDER BY COALESCE(sh.started_at, ''), sh.id
            """,  # noqa: S608 - the interpolation is the label expression, the id is bound
            (set_id,),
        )
        points: list[SetTrendPoint] = []
        for row in rows:
            points.append(
                SetTrendPoint(
                    shot_id=int(row["shot_id"]),
                    device_id=str(row["device_id"]),
                    set_version_id=int(row["set_version_id"]),
                    version_label=str(row["version_label"]),
                    started_at=row["started_at"],
                    duration_s=None if row["duration_ms"] is None else row["duration_ms"] / 1000,
                    ratio=brew_ratio(
                        judged_dose_g=row["dose_in_g"],
                        version_dose_g=row["version_dose_g"],
                        judged_yield_g=row["dose_out_g"],
                        scale_yield_g=row["volume_g"],
                    ),
                    rating=row["rating"],
                )
            )

        versions = await self.versions(set_id)
        if counted is None:
            counted = await self.counted_shots(set_id)
        ratios = {p.shot_id: p.ratio for p in points}
        summaries = [
            _trend_version(
                version,
                shots=sum(1 for p in points if p.set_version_id == version.id),
                counted=[shot for shot in counted if shot.version_id == version.id],
                ratios=ratios,
            )
            # Oldest first: a trend reads left to right.
            for version in sorted(versions, key=lambda v: (v.created_at, v.id))
        ]
        return SetTrends(set_id=set_id, versions=summaries, shots=points)


def _trend_version(
    version: SetVersionRow,
    *,
    shots: int,
    counted: Sequence[CountedShot],
    ratios: Mapping[int, float | None],
) -> SetTrendVersion:
    """One version's averages: the only place they are worked out.

    `get_set`'s trajectory, the chat's ledger line and the design chat's mean
    rating all read this, so they cannot disagree. Counted shots only, means only, and
    a mean over nothing is ``None`` rather than a zero.
    """
    columns: dict[str, list[float | None]] = {
        "duration_s": [shot.shot_time_s for shot in counted],
        "yield_g": [shot.yield_g for shot in counted],
        "ratio": [ratios.get(shot.shot_id) for shot in counted],
        "rating": [shot.rating for shot in counted],
        "first_drip_s": [shot.first_drip_s for shot in counted],
    }
    return SetTrendVersion(
        set_version_id=version.id,
        version_label=version.version_label,
        intent=version.intent,
        origin=version.origin,
        created_at=version.created_at,
        shots=shots,
        counted_shots=len(counted),
        avg_duration_s=_mean(columns["duration_s"]),
        avg_yield_g=_mean(columns["yield_g"]),
        avg_ratio=_mean(columns["ratio"]),
        avg_rating=_mean(columns["rating"]),
        avg_first_drip_s=_mean(columns["first_drip_s"]),
        averaged_over=SetTrendCounts(
            **{
                name: sum(1 for value in values if value is not None)
                for name, values in columns.items()
            }
        ),
    )


def _mean(values: list[float | None] | list[int | None]) -> float | None:
    """The mean of the values that exist, or ``None`` when none of them do.

    ``None`` rather than 0: a version whose shots recorded no value has no
    average, and a zero would draw a bar at the bottom of the chart saying the
    recipe was terrible.
    """
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 3)
