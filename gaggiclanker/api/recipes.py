"""`/api/recipes` — what you were trying, version by version.

Five routes and one shape. A Recipe is created whole (identity plus its first
version, in one request, because a Recipe with no version is not a Recipe); everything
after that is **append-only**: a new version records what changed, why, and
which version it came from. Nothing here edits a version in place, and that is
the design rather than an omission — see `db/repos/recipes.py`.

`GET /api/recipes/{id}` is the page: the Recipe, every version newest first with the
diff against its parent already computed, and the shots grouped under the
version they were pulled with.

Three routes do edit a version, and they are the exceptions that prove the
rule: they write the **prediction** (only while the version has no shots) and
the **outcome** (at any time, and clearable). Neither is part of the recipe. The
fourth, `/rollback`, writes no version at all: it puts the Recipe back on an
another one (the Recipe's `current_version_id`) and logs that it did.

`/proposals` is where a change an agent suggested waits for the person. A
proposal has changed nothing; `/accept` is what turns one into a version and
`/decline` is what turns it down, and both are routes somebody presses. There is
no tool for either, in the chat or over MCP — the agent proposes and the person
decides, which is the same rule as everywhere else in this box.

`/design` starts a Recipe the other way round: a bean and a grinder, a version 1
with no recipe, and the conversation in which the recipe is worked out. The
first version written to such a Recipe fills that version 1 in place, whichever
route writes it; `DELETE /{id}/design` removes a design nobody brewed anything
under.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, NoReturn

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from gaggiclanker.api.deps import (
    BeansRepoDep,
    BoardServiceDep,
    DatabaseDep,
    GrindersRepoDep,
    InsightDeletionsRepoDep,
    JudgementsRepoDep,
    OutcomeProposalsRepoDep,
    PinsRepoDep,
    ProfilesRepoDep,
    RecipeProposalsRepoDep,
    RecipesRepoDep,
    ShotsRepoDep,
)
from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.db.repos.insight_deletions import (
    DeletionResult,
    InsightDeletionRefusal,
    InsightDeletionRow,
)
from gaggiclanker.db.repos.judgements import ShotJudgementRow
from gaggiclanker.db.repos.outcome_proposals import (
    OutcomeProposalRefusal,
    OutcomeProposalRow,
    OutcomeResult,
)
from gaggiclanker.db.repos.profile_board import BoardRow
from gaggiclanker.db.repos.recipe_proposals import (
    ProposalKind,
    ProposalRefusal,
    ProposalStatus,
    ProposalWriteResult,
    PutProfile,
    RecipeProposalRow,
    RecipeProposalsRepository,
)
from gaggiclanker.db.repos.recipes import (
    DesignBrief,
    DesignRefusal,
    FieldChange,
    LongText,
    RecipeRevertRow,
    RecipeRow,
    RecipeTrackRecord,
    RecipeTrends,
    RecipeVersionChange,
    RecipeVersionPatch,
    RecipeVersionRow,
    RecipeVersionWrite,
    RecipeWrite,
    RollbackWrite,
    VersionLabelCounts,
    VersionOutcomeWrite,
    VersionPredictionWrite,
    VersionRefusal,
    VersionRefused,
    VersionWriteResult,
    dead_end_ids,
    design_refusal_message,
    track_record,
    version_changes,
)
from gaggiclanker.db.repos.shots import ShotListItem
from gaggiclanker.domain.drinks import Drink
from gaggiclanker.domain.recipes import recipe_name
from gaggiclanker.domain.spread import (
    MeasureSpread,
    VersionEvidence,
    pooled_spreads,
    spread_report,
    version_evidence,
)
from gaggiclanker.domain.vocab import VersionOutcome
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import AppError, Conflict, NotFound, Unprocessable

__all__ = ["router"]

router = APIRouter(prefix="/recipes", tags=["recipes"])

#: How many shots a Recipe page loads. A Recipe that has run past this is a Recipe worth
#: a filtered shots list, which the page links to.
SHOTS_PER_RECIPE = 500

#: How long the note on a decline may be. A sentence or two: it is read
#: beside the proposal and quoted to the next conversation, both of which
#: are places a paragraph would be in the way.
DECLINE_NOTE_MAX = 500


class RecipeCreate(BaseModel):
    """`POST /api/recipes`: the identity and the first version, in one request."""

    model_config = ConfigDict(extra="forbid")

    name: str
    bean_id: int
    grinder_id: int | None = None
    #: Version 1. Everything on it is optional but the drink — a Recipe can start as "this
    #: bean, this grinder, we will see", and the wizard fills in the rest — so the body must
    #: carry a version, and the version a drink.
    version: RecipeVersionWrite
    #: Whether the matcher may file shots under this Recipe. True by default: a
    #: Recipe is created by somebody who has just put that bean in a hopper, and one
    #: that did not collect the shots pulled on its profile would look broken.
    #: It takes nothing away from any other Recipe.
    automatch: bool = True


class RecipeVersionAdd(RecipeVersionChange):
    """`POST /api/recipes/{id}/versions`: the change, and whether it is a major one.

    ``major`` is the person's answer to the form's "Major change" box: true
    starts the next major (v1.2 → v2), false takes the next minor (v1.2 → v1.3).
    Left out, it is a minor. It is not part of the recipe, so it is split off
    before the patch reaches the repository. Strictly a boolean: "yes" or 1 is
    a request somebody should look at, not a guess this route makes.
    """

    major: StrictBool | None = None
    #: Only for a Recipe that has no profile (one being designed, or an old one that never named
    #: one): the profile it starts on, which is the active version of a profile with no Recipe, as
    #: for a new Recipe. A Recipe that has a profile has no choice: its versions follow the profile.
    profile_version_id: int | None = None


class ProposalAccept(BaseModel):
    """`POST .../accept`: the person's answer to the card's "Major change" box.

    Optional, as the body itself is: left out, it is a minor. The agent's
    suggestion is never the default — it only preselects the box.
    """

    model_config = ConfigDict(extra="forbid")

    major: StrictBool | None = None
    #: A first recipe only: the name for its new profile, stored with the accept. The accept
    #: always puts the card's draft on the profile list in the same transaction as version 1;
    #: this renames it first, and is allowed only when the draft is a new profile (a taken or
    #: empty name refuses the whole accept; a draft that continues a profile keeps its name).
    profile_label: str | None = Field(default=None, max_length=200)


class RecipeDesignCreate(DesignBrief):
    """`POST /api/recipes/design`: what the design wizard asks, and the brief it stores.

    The brief's own fields — the profile to fork, the usual grind, the goal and
    whichever of the grind, dose and yield the form already had filled in — are
    inherited from :class:`DesignBrief`, so the route and the stored brief cannot
    disagree about their limits.
    """

    bean_id: int
    #: Required here, where the brief it is stored as allows a brief from before drinks: the
    #: drink the design is for. The design's version 1 takes it (and the notes) from the brief.
    drink: Drink
    #: Required: a grind is only ever a number on one grinder's dial, and a
    #: design with no grinder has nothing to anchor one on. Pre-ground coffee
    #: keeps the ordinary New Recipe form and its suggestions.
    grinder_id: int
    #: Defaults to "<bean> (<grinder>)", the name every new Recipe from a
    #: suggestion gets.
    name: str | None = Field(default=None, min_length=1, max_length=200)


class RecipeDesignCreated(BaseModel):
    """What starting a design produced: the Recipe, its empty version 1, and its chat."""

    model_config = ConfigDict(extra="forbid")

    recipe: RecipeRow
    version: RecipeVersionRow
    #: Version 1's conversation, opened here so the page can go straight to it.
    thread_id: int


class RecipeDesignDiscarded(BaseModel):
    """`DELETE /api/recipes/{id}/design`: the Recipe that no longer exists."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: int
    discarded: bool = True


