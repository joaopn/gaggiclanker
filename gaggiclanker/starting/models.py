"""The shape the starting-point call must answer in.

As with the analyzer (`gaggiclanker/analyzer/models.py`) this is the only
description of the contract: its JSON schema is what the provider is sent and
its validator is what the reply is checked against, so the prompt explains what
the fields are *for* and never restates their types.

Three options come back, not one. That is gaggimate-mcp's `new-coffee` skill
and it is the right shape for a
cold start: nobody knows what this bag wants yet, and a single confident answer
hides that. `conservative` is the one that will make drinkable coffee,
`recommended` is the one to actually pull, `adventurous` is the one worth a shot
if the first two are dull. The three keys are fixed and validated, because the
UI renders one card per key and a fourth option called "balanced" would be a
card nobody sees.

**A grind number is only offered when something anchors it.** A grinder's scale
is arbitrary — 22 on a Niche and 22 on a Mazzer are different grinds, and there
is no conversion — so `grind_setting` may be a number only when the user gave
their usual setting or a similar Set on the *same* grinder supplies one.
Otherwise the model answers in words ("two steps finer than your usual espresso
setting") and says why in `grind_note`. That distinction is `grind_is_absolute`,
and it exists because a made-up number on a dial is the single most expensive
kind of wrong answer here: somebody dials it and wastes a bag.

**The profile is either a pointer or a document, never both.** Naming an
existing `profile_version_id` is preferred — a profile that already brews well
on this machine beats a fresh one — and a whole `profile` document is the
fallback for when nothing in the library fits.

**And a document is checked here, at propose time, not only at accept time.**
It goes through the safety policy's own `clamp` then `check` — the order the
draft service uses, so a number the clamp would have fixed is not rejected as
if it were unfixable — plus one rule of this feature's own: the last brew phase
must carry a volumetric or pumped stop, because the prompt demands one and a
profile without it runs until its clock expires. Doing it here means a
non-compliant document costs the corrective turn the LLM layer already budgets
for and, failing that, a `failed` run; doing it only at accept time means an
`ok` run whose card cannot be taken, which is the worse of the two by a long
way — the user has paid for the call either way, and one of the two outcomes
tells them so.

The bounds used here are the **defaults**, because a validator cannot reach the
settings service. That is deliberately the looser check of the two: the accept
path re-runs the policy against the *configured* bounds through
`DraftsService.create_manual`, so a user who has narrowed them still gets the
refusal they asked for.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from gaggiclanker.domain.models import Phase, Profile
from gaggiclanker.domain.profile_policy import DEFAULT_BOUNDS, check, clamp

__all__ = [
    "OPTION_KEYS",
    "OptionKey",
    "StartingPointOption",
    "StartingPointResult",
]

#: The three options, in the order the cards are laid out. A literal rather
#: than free text so an invented fourth key is a validation failure and one
#: corrective turn, not a silently dropped card.
type OptionKey = Literal["conservative", "recommended", "adventurous"]

OPTION_KEYS: tuple[str, ...] = ("conservative", "recommended", "adventurous")


class StartingPointOption(BaseModel):
    """One complete first recipe: what to dial, and what to brew it with."""

    model_config = ConfigDict(extra="forbid")

    option: OptionKey
    #: One line for the card's header — "Safe: classic 1:2 at 93 °C".
    headline: str = Field(min_length=1, max_length=200)

    #: The grinder's own reading when one is anchored, or words when it is not.
    #: See the module docstring: this is the field that must never be invented.
    grind_setting: str = Field(min_length=1, max_length=100)
    #: True only when `grind_setting` is a number on this grinder's scale that
    #: the user's usual setting or a similar Set on the same grinder supports.
    grind_is_absolute: bool = False
    #: Why the grind is what it is, and what to change if the first shot runs
    #: long or short. Always filled in; it is the only actionable thing when
    #: the setting itself is relative.
    grind_note: str = Field(default="", max_length=500)

    dose_g: float = Field(gt=0, le=100)
    yield_g: float = Field(gt=0, le=500)
    #: Yield over dose. Asked for rather than computed so a mismatch between it
    #: and the two grams figures is visible — it means the model changed its
    #: mind halfway and one of the three numbers is stale.
    ratio: float = Field(gt=0, le=10)
    temperature_c: float = Field(ge=60, le=100)

    #: The profile from the library this option wants, when one fits. Validated
    #: against the versions the run was shown, the way `rules_used` is.
    profile_version_id: int | None = None
    #: A whole new profile, when nothing in the library fits. Exactly one of
    #: this and `profile_version_id` is expected; both empty means "keep
    #: whatever is selected on the machine", which is a legitimate answer for a
    #: conservative option.
    profile: Profile | None = None
    #: What the profile is for, in one line. Shown on the card above the diff.
    profile_note: str = Field(default="", max_length=500)

    #: Why this option, citing rule keys, similar Set versions and excerpt
    #: heading paths. The card renders it under the numbers.
    rationale: str = Field(min_length=1, max_length=2000)
    #: The rule keys leaned on. Filtered against the rules this run was given.
    rules_used: list[str] = Field(default_factory=list, max_length=40)
    #: The heading paths of the excerpts leaned on. Filtered the same way.
    excerpts_used: list[str] = Field(default_factory=list, max_length=20)
    #: The `set_version_id`s of the similar Sets this option is anchored on.
    #: Filtered against the ones the run was shown, because an anchor the model
    #: invented is worse than none: the card links to it.
    similar_set_version_ids: list[int] = Field(default_factory=list, max_length=10)

    @field_serializer("profile")
    def _serialise_profile(self, profile: Profile | None) -> dict[str, Any] | None:
        """Dump the profile in the shape `Profile` itself accepts back.

        `model_dump` would emit the `annotations` field, and `Profile`'s own
        validator refuses that key outright — so the stored output of a run
        could not be re-read, which is exactly what `accept` does days later.
        `to_device()` re-expands the `^_` keys and drops the field.
        """
        return None if profile is None else profile.to_device()

    @model_validator(mode="after")
    def _profile_is_writable(self) -> StartingPointOption:
        """A profile document that the policy would refuse is not an answer.

        `clamp` first and `check` after, which is the draft service's order and
        therefore the same verdict the accept path will reach: what survives a
        clamp is what a clamp cannot fix, and rejecting a 101 °C the clamp would
        have moved to 100 would cost a retry for nothing.
        """
        if self.profile is None:
            return self
        clamped, _ = clamp(self.profile, DEFAULT_BOUNDS)
        violations = check(clamped, DEFAULT_BOUNDS)
        if violations:
            raise ValueError(
                "that profile is not one the safety policy allows: "
                + "; ".join(f"{v.path}: {v.message}" for v in violations[:5])
            )
        if not _terminates(clamped.phases):
            raise ValueError(
                "the last brew phase needs a volumetric or pumped stop condition at about "
                "the yield; without one the shot runs until its duration expires and floods "
                "the cup"
            )
        return self

    @model_validator(mode="after")
    def _one_profile_form(self) -> StartingPointOption:
        """A pointer or a document, never both.

        Both set is not a richer answer, it is an ambiguous one: the accept
        path would have to pick, and whichever it picked would be the one the
        person did not see on the card.
        """
        if self.profile_version_id is not None and self.profile is not None:
            raise ValueError(
                "an option names either an existing profile_version_id or a whole "
                "profile document, not both"
            )
        return self


class StartingPointResult(BaseModel):
    """The whole answer: what the bean needs, and three ways to start."""

    model_config = ConfigDict(extra="forbid")

    #: Two or three sentences on what this coffee is and what to expect from it.
    summary: str = Field(min_length=1, max_length=2000)
    #: What would make the next suggestion better — almost always "tell me your
    #: usual grind setting". Rendered as a list under the cards.
    questions_for_user: list[str] = Field(default_factory=list, max_length=5)
    #: Exactly three, one per key. A list rather than three named fields so the
    #: UI can iterate it and the accept path can look one up by name; the keys
    #: are checked below, which is what makes that lookup total.
    options: list[StartingPointOption] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _check_option_keys(self) -> StartingPointResult:
        """One option per key, no duplicates, nothing missing.

        A `min_length=3` alone would accept three `recommended` options, which
        renders as one card and loses two thirds of the answer. Rejecting it
        here costs the corrective turn the LLM layer already budgets for, and
        the message names exactly what is wrong — which is the kind of error a
        model fixes on the first retry.
        """
        seen = [item.option for item in self.options]
        if sorted(seen) != sorted(OPTION_KEYS):
            raise ValueError(
                "options must be exactly one of each: "
                f"{', '.join(OPTION_KEYS)} — got {', '.join(seen) or '(none)'}"
            )
        return self

    def option(self, key: str) -> StartingPointOption | None:
        """The option with this key, or ``None``. What the accept path calls."""
        return next((item for item in self.options if item.option == key), None)


def _terminates(phases: list[Phase]) -> bool:
    """Whether the last brew phase ends on the cup rather than on the clock.

    The policy's own termination rule only fires for a phase past the duration
    ceiling, and `clamp` moves every duration inside that ceiling first — so a
    profile with no stop anywhere is, to the shared policy, merely a profile
    that stops on time. That is the right answer for a backflush and the wrong
    one here: this prompt asks for a stop near the yield, and an option without
    one is a card nobody can use. So the rule lives with the feature that needs
    it rather than being pushed into `profile_policy`, where it would start
    refusing utility profiles somebody wrote years ago.
    """
    brew = next((phase for phase in reversed(phases) if phase.phase == "brew"), None)
    if brew is None:
        brew = phases[-1] if phases else None
    if brew is None:  # pragma: no cover - `Profile` requires at least one phase
        return False
    return any(
        target.type in ("volumetric", "pumped") and target.value > 0 for target in brew.targets
    )