class AutomatchWrite(BaseModel):
    """`PUT /api/recipes/{id}/automatch`: whether the matcher may file shots here."""

    model_config = ConfigDict(extra="forbid")

    automatch: bool


class RecipeListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RecipeRow]


class ProfileChoice(BaseModel):
    """What a Recipe may start on: the version to name and the profile's name."""

    model_config = ConfigDict(extra="forbid")

    profile_version_id: int
    label: str


class TakenProfileChoice(BaseModel):
    """A profile a new Recipe cannot start on because it has a Recipe: New Recipe lists it to
    duplicate."""

    model_config = ConfigDict(extra="forbid")

    #: The profile's active version, the one Duplicate Profile opens on.
    profile_version_id: int
    label: str
    recipe_id: int
    recipe_name: str


class ProfileChoiceListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProfileChoice]
    #: Profiles that have a Recipe, for New Recipe to offer as duplicates; never accepted as a
    #: start.
    taken: list[TakenProfileChoice] = []


class VersionPinned(BaseModel):
    """A version that has a live pin: which, and how many shots were brewed on its copy."""

    model_config = ConfigDict(extra="forbid")

    pin_id: int
    #: Shots filed under this version whose profile is the pin's copy (a share of the version's
    #: own ``shot_count``, which counts every shot filed under it).
    shot_count: int


class RecipePin(BaseModel):
    """One live pin, as the Recipe page's "Pinned on the machine" card lists it."""

    model_config = ConfigDict(extra="forbid")

    pin_id: int
    recipe_version_id: int
    #: The pinned version's name, "v1.2".
    version_label: str
    #: The copy's name on the machine.
    name: str
    #: The profile-list row that carries the copy: the Profiles page's row for the pin.
    board_id: int
    pinned_at: str
    shot_count: int


class RecipeVersionDetail(BaseModel):
    """One version, its diff against its parent, and the shots pulled with it."""

    model_config = ConfigDict(extra="forbid")

    version: RecipeVersionRow
    #: What this version changed, computed against `parent_version_id`. Empty
    #: for version 1, which is a baseline rather than a change to anything.
    changes: list[FieldChange]
    shots: list[ShotListItem]
    #: The Recipe went back past this version: it is not on the line the Recipe is on
    #: now (walked back from the current version through the fork history).
    #: Computed from the list, not from the row: it is a fact about what came
    #: after, not about this version.
    dead_end: bool = False
    #: How this version's shots were rated (and how many were discarded). Counted over the
    #: Recipe's shots rather than over `shots` above, which is capped at `SHOTS_PER_RECIPE`.
    labels: VersionLabelCounts = VersionLabelCounts()
    #: This version's shots against the compared version's, measure by measure,
    #: with each difference marked as beyond the Recipe's spread or inside it.
    #: NULL on a version with no prediction: there is nothing it is evidence
    #: for, and a table answering no question is noise in a log people read.
    evidence: VersionEvidence | None = None
    #: The conversation this version was argued in, when it came from an
    #: accepted proposal and that conversation still exists. The log offers a
    #: way back into the room where the reasoning is.
    chat_thread_id: int | None = None
    #: The grade an agent proposed for this version that the person has not
    #: answered. NULL once it is answered; never the recorded outcome, which is
    #: `version.outcome` and is written only by a person.
    outcome_proposal: OutcomeProposalRow | None = None
    #: The live pin of this version, if it has one.
    pinned: VersionPinned | None = None


class RecipeProposalDetail(BaseModel):
    """One proposed change, as the card that asks about it needs it.

    Written out field by field rather than dumped from the repository's row,
    because the row carries the change as a **patch** — a shape whose nulls mean
    "clear this field" and whose absences mean "leave it alone". That is the
    right thing to store and the wrong thing to hand a browser, which wants the
    same before-and-after lines the experiment log already draws. So the patch
    stays inside the repository and `changes` comes out.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    recipe_id: int
    #: `change` for one move on a recipe, `design` for a Recipe's whole first
    #: recipe — which fills version 1 in place when accepted.
    kind: ProposalKind = "change"
    #: The profile draft an initial recipe carries, waiting on the Profiles
    #: page to be approved and pushed. Empty on a change.
    draft_id: int | None = None
    #: The conversation it was argued in, if that conversation still exists.
    thread_id: int | None = None
    base_version_id: int
    base_version_label: str | None = None
    #: Whether the Recipe is still on the version this was proposed against.
    #: ``False`` means Accept will refuse: the change was argued against a
    #: recipe nobody is brewing any more.
    base_is_current: bool = True
    #: What it would change, rendered exactly as a version's own diff is.
    changes: list[FieldChange] = []
    #: The same changes named as a person names them ("the grind", "the dose"),
    #: which is what a one-line summary reads.
    changed: list[str] = []
    reason: str = ""
    prediction: str = ""
    compares_to_version_id: int | None = None
    compares_to_version_label: str | None = None
    #: Why two things had to move together, when the agent said they did. Empty
    #: for the ordinary one-change proposal.
    combined_reason: str = ""
    #: The agent suggests recording this as a major version, and why. Shown
    #: beside the card's "Major change" box, which it preselects; the person
    #: decides.
    suggest_major: bool = False
    major_reason: str = ""
    #: What Accept would name the version as a minor and as a major — the
    #: button says one of them, whichever the box says. Worked out by the same
    #: query the insert numbers with, so the web never numbers anything; both
    #: v1 on a first recipe, which fills version 1.
    next_minor_label: str = ""
    next_major_label: str = ""
    #: False when the stored change cannot be read back as a patch, which
    #: only a hand-edited row can produce. `changes` is then empty and
    #: Accept is refused; Decline still works, because getting rid of it
    #: needs no patch.
    readable: bool = True
    status: ProposalStatus = "proposed"
    decline_note: str = ""
    resulting_version_id: int | None = None
    resulting_version_label: str | None = None
    #: The agent's grade of the base version, waiting beside this change.
    #: Accepting the change records it first, in the same transaction, so the
    #: card says so. NULL when nothing is waiting, and on a change that has
    #: already been answered.
    records_outcome: OutcomeProposalRow | None = None
    created_at: str
    decided_at: str | None = None


class RecipeProposalListData(BaseModel):
    """`GET /api/recipes/{id}/proposals`: every change ever proposed, newest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[RecipeProposalDetail]


class RecipeProposalDecision(BaseModel):
    """What accepting one produced: the proposal as it now stands, and the version."""

    model_config = ConfigDict(extra="forbid")

    proposal: RecipeProposalDetail
    #: The version Accept appended. NULL after a decline, which creates none.
    version: RecipeVersionRow | None = None
    #: The profile draft a first recipe's accept put on the list, and the profile it became,
    #: so a sync can follow. NULL when nothing was put.
    profile_draft_id: int | None = None
    profile_row_id: int | None = None


class ProposalDecline(BaseModel):
    """`POST .../decline`: the turn-down, and optionally why.

    The note is worth typing: it is what the next conversation is told, and
    "not that, the last two finer grinds went the wrong way" is the sentence
    that stops the same proposal coming back next week.
    """

    model_config = ConfigDict(extra="forbid")

    #: Capped at a sentence or two, like every other short note here: it is
    #: read beside the proposal and quoted into the next conversation's
    #: opening context, and an essay in either place is a wall nobody reads
    #: and a token budget spent on one turn-down.
    note: str = Field(default="", max_length=DECLINE_NOTE_MAX)


class RecipeDetailData(BaseModel):
    """`GET /api/recipes/{id}`: everything the Recipe page draws."""

    model_config = ConfigDict(extra="forbid")

    recipe: RecipeRow
    versions: list[RecipeVersionDetail]
    #: Verdicts for the shots above, keyed by shot id as a string (JSON object
    #: keys are strings, and pretending otherwise costs the client a cast).
    judgements: dict[str, ShotJudgementRow]
    #: How often this Recipe's predictions held. The page's one headline number.
    track_record: RecipeTrackRecord = RecipeTrackRecord()
    #: How much this Recipe's shots vary when nothing in the recipe changed, per
    #: measure, with the basis under each one. Worked out by the app and not by
    #: a model, so the same shots always give the same figure.
    spread: list[MeasureSpread] = []
    #: The version the page offers to go back to: the newest one made, other
    #: than the current, that has a Good shot (4 or 5 stars, not discarded). NULL when there is
    #: nowhere to go.
    rollback_target_version_id: int | None = None
    #: Every time this Recipe went back to another version, newest first: the
    #: log's "Went back to v1.2 (from v2.1)" lines. Not versions.
    reverts: list[RecipeRevertRow] = []
    #: The change an agent has proposed and nobody has answered yet. At most one
    #: per Recipe, and it has changed nothing: the next shot is still filed under
    #: the recipe in the hopper until somebody presses Accept.
    proposal: RecipeProposalDetail | None = None
    #: The agent's grade of the **current** version, waiting for the person.
    outcome_proposal: OutcomeProposalRow | None = None
    #: The versions of this Recipe kept on the machine as profiles of their own, newest pin first.
    #: Live pins only; empty when there are none.
    pins: list[RecipePin] = []


def _missing(field: str, value: int, noun: str) -> NoReturn:
    """422 naming the field, for a reference that does not resolve."""
    raise Unprocessable(
        f"No {noun} {value}",
        details={"field": field, "message": f"that {noun} does not exist"},
    )


def _unwrap(
    result: VersionWriteResult, recipe_id: int, version_id: int | None = None
) -> JSONResponse:
    """The written version, or the one error its refusal means.

    The repository answers with a slug and no opinion about HTTP; this is the
    one place a slug becomes a status, a code and a sentence. `details` names
    the field at fault and never repeats what was sent — a prediction is the
    person's own words and has no business in an error body.
    """
    if result.version is not None:
        return envelope_response(result.version.model_dump(mode="json"))
    raise _REFUSALS[result.refused or "no_version"](recipe_id, version_id)


def _refusal_no_version(recipe_id: int, version_id: int | None) -> AppError:
    return NotFound(f"No version {version_id} in Recipe {recipe_id}")


def _refusal_has_shots(recipe_id: int, version_id: int | None) -> AppError:
    return Conflict(
        f"Version {version_id} already has shots",
        code="VERSION_HAS_SHOTS",
        details={
            "field": "prediction",
            "message": "a prediction is written before the shots, not after them",
        },
    )


def _refusal_has_outcome(recipe_id: int, version_id: int | None) -> AppError:
    return Conflict(
        f"Version {version_id} has already been graded",
        code="VERSION_HAS_OUTCOME",
        details={
            "field": "prediction",
            "message": "clear the outcome first — it grades the prediction as it was written",
        },
    )


def _refusal_bad_compare(recipe_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        "That is not a version this prediction can be compared against",
        details={
            "field": "compares_to_version_id",
            "message": "it must be an earlier version of this Recipe",
        },
    )


def _refusal_no_prediction(recipe_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        f"Version {version_id} states no prediction",
        details={"field": "outcome", "message": "there is nothing to grade"},
    )


def _refusal_nothing_to_grade(recipe_id: int, version_id: int | None) -> AppError:
    return Conflict(
        f"Version {version_id} has no rated shot yet",
        code="NOTHING_TO_GRADE",
        details={
            "field": "outcome",
            "message": (
                "no shot of the version is rated yet (a discarded shot does not count): "
                "rate a shot before grading the prediction"
            ),
        },
    )


def _refusal_no_target(recipe_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        f"No such version in Recipe {recipe_id}",
        details={"field": "to_version_id", "message": "it must be a version of this Recipe"},
    )


def _refusal_current_version(recipe_id: int, version_id: int | None) -> AppError:
    return Unprocessable(
        "That is already the current version",
        details={
            "field": "to_version_id",
            "message": "pick another version of this Recipe to go back to",
        },
    )


def _refusal_designing(recipe_id: int, version_id: int | None) -> AppError:
    return Conflict(
        design_refusal_message("designing"),
        code="DESIGNING",
        details={"field": "recipe_id", "message": "there is no other version to go back to yet"},
    )


def version_refused(exc: VersionRefused) -> AppError:
    """A version write the repository refused mid-transaction, as its 409.

    Today every such refusal is about a Recipe being designed whose version 1 can
    no longer be filled, and each carries its own code
    (`DESIGN_HAS_SHOTS`, `DESIGN_HAS_VERSIONS`) so a page can say which.
    Public because the other path that writes a version — a push for a Recipe —
    answers with the same error.
    """
    if exc.refused == "profile_is_a_pin":
        return Conflict(
            exc.message,
            code=exc.code,
            details={
                "field": "profile_version_id",
                "message": "name the profile it was copied from",
            },
        )
    if exc.refused in (
        "profile_has_a_recipe",
        "other_profile",
        "not_the_active_version",
        "profile_deleted",
    ):
        return Conflict(
            exc.message,
            code=exc.code,
            details={"field": "profile_version_id", "message": "a profile has at most one Recipe"},
        )
    return Conflict(
        exc.message,
        code=exc.code,
        details={
            "field": "recipe_id",
            "message": "version 1 of this Recipe can no longer be filled",
        },
    )


def _proposal_error(refusal: ProposalRefusal, recipe_id: int, proposal_id: int) -> AppError:
    """A proposal refusal as the one error it means.

    Two of them are 409s with codes of their own, because they are the two the
    page has to explain in words rather than as "something went wrong": the Recipe
    moved on while the proposal was waiting, and the current version's
    prediction has still not been graded. A client that can tell them apart can
    say which, and offer the right next thing — draft again, or grade.

    `details` names the field at fault and never repeats what was sent.
    """
    if refusal == "stale":
        return Conflict(
            f"Proposal {proposal_id} was made against a version that is no longer current",
            code="PROPOSAL_STALE",
            details={
                "field": "base_version_id",
                "message": "the Recipe moved on since; ask in the chat for the change again",
            },
        )
    if refusal == "outcome_open":
        return Conflict(
            "This version's prediction has not been graded yet",
            code="PROPOSAL_OUTCOME_OPEN",
            details={
                "field": "outcome",
                "message": "record how the current version's prediction turned out first",
            },
        )
    if refusal == "grade_unrecordable":
        return Conflict(
            "The grade waiting for this version can no longer be recorded",
            code="PROPOSAL_GRADE_UNRECORDABLE",
            details={
                "field": "outcome",
                "message": (
                    "no shot of the version is rated any more (a discarded shot does not "
                    "count): rate a shot, or dismiss the grade, and accept again"
                ),
            },
        )
    if refusal == "unreadable":
        return Conflict(
            f"Proposal {proposal_id} cannot be read",
            code="PROPOSAL_UNREADABLE",
            details={
                "field": "patch",
                "message": "the change stored with it is damaged; decline it and ask again",
            },
        )
    if refusal == "draft_closed":
        return Conflict(
            f"The profile draft proposal {proposal_id} carries is no longer open",
            code="PROPOSAL_DRAFT_CLOSED",
            details={
                "field": "draft_id",
                "message": (
                    "its draft was discarded or replaced on the Profiles page; decline this card "
                    "and ask the agent in the Recipe's chat for a new one"
                ),
            },
        )
    if refusal == "profile_is_a_pin":
        return Conflict(
            design_refusal_message(refusal),
            code="PROFILE_IS_A_PIN",
            details={
                "field": "profile_version_id",
                "message": "name the profile it was copied from",
            },
        )
    if refusal in ("design_has_shots", "design_has_versions"):
        return Conflict(
            design_refusal_message(refusal),
            code=refusal.upper(),
            details={
                "field": "recipe_id",
                "message": "version 1 of this Recipe can no longer be filled",
            },
        )
    if refusal == "not_waiting":
        return Conflict(
            f"Proposal {proposal_id} has already been answered",
            code="PROPOSAL_DECIDED",
            details={"field": "status", "message": "it is no longer waiting for a decision"},
        )
    return NotFound(f"No proposal {proposal_id} in Recipe {recipe_id}")


def _design_error(refusal: DesignRefusal, recipe_id: int) -> AppError:
    """A refused discard, as the one error it means. Each 409 has its own code."""
    if refusal == "no_recipe":
        return NotFound(f"No Recipe {recipe_id}")
    return Conflict(
        design_refusal_message(refusal),
        code=refusal.upper(),
        details={
            "field": "recipe_id",
            "message": "only a Recipe still being designed, with nothing filed on it, is discarded",
        },
    )


#: Every refusal the repository can answer with, and the error it becomes.
_REFUSALS: dict[VersionRefusal, Callable[[int, int | None], AppError]] = {
    "no_version": _refusal_no_version,
    "has_shots": _refusal_has_shots,
    "has_outcome": _refusal_has_outcome,
    "bad_compare": _refusal_bad_compare,
    "no_prediction": _refusal_no_prediction,
    "nothing_to_grade": _refusal_nothing_to_grade,
    "no_target": _refusal_no_target,
    "current_version": _refusal_current_version,
    "designing": _refusal_designing,
}


async def _proposal_detail(
    proposals: RecipeProposalsRepository, row: RecipeProposalRow
) -> RecipeProposalDetail:
    """One proposal with its change rendered the way the log renders a version's.

    The diff is computed against a version that does not exist yet — the base
    with the patch applied — so the card and the log entry it will become are
    drawn by the same function. What the other side is comes from
    :meth:`RecipeProposalsRepository.diff_base`: a first recipe is drawn against an
    empty one, so it keeps its recipe after it fills version 1. Labels are
    gathered from both sides rather than from the Recipe's versions, because a
    proposal may name a profile this Recipe has never been on, and a bare row id is
    not a profile anybody recognises.
    """
    base = await proposals.diff_base(row)
    preview = await proposals.preview(row)
    labels = {
        version.profile_version_id: version.profile_label
        for version in (base, preview)
        if version is not None and version.profile_version_id is not None and version.profile_label
    }
    changes = (
        version_changes(preview, base, labels) if base is not None and preview is not None else []
    )
    names = await proposals.next_names(row.recipe_id)
    return RecipeProposalDetail(
        id=row.id,
        recipe_id=row.recipe_id,
        kind=row.kind,
        draft_id=row.draft_id,
        thread_id=row.thread_id,
        base_version_id=row.base_version_id,
        base_version_label=row.base_version_label,
        base_is_current=row.base_is_current,
        changes=changes,
        changed=row.changed,
        readable=row.readable,
        reason=row.reason,
        prediction=row.prediction,
        compares_to_version_id=row.compares_to_version_id,
        compares_to_version_label=row.compares_to_version_label,
        combined_reason=row.combined_reason,
        suggest_major=row.suggest_major,
        major_reason=row.major_reason,
        next_minor_label=names.minor,
        next_major_label=names.major,
        status=row.status,
        decline_note=row.decline_note,
        resulting_version_id=row.resulting_version_id,
        resulting_version_label=row.resulting_version_label,
        records_outcome=(
            await proposals.outcomes.waiting_for_version(row.base_version_id)
            if row.status == "proposed"
            and row.kind == "change"
            and await proposals.records_a_grade(row)
            else None
        ),
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


async def _decided(
    proposals: RecipeProposalsRepository,
    result: ProposalWriteResult,
    recipe_id: int,
    proposal_id: int,
) -> JSONResponse:
    """The proposal as it now stands, or the one error its refusal means."""
    if result.proposal is None or result.refused is not None:
        raise _proposal_error(result.refused or "no_proposal", recipe_id, proposal_id)
    return envelope_response(
        RecipeProposalDecision(
            proposal=await _proposal_detail(proposals, result.proposal),
            version=result.version,
            profile_draft_id=result.profile_draft_id,
            profile_row_id=None if result.profile_row is None else result.profile_row.id,
        ).model_dump(mode="json")
    )


@router.get(
    "/profile-choices",
    response_model=ApiResponse[ProfileChoiceListData],
    summary="The profiles a new Recipe may start on",
)
async def profile_choices(recipes: RecipesRepoDep) -> JSONResponse:
    """The active version of each profile that has no Recipe, then each mirrored machine file no
    profile stands on and no Recipe names (with Writes off the list has no profiles, so the files
    are what there is). Exactly what New Recipe and a Recipe with no profile accept: one function
    decides both.

    `taken` lists the profiles that have a Recipe, with that Recipe, which New Recipe offers to
    duplicate (Duplicate Profile) and never accepts as they are."""
    return envelope_response(
        ProfileChoiceListData(
            items=[
                ProfileChoice(profile_version_id=i, label=label)
                for i, label in await recipes.profile_choices()
            ],
            taken=[
                TakenProfileChoice(
                    profile_version_id=i, label=label, recipe_id=recipe_id, recipe_name=recipe_name
                )
                for i, label, recipe_id, recipe_name in await recipes.taken_profile_choices()
            ],
        ).model_dump(mode="json")
    )


@router.get(
    "",
    response_model=ApiResponse[RecipeListData],
    summary="The Recipes, the ones collecting shots first",
)
async def list_recipes(
    recipes: RecipesRepoDep,
    include_archived: Annotated[bool, Query()] = False,
) -> JSONResponse:
    rows = await recipes.list_recipes(include_archived=include_archived)
    return envelope_response(RecipeListData(items=rows).model_dump(mode="json"))


@router.post(
    "",
    response_model=ApiResponse[RecipeRow],
    status_code=201,
    summary="Start a Recipe, with its first version",
)
async def create_recipe(
    body: RecipeCreate,
    recipes: RecipesRepoDep,
    beans: BeansRepoDep,
    grinders: GrindersRepoDep,
    profiles: ProfilesRepoDep,
) -> JSONResponse:
    """Create the Recipe and version 1 atomically.

    Every reference is checked here rather than left to the foreign keys. A
    failed key raises `IntegrityError` from inside the transaction, which the
    envelope can only report as an internal error — a 500 on a request whose
    only problem is a stale id in a dropdown, and with nothing in the body
    saying which of the three ids was wrong. Each check answers that instead:
    422, with `details.field` naming the one at fault.
    """
    # A null grinder is legitimate — pre-ground coffee — so only a stated id is looked up.
    if await beans.get(body.bean_id) is None:
        _missing("bean_id", body.bean_id, "bean")
    if body.grinder_id is not None and await grinders.get(body.grinder_id) is None:
        _missing("grinder_id", body.grinder_id, "grinder")
    # A Recipe brews one profile, the one its first version names: a profile no Recipe has (a
    # profile that has one is refused by the repository, with the way forward).
    profile_version_id = body.version.profile_version_id
    if profile_version_id is None:
        raise Unprocessable(
            "A Recipe needs a profile: pick one that has no Recipe, or design one with the agent.",
            details={"field": "version.profile_version_id", "message": "a profile is required"},
        )
    if await profiles.get_version(profile_version_id) is None:
        _missing("version.profile_version_id", profile_version_id, "profile version")

    try:
        row = await recipes.create(
            RecipeWrite(
                name=body.name,
                bean_id=body.bean_id,
                grinder_id=body.grinder_id,
            ),
            body.version,
            automatch=body.automatch,
        )
    except VersionRefused as exc:
        raise version_refused(exc) from None
    return envelope_response(row.model_dump(mode="json"), status_code=201)


@router.post(
    "/design",
    response_model=ApiResponse[RecipeDesignCreated],
    status_code=201,
    summary="Start a Recipe to be designed in its own conversation",
)
async def design_recipe(
    body: RecipeDesignCreate,
    db: DatabaseDep,
    recipes: RecipesRepoDep,
    beans: BeansRepoDep,
    grinders: GrindersRepoDep,
    profiles: ProfilesRepoDep,
) -> JSONResponse:
    """A Recipe with a bean and a grinder and no version yet, and the chat to design it in.

    Version 1 states nothing: the agent works the recipe out with the person
    in version 1's own conversation, which is opened here and answered with the
    design prompt while the Recipe is being designed. Nothing is proposed, drafted
    or sent anywhere by this route.

    Every reference is looked up first and a missing one is a 404 naming the
    field, never the value sent.
    """
    bean = await beans.get(body.bean_id)
    if bean is None:
        raise NotFound(
            "No such bean", details={"field": "bean_id", "message": "that bean does not exist"}
        )
    grinder = await grinders.get(body.grinder_id)
    if grinder is None:
        raise NotFound(
            "No such grinder",
            details={"field": "grinder_id", "message": "that grinder does not exist"},
        )
    fork = body.fork_profile_version_id
    if fork is not None and await profiles.get_version(fork) is None:
        raise NotFound(
            "No such profile version",
            details={
                "field": "fork_profile_version_id",
                "message": "that profile version does not exist",
            },
        )

    row = await recipes.create_design(
        RecipeWrite(
            name=body.name or recipe_name(bean.name, grinder.name),
            bean_id=bean.id,
            grinder_id=grinder.id,
        ),
        DesignBrief.model_validate(body.model_dump(include=set(DesignBrief.model_fields))),
    )
    version = await recipes.current_version(row.id)
    opened = await ChatRepository(db).open_thread(row.id)
    if version is None or opened.thread is None:  # pragma: no cover - created just above
        raise NotFound(f"No Recipe {row.id}")
    return envelope_response(
        RecipeDesignCreated(recipe=row, version=version, thread_id=opened.thread.id).model_dump(
            mode="json"
        ),
        status_code=201,
    )


@router.delete(
    "/{recipe_id}/design",
    response_model=ApiResponse[RecipeDesignDiscarded],
    summary="Discard a Recipe that is still being designed",
)
async def discard_design(recipe_id: int, recipes: RecipesRepoDep) -> JSONResponse:
    """Delete it, its conversations and its proposals; discard its drafts.

    Only while the Recipe is being designed and nothing is filed on it. A Recipe with
    a recipe or a shot has history, and is archived rather than deleted: 409
    `NOT_DESIGNING` or `DESIGN_HAS_SHOTS`.
    """
    refusal = await recipes.discard_design(recipe_id)
    if refusal is not None:
        raise _design_error(refusal, recipe_id)
    return envelope_response(RecipeDesignDiscarded(recipe_id=recipe_id).model_dump(mode="json"))


@router.get(
    "/{recipe_id}",
    response_model=ApiResponse[RecipeDetailData],
    summary="One Recipe with its versions, their diffs and their shots",
)
async def get_recipe(
    recipe_id: int,
    recipes: RecipesRepoDep,
    shots: ShotsRepoDep,
    judgements: JudgementsRepoDep,
    proposals: RecipeProposalsRepoDep,
    outcomes: OutcomeProposalsRepoDep,
    pins: PinsRepoDep,
) -> JSONResponse:
    row = await recipes.get(recipe_id)
    if row is None:
        raise NotFound(f"No Recipe {recipe_id}")
    versions = await recipes.versions(recipe_id)
    by_id = {version.id: version for version in versions}
    labels = {
        version.profile_version_id: version.profile_label
        for version in versions
        if version.profile_version_id is not None and version.profile_label
    }

    # One shots query for the whole Recipe rather than one per version: a Recipe with
    # five versions is five round trips otherwise, and the rows are grouped in
    # memory from a column that is already in the projection.
    page = await shots.list_shots(recipe_id=recipe_id, limit=SHOTS_PER_RECIPE)
    grouped: dict[int, list[ShotListItem]] = {version.id: [] for version in versions}
    for shot in page.items:
        if shot.recipe_version_id in grouped:
            grouped[shot.recipe_version_id].append(shot)

    dead_ends = dead_end_ids(versions, row.current_version_id)
    counts = await recipes.label_counts(recipe_id)
    # One pass over the Recipe's counted shots feeds both the spread and every
    # version's evidence: the evidence is held against the spread, and two
    # queries could answer with two different sets of shots if one landed
    # between them.
    counted = await recipes.counted_shots(recipe_id)
    spreads = pooled_spreads(counted)
    # One query for every version that came out of an accepted proposal, so the
    # log can offer a way back into the conversation the change was argued in
    # without a request per row.
    threads = await proposals.accepted_threads(recipe_id)
    grades = await outcomes.waiting_by_version(recipe_id)
    live_pins = await pins.live_for_recipe(recipe_id)
    pin_of_version = {pin.recipe_version_id: pin for pin in live_pins}
    details = [
        RecipeVersionDetail(
            version=version,
            changes=version_changes(version, by_id.get(version.parent_version_id or 0), labels),
            shots=grouped[version.id],
            dead_end=version.id in dead_ends,
            labels=counts.get(version.id, VersionLabelCounts()),
            chat_thread_id=threads.get(version.id),
            outcome_proposal=grades.get(version.id),
            pinned=(
                VersionPinned(
                    pin_id=pin_of_version[version.id].id,
                    shot_count=pin_of_version[version.id].shot_count,
                )
                if version.id in pin_of_version
                else None
            ),
            evidence=(
                version_evidence(
                    counted,
                    spreads,
                    version_id=version.id,
                    version_label=version.version_label,
                    compares_to_version_id=version.compares_to_version_id,
                    compares_to_version_label=version.compares_to_version_label,
                )
                if version.prediction
                else None
            ),
        )
        for version in versions
    ]
    verdicts = await judgements.for_shots([shot.id for shot in page.items])
    waiting = await proposals.waiting(recipe_id)
    return envelope_response(
        RecipeDetailData(
            recipe=row,
            versions=details,
            judgements={str(shot_id): verdict for shot_id, verdict in verdicts.items()},
            track_record=track_record(versions),
            spread=spread_report(spreads),
            rollback_target_version_id=await recipes.rollback_target(recipe_id),
            reverts=await recipes.reverts(recipe_id),
            proposal=(await _proposal_detail(proposals, waiting) if waiting is not None else None),
            outcome_proposal=grades.get(row.current_version_id or 0),
            pins=[
                RecipePin(
                    pin_id=pin.id,
                    recipe_version_id=pin.recipe_version_id,
                    version_label=pin.version_label,
                    name=pin.name,
                    board_id=pin.board_id,
                    pinned_at=pin.pinned_at,
                    shot_count=pin.shot_count,
                )
                for pin in live_pins
            ],
        ).model_dump(mode="json")
    )


@router.post(
    "/{recipe_id}/versions",
    response_model=ApiResponse[RecipeVersionRow],
    status_code=201,
    summary="Change something: a new version, with its parent and its intent",
)
async def add_version(
    recipe_id: int, body: RecipeVersionAdd, recipes: RecipesRepoDep
) -> JSONResponse:
    """Append a version made of the current one plus the fields that were sent.

    Omitting a field inherits it; sending it as `null` clears it. That
    distinction is the reason the body is read with ``exclude_unset`` rather
    than compared against defaults — "no dose" and "same dose as before" are
    different statements about the coffee.

    ``major`` names it: true is the next major, false the next minor, left out
    a minor. The answer carries the name it was given (`version_label`).
    """
    # The Recipe first: a comparison check against a Recipe that is not there would
    # answer "that is not a version of this Recipe", which is true and useless
    # when the Recipe itself is the thing that does not exist.
    found = await recipes.get(recipe_id)
    if found is None:
        raise NotFound(f"No Recipe {recipe_id}")
    if found.designing and body.profile_version_id is None:
        raise Unprocessable(
            "A Recipe needs a profile: pick one that has no Recipe, or design one with the agent.",
            details={"field": "profile_version_id", "message": "a profile is required"},
        )
    if body.profile_version_id is not None and found.board_id is not None:
        raise Unprocessable(
            f"{found.name} brews {found.profile_label}: a new version follows its profile, and "
            "a different profile means a new Recipe (Duplicate this one).",
            details={"field": "profile_version_id", "message": "this Recipe already has a profile"},
        )
    if body.profile_version_id is not None:
        try:
            await recipes.profile_to_start_on(body.profile_version_id)
        except VersionRefused as exc:
            raise version_refused(exc) from None
    # The one reference on this body that is not a recipe field. Checked here
    # because it is the only place it can arrive: `add_version` inherits and
    # appends, and a comparison against a version of somebody else's Recipe is a
    # bad request rather than something the append should quietly drop. Every
    # candidate is older by construction — the new version is the highest there
    # is — so only the "same Recipe" half of the rule applies here.
    compare = body.compares_to_version_id
    if body.prediction and compare is not None:
        if await recipes.version_of_recipe(recipe_id, compare) is None:
            raise _refusal_bad_compare(recipe_id, None)
    # The recipe half only: `major` is how to name the version, not part of it,
    # and the fields actually sent are kept as sent (inherit versus clear).
    patch = RecipeVersionPatch.model_validate(
        body.model_dump(exclude_unset=True, exclude={"major"})
    )
    try:
        version = await recipes.add_version(recipe_id, patch, major=body.major)
    except VersionRefused as exc:
        raise version_refused(exc) from None
    if version is None:  # pragma: no cover - the Recipe was checked above
        raise NotFound(f"No Recipe {recipe_id}")
    return envelope_response(version.model_dump(mode="json"), status_code=201)


@router.patch(
    "/{recipe_id}/versions/{version_id}/prediction",
    response_model=ApiResponse[RecipeVersionRow],
    summary="Say what this version is expected to do differently",
)
async def set_prediction(
    recipe_id: int, version_id: int, body: VersionPredictionWrite, recipes: RecipesRepoDep
) -> JSONResponse:
    """Writable only while the version has no shots.

    An empty `prediction` takes the prediction back, comparison and all — which
    is the only way to remove one, and still only before the first shot.
    """
    return _unwrap(await recipes.set_prediction(recipe_id, version_id, body), recipe_id, version_id)


@router.put(
    "/{recipe_id}/versions/{version_id}/outcome",
    response_model=ApiResponse[RecipeVersionRow],
    summary="Grade this version's prediction",
)
async def set_outcome(
    recipe_id: int, version_id: int, body: VersionOutcomeWrite, recipes: RecipesRepoDep
) -> JSONResponse:
    """Held, partly held, failed or inconclusive, with the why beside it.

    Recordable only once the version has a prediction and a rated shot (a
    discarded shot does not count); changeable afterwards as often as you like.
    """
    return _unwrap(await recipes.set_outcome(recipe_id, version_id, body), recipe_id, version_id)


@router.delete(
    "/{recipe_id}/versions/{version_id}/outcome",
    response_model=ApiResponse[RecipeVersionRow],
    summary="Take back the grade on this version's prediction",
)
async def clear_outcome(recipe_id: int, version_id: int, recipes: RecipesRepoDep) -> JSONResponse:
    return _unwrap(await recipes.clear_outcome(recipe_id, version_id), recipe_id, version_id)


@router.get(
    "/{recipe_id}/proposals",
    response_model=ApiResponse[RecipeProposalListData],
    summary="Every change an agent has proposed for this Recipe",
)
async def list_proposals(
    recipe_id: int, recipes: RecipesRepoDep, proposals: RecipeProposalsRepoDep
) -> JSONResponse:
    """Newest first, waiting and answered alike.

    The answered ones are the point as much as the waiting one: a declined
    proposal is a record of what the person did not want, and a stale one says
    the Recipe moved on while somebody was thinking about it.
    """
    if await recipes.get(recipe_id) is None:
        raise NotFound(f"No Recipe {recipe_id}")
    rows = await proposals.for_recipe(recipe_id)
    items = [await _proposal_detail(proposals, row) for row in rows]
    return envelope_response(RecipeProposalListData(items=items).model_dump(mode="json"))


@router.post(
    "/{recipe_id}/proposals/{proposal_id}/accept",
    response_model=ApiResponse[RecipeProposalDecision],
    summary="Accept a proposed change: record it as this Recipe's next version",
)
async def accept_proposal(
    recipe_id: int,
    proposal_id: int,
    proposals: RecipeProposalsRepoDep,
    board: BoardServiceDep,
    body: ProposalAccept | None = None,
) -> JSONResponse:
    """A person's press, and the only way a proposal becomes a version.

    There is no tool for this, in the chat or over MCP, and there never will
    be: an agent that could accept its own proposal would be an agent changing
    what the next shot is filed under without anybody agreeing to it.

    **Nothing is sent to the machine.** A proposal that names a different
    profile records that this Recipe now brews with that profile, exactly as the
    Add a version form does. A first recipe is accepted **together with its
    profile**, in the same transaction: while its draft is still waiting it is
    put on the profile list (no Recipe recording, since version 1 names it), the
    profile is switched on, and version 1 names the version that put ended on.
    A draft that continues an existing profile becomes that profile's active
    version, exactly as approving it would. A draft that was already answered
    (approved or pushed on the Profiles page, a version made active since) is
    left exactly as it is, and version 1 names its version; a name sent for it
    is refused. Only a sync a person starts puts the file on the machine.

    The body is optional. ``major`` is the card's "Major change" box; left
    out, it is a minor, never the agent's suggestion.
    """
    major = body.major if body is not None else None
    label = body.profile_label if body is not None else None
    put_profile: PutProfile | None = None
    found = await proposals.get(recipe_id, proposal_id)
    if found is not None and found.kind != "design" and label is not None:
        raise Unprocessable("A profile name goes only with a first version")
    if found is not None and found.kind == "design":

        async def put_in_accept(draft_id: int) -> BoardRow | None:
            # Inside accept's transaction: the put does not open one of its own.
            return await board.settle_for_accept(draft_id, label=label)

        put_profile = put_in_accept

    try:
        accepted = await proposals.accept(
            recipe_id, proposal_id, major=major, put_profile=put_profile
        )
    except VersionRefused as exc:
        raise version_refused(exc) from None
    return await _decided(proposals, accepted, recipe_id, proposal_id)


@router.post(
    "/{recipe_id}/proposals/{proposal_id}/decline",
    response_model=ApiResponse[RecipeProposalDecision],
    summary="Turn a proposed change down, optionally saying why",
)
async def decline_proposal(
    recipe_id: int, proposal_id: int, body: ProposalDecline, proposals: RecipeProposalsRepoDep
) -> JSONResponse:
    """Nothing is created. The note is what the next conversation is told."""
    return await _decided(
        proposals,
        await proposals.decline(recipe_id, proposal_id, body.note),
        recipe_id,
        proposal_id,
    )


class OutcomeProposalListData(BaseModel):
    """`GET /api/recipes/{id}/outcome-proposals`: every grade proposed on this Recipe, newest
    first."""

    model_config = ConfigDict(extra="forbid")

    items: list[OutcomeProposalRow]


class OutcomeProposalDecision(BaseModel):
    """What answering a proposed grade produced: the proposal as it now stands, and the version."""

    model_config = ConfigDict(extra="forbid")

    proposal: OutcomeProposalRow
    #: The version carrying the outcome that was recorded. NULL after a dismissal.
    version: RecipeVersionRow | None = None


class OutcomeChange(BaseModel):
    """`POST .../change`: the outcome the person records instead, and optionally their own note."""

    model_config = ConfigDict(extra="forbid")

    outcome: VersionOutcome
    #: Left out, the agent's per-claim lines stay with the recorded outcome.
    note: LongText | None = None


class OutcomeDismiss(BaseModel):
    """`POST .../dismiss`: the turn-down, and optionally why."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=DECLINE_NOTE_MAX)


def _outcome_error(
    refusal: OutcomeProposalRefusal, recipe_id: int, proposal_id: int, version: str | None
) -> AppError:
    """A refused answer to a proposed grade as the one error it means.

    `details` names the field at fault and never repeats what was sent: the
    note is the agent's reasoning and the person's own words. ``version`` is the
    graded version's name, so the sentence says which one.
    """
    named = version or "The version"
    if refusal == "not_waiting":
        return Conflict(
            f"Outcome proposal {proposal_id} has already been answered",
            code="OUTCOME_PROPOSAL_DECIDED",
            details={"field": "status", "message": "it is no longer waiting for an answer"},
        )
    if refusal == "nothing_to_grade":
        return Conflict(
            f"{named} has no rated shot yet",
            code="NOTHING_TO_GRADE",
            details={
                "field": "outcome",
                "message": (
                    "no shot of the version is rated yet (a discarded shot does not count): "
                    "rate a shot before grading the prediction"
                ),
            },
        )
    if refusal == "no_prediction":
        return Unprocessable(
            f"{named} states no prediction",
            details={"field": "outcome", "message": "there is nothing to grade"},
        )
    return NotFound(f"No outcome proposal {proposal_id} in Recipe {recipe_id}")


def _outcome_decided(result: OutcomeResult, recipe_id: int, proposal_id: int) -> JSONResponse:
    if result.proposal is None or result.refused is not None:
        raise _outcome_error(
            result.refused or "no_proposal",
            recipe_id,
            proposal_id,
            result.proposal.version_label if result.proposal else None,
        )
    return envelope_response(
        OutcomeProposalDecision(proposal=result.proposal, version=result.version).model_dump(
            mode="json"
        )
    )


@router.get(
    "/{recipe_id}/outcome-proposals",
    response_model=ApiResponse[OutcomeProposalListData],
    summary="Every grade an agent has proposed for this Recipe's versions",
)
async def list_outcome_proposals(
    recipe_id: int, recipes: RecipesRepoDep, outcomes: OutcomeProposalsRepoDep
) -> JSONResponse:
    """Newest first, waiting and answered alike, so a card read days later tells the truth."""
    if await recipes.get(recipe_id) is None:
        raise NotFound(f"No Recipe {recipe_id}")
    return envelope_response(
        OutcomeProposalListData(items=await outcomes.for_recipe(recipe_id)).model_dump(mode="json")
    )


@router.post(
    "/{recipe_id}/outcome-proposals/{proposal_id}/accept",
    response_model=ApiResponse[OutcomeProposalDecision],
    summary="Accept a proposed grade: record it as the version's outcome",
)
async def accept_outcome_proposal(
    recipe_id: int, proposal_id: int, outcomes: OutcomeProposalsRepoDep
) -> JSONResponse:
    """A person's press, and the only way an agent's grade becomes an outcome.

    There is no tool for this, in the chat or over MCP: an agent that could
    accept its own grade would be writing the track record every later
    conversation reads.
    """
    return _outcome_decided(await outcomes.accept(recipe_id, proposal_id), recipe_id, proposal_id)


@router.post(
    "/{recipe_id}/outcome-proposals/{proposal_id}/change",
    response_model=ApiResponse[OutcomeProposalDecision],
    summary="Record another outcome than the one proposed",
)
async def change_outcome_proposal(
    recipe_id: int, proposal_id: int, body: OutcomeChange, outcomes: OutcomeProposalsRepoDep
) -> JSONResponse:
    return _outcome_decided(
        await outcomes.change(recipe_id, proposal_id, body.outcome, body.note),
        recipe_id,
        proposal_id,
    )


@router.post(
    "/{recipe_id}/outcome-proposals/{proposal_id}/dismiss",
    response_model=ApiResponse[OutcomeProposalDecision],
    summary="Turn a proposed grade down, optionally saying why",
)
async def dismiss_outcome_proposal(
    recipe_id: int, proposal_id: int, body: OutcomeDismiss, outcomes: OutcomeProposalsRepoDep
) -> JSONResponse:
    """Nothing is recorded. The note is what that version's conversation is told."""
    return _outcome_decided(
        await outcomes.dismiss(recipe_id, proposal_id, body.note), recipe_id, proposal_id
    )


@router.post(
    "/{recipe_id}/rollback",
    response_model=ApiResponse[RecipeVersionRow],
    summary="Go back to another version: the Recipe is on it again, and no version is written",
)
async def rollback(
    recipe_id: int, body: RollbackWrite, recipes: RecipesRepoDep, board: BoardServiceDep
) -> JSONResponse:
    """Move the Recipe's current version to `to_version_id` and log that it went back.

    Nothing is written to the machine and no version is created: the target is
    the Recipe's current version again, with its prediction, shots and outcome as
    they were, and the next change continues its line (v2.1 back to v1.2, then
    v1.3). The Recipe's profile is put back on the version the target brews, as Make active
    would do (and refused as it would be). The answer is the version the Recipe is now on. A Recipe
    still being designed has nothing to go back to: 409 `DESIGNING`.
    """
    if await recipes.get(recipe_id) is None:
        raise NotFound(f"No Recipe {recipe_id}")
    return _unwrap(await board.revert_recipe(recipe_id, body), recipe_id, body.to_version_id)


@router.put(
    "/{recipe_id}/automatch",
    response_model=ApiResponse[RecipeRow],
    summary="Offer this Recipe to the matcher, or take it out of the running",
)
async def set_automatch(
    recipe_id: int, body: AutomatchWrite, recipes: RecipesRepoDep
) -> JSONResponse:
    """Any number of Recipes may be offered. Nothing is taken from another Recipe.

    One route both ways rather than two: this is one flag with two values, and
    the page that reads it as a switch would otherwise have to know two paths to
    write it.

    Turning it **on** for an archived Recipe is a 409 rather than a silent no-op:
    an archived Recipe receives no shots whatever the flag says, so the page would
    show a badge promising something that will never happen. Turning it off is
    allowed on any Recipe — archiving does that itself.
    """
    existing = await recipes.get(recipe_id)
    if existing is None:
        raise NotFound(f"No Recipe {recipe_id}")
    if body.automatch and existing.archived:
        raise Conflict(
            f"Recipe {recipe_id} is archived",
            details={
                "field": "archived",
                "message": "un-archive it before the matcher can file shots under it",
            },
        )
    row = await recipes.set_automatch(recipe_id, body.automatch)
    if row is None:  # pragma: no cover - checked above, inside the same request
        raise NotFound(f"No Recipe {recipe_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.post(
    "/{recipe_id}/archive", response_model=ApiResponse[RecipeRow], summary="Retire a Recipe"
)
async def archive_recipe(recipe_id: int, recipes: RecipesRepoDep) -> JSONResponse:
    row = await recipes.archive(recipe_id)
    if row is None:
        raise NotFound(f"No Recipe {recipe_id}")
    return envelope_response(row.model_dump(mode="json"))


@router.get(
    "/{recipe_id}/trends",
    response_model=ApiResponse[RecipeTrends],
    summary="Duration, ratio and rating across the Recipe's versions",
)
async def get_trends(recipe_id: int, recipes: RecipesRepoDep) -> JSONResponse:
    """The chart's data: one point per shot, one summary per version.

    Both from one pass over the Recipe's shots, so a bar can never sit off its own
    points because two queries rounded differently.
    """
    row = await recipes.get(recipe_id)
    if row is None:
        raise NotFound(f"No Recipe {recipe_id}")
    return envelope_response((await recipes.trends(recipe_id)).model_dump(mode="json"))


# ── insights the agent proposed deleting ─────────────────────────────


class InsightDeletionListData(BaseModel):
    """`GET /api/recipes/{id}/insight-deletions`: every deletion proposed on this Recipe, newest
    first."""

    model_config = ConfigDict(extra="forbid")

    items: list[InsightDeletionRow]


class InsightDeletionDecision(BaseModel):
    """What answering a proposed deletion produced: the proposal as it now stands."""

    model_config = ConfigDict(extra="forbid")

    proposal: InsightDeletionRow


def _deletion_error(refusal: InsightDeletionRefusal, recipe_id: int, proposal_id: int) -> AppError:
    """A refused answer to a proposed deletion as the one error it means.

    `details` names the field at fault and never repeats the insight or the reason.
    """
    if refusal == "not_waiting":
        return Conflict(
            f"Insight deletion {proposal_id} has already been answered",
            code="INSIGHT_DELETION_DECIDED",
            details={"field": "status", "message": "it is no longer waiting for an answer"},
        )
    if refusal == "insight_changed":
        return Conflict(
            f"The insight behind deletion {proposal_id} is no longer added",
            code="INSIGHT_NOT_ADDED",
            details={
                "field": "confirmed",
                "message": "it was taken back since, so there is nothing left to delete",
            },
        )
    return NotFound(f"No insight deletion {proposal_id} in Recipe {recipe_id}")


def _deletion_decided(result: DeletionResult, recipe_id: int, proposal_id: int) -> JSONResponse:
    if result.proposal is None or result.refused is not None:
        raise _deletion_error(result.refused or "no_proposal", recipe_id, proposal_id)
    return envelope_response(
        InsightDeletionDecision(proposal=result.proposal).model_dump(mode="json")
    )


@router.get(
    "/{recipe_id}/insight-deletions",
    response_model=ApiResponse[InsightDeletionListData],
    summary="Every deletion an agent has proposed for this Recipe's insights",
)
async def list_insight_deletions(
    recipe_id: int,
    recipes: RecipesRepoDep,
    deletions: InsightDeletionsRepoDep,
    thread_id: Annotated[
        int | None,
        Query(
            description=(
                "Only what this conversation proposed, with no cap: the chat's cards read "
                "their proposal this way, so one is found however old it is."
            )
        ),
    ] = None,
) -> JSONResponse:
    """Newest first, waiting and answered alike, so a card read days later tells the truth."""
    if await recipes.get(recipe_id) is None:
        raise NotFound(f"No Recipe {recipe_id}")
    items = (
        await deletions.for_recipe(recipe_id)
        if thread_id is None
        else [row for row in await deletions.for_thread(thread_id) if row.recipe_id == recipe_id]
    )
    return envelope_response(InsightDeletionListData(items=items).model_dump(mode="json"))


@router.post(
    "/{recipe_id}/insight-deletions/{proposal_id}/accept",
    response_model=ApiResponse[InsightDeletionDecision],
    summary="Delete the insight an agent proposed deleting",
)
async def accept_insight_deletion(
    recipe_id: int, proposal_id: int, deletions: InsightDeletionsRepoDep
) -> JSONResponse:
    """The card's Delete: a person's press, and the only way a proposal removes an insight.

    There is no tool for this, in the chat or over MCP: an agent that could delete
    its own predecessors' insights would be rewriting what every later conversation
    is told. The insight is removed outright; nothing of it is kept but the text on
    the card.
    """
    return _deletion_decided(await deletions.accept(recipe_id, proposal_id), recipe_id, proposal_id)


@router.post(
    "/{recipe_id}/insight-deletions/{proposal_id}/keep",
    response_model=ApiResponse[InsightDeletionDecision],
    summary="Keep the insight an agent proposed deleting",
)
async def keep_insight_deletion(
    recipe_id: int, proposal_id: int, deletions: InsightDeletionsRepoDep
) -> JSONResponse:
    """The card's Keep: the insight stays exactly as it is, and the agent is told."""
    return _deletion_decided(await deletions.keep(recipe_id, proposal_id), recipe_id, proposal_id)
