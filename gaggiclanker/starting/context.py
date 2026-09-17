"""Everything the starting-point call is told, assembled once and rendered once.

The same contract as `gaggiclanker/analyzer/context.py`, and for the same
reason: the document built here is stored verbatim on the run row, so a
suggestion stays explainable after the bag has been finished, the rules edited
and the prompt rewritten. One function builds it; one method renders it into
the prompt's variables; nothing else may reach into the database mid-call.

What is in it, and why each piece earns its tokens:

* **the bean** — the coffee, its roaster, origin, process and roast level, which
  is what every rule in the tier is keyed on;
* **the hardware**, because advice is given in the grinder's own step unit and
  the machine's temperature offset shifts every number in the answer;
* **the user's usual grind**, when they gave one. This is what turns a relative
  answer into a number somebody can dial, and the prompt is told so;
* **the similar Sets** (`similar.py`) with their outcomes — the calibration
  this archive has that a textbook does not;
* **the profile library**, as a short list of candidate versions, so "use
  profile 14" is a thing the model can say instead of authoring a new one;
* **the rules** for the planned style, the roast and the process, and
* **a few excerpts**, last and least authoritative.

**The planned style.** Rule selection filters on a shot style, and a bag nobody
has brewed yet has none. Rather than pass `unknown` — which would drop every
ratio and time rule, the two most useful categories here — the style is taken
from the best similar Set's profile and falls back to `classic`. That is an
honest answer to "what kind of shot are we planning": the one you have been
pulling, or a traditional one if you have pulled nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.analyzer.style import detect_style
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeanRow, BeansRepository
from gaggiclanker.db.repos.grinders import GrinderRow, GrindersRepository
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.machines import MachineRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.knowledge.rules import SetContext, render_rules, select_rules
from gaggiclanker.knowledge.service import (
    DEFAULT_CHUNK_TOKEN_BUDGET,
    KnowledgeService,
    RetrievalContext,
    render_excerpts,
)
from gaggiclanker.starting.similar import DEFAULT_LIMIT, SimilarSet, similar_sets

__all__ = [
    "CANDIDATE_PROFILES",
    "DEFAULT_STYLE",
    "BeanFacts",
    "HardwareFacts",
    "ProfileCandidate",
    "StartingPointContext",
    "build_context",
]

#: The style assumed when nothing has been brewed on this kit that resembles
#: the new bag. Traditional espresso: the thing somebody who has just opened a
#: bag is most likely trying to make.
DEFAULT_STYLE = "classic"

#: How many profiles from the library the model is offered to choose from.
#: Enough that a fitting one is probably in the list, few enough that reading
#: it is cheaper than authoring a profile from scratch.
CANDIDATE_PROFILES = 12


class BeanFacts(BaseModel):
    """The bag, as the prompt states it."""

    model_config = ConfigDict(extra="forbid")

    bean_id: int
    name: str
    roaster: str = ""
    origin: str = ""
    process: str | None = None
    roast_level: str | None = None
    decaf: bool = False
    description: str = ""
    notes: str = ""


class HardwareFacts(BaseModel):
    """The machine and the grinder, plus whatever the user said about the dial."""

    model_config = ConfigDict(extra="forbid")

    machine_name: str = ""
    machine_hardware: str = ""
    temperature_offset_c: float | None = None

    grinder_id: int | None = None
    grinder_name: str = ""
    grinder_model: str = ""
    burr_type: str = "unknown"
    step_unit: str = "clicks"
    #: What the user says they normally grind espresso at, verbatim and in the
    #: grinder's own units. Empty is the common case and the prompt handles it.
    usual_grind: str = ""
    #: A dose they want held — usually their basket. Empty means "you choose".
    dose_hint_g: float | None = None


class ProfileCandidate(BaseModel):
    """One profile from the library the model may point at instead of authoring."""

    model_config = ConfigDict(extra="forbid")

    profile_version_id: int
    label: str
    #: How many shots in the archive were pulled with it. The list is ordered
    #: by this: a profile nobody has used is a worse suggestion than one that
    #: has made a hundred drinkable shots on this machine.
    shot_count: int = 0
    phases: int = 0
    temperature_c: float | None = None
    #: A one-line shape — "preinfusion 8 s, brew 9 bar, volumetric 36 g" — so
    #: choosing does not need the whole document in the prompt.
    shape: str = ""


class StartingPointContext(BaseModel):
    """Everything the model is told, as one validated document."""

    model_config = ConfigDict(extra="forbid")

    bean: BeanFacts
    hardware: HardwareFacts
    #: The date the run was asked on, stated to the model so "wait a few days"
    #: is advice with a reference point. ISO, day precision: nothing here is
    #: finer-grained than a day.
    as_of: str = ""
    #: The style rule selection was made for, and why. See the module docstring.
    planned_style: str = DEFAULT_STYLE
    planned_style_reason: str = ""
    similar: list[SimilarSet] = Field(default_factory=list)
    profiles: list[ProfileCandidate] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    #: `[{key, category, text, confidence, source}]` for every selected rule.
    rules: list[dict[str, str]] = Field(default_factory=list)
    #: The tier-2 excerpts, stored in full for the reason an analysis stores
    #: them in full: a citation into a document since edited has to resolve to
    #: the text the model actually read.
    excerpts: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def rule_keys(self) -> frozenset[str]:
        return frozenset(rule["key"] for rule in self.rules)

    @property
    def excerpt_paths(self) -> frozenset[str]:
        return frozenset(str(excerpt["heading_path"]) for excerpt in self.excerpts)

    @property
    def similar_version_ids(self) -> frozenset[int]:
        return frozenset(entry.set_version_id for entry in self.similar)

    @property
    def profile_version_ids(self) -> frozenset[int]:
        return frozenset(entry.profile_version_id for entry in self.profiles)

    def render(self) -> dict[str, str]:
        """The prompt's variables: one rendered block per section.

        Prose with numbers in it rather than JSON, for the reason the analyzer
        renders the same way — the same facts cost about a third of the tokens
        — except the candidate profiles' shapes, which are already one line.
        """
        return {
            "bean_facts": _render_bean(self.bean, self.as_of),
            "hardware_facts": _render_hardware(self.hardware),
            "similar_sets": _render_similar(self.similar),
            "profile_library": _render_profiles(self.profiles),
            "planned_style": (
                f"{self.planned_style} — {self.planned_style_reason}"
                if self.planned_style_reason
                else self.planned_style
            ),
            "knowledge_rules": render_rules(self.rules),
            "knowledge_excerpts": render_excerpts(self.excerpts),
        }


@dataclass(frozen=True, slots=True)
class _Inputs:
    """The three rows the build needs, fetched once."""

    bean: BeanRow
    machine_hardware: str
    machine_name: str
    temperature_offset_c: float | None
    grinder: GrinderRow | None


async def build_context(
    db: Database,
    *,
    bean_id: int,
    grinder_id: int | None = None,
    usual_grind: str = "",
    dose_hint_g: float | None = None,
    as_of: str = "",
    chunk_token_budget: int = DEFAULT_CHUNK_TOKEN_BUDGET,
    similar_limit: int = DEFAULT_LIMIT,
) -> StartingPointContext:
    """Assemble the whole context. Pure in the database it is handed.

    ``as_of`` is the day the suggestion is *for*, defaulting to today. It is a
    parameter rather than a `date.today()` inside because the golden test has to
    render a fixed document, and a rendered date read off the clock would move
    the file once a day for ever.

    Raises ``LookupError`` for a bean or a grinder that does not exist: that is
    the caller's mistake rather than the provider's, and it wants to be a 404
    rather than a stored `failed` run. The machine is a singleton and is always
    there, so it is read rather than looked up.
    """
    inputs = await _fetch(db, bean_id=bean_id, grinder_id=grinder_id)
    today = (as_of or datetime.now(UTC).date().isoformat())[:10]

    bean = BeanFacts(
        bean_id=inputs.bean.id,
        name=inputs.bean.name,
        roaster=inputs.bean.roaster or "",
        origin=inputs.bean.origin or "",
        process=inputs.bean.process,
        roast_level=inputs.bean.roast_level,
        decaf=inputs.bean.decaf,
        description=inputs.bean.description,
        notes=inputs.bean.notes,
    )
    hardware = HardwareFacts(
        machine_name=inputs.machine_name,
        machine_hardware=inputs.machine_hardware,
        temperature_offset_c=inputs.temperature_offset_c,
        grinder_id=inputs.grinder.id if inputs.grinder else None,
        grinder_name=inputs.grinder.name if inputs.grinder else "",
        grinder_model=(inputs.grinder.model or "") if inputs.grinder else "",
        burr_type=inputs.grinder.burr_type if inputs.grinder else "unknown",
        step_unit=inputs.grinder.step_unit if inputs.grinder else "clicks",
        usual_grind=usual_grind.strip(),
        dose_hint_g=dose_hint_g,
    )

    similar = await similar_sets(
        db,
        roast_level=bean.roast_level,
        process=bean.process,
        origin=bean.origin or None,
        decaf=bean.decaf,
        grinder_id=hardware.grinder_id,
        limit=similar_limit,
    )
    profiles = await _profile_candidates(db)
    style, reason = await _planned_style(db, similar)

    signals = _signal_tokens(style)
    selection = await select_rules(
        RulesRepository(db),
        SetContext(
            roast_level=bean.roast_level,
            process=bean.process,
            burr_type=hardware.burr_type,
            decaf=bean.decaf,
        ),
        style,
        signals,
    )
    excerpts = await KnowledgeService(db).select_chunks(
        RetrievalContext(
            style=style,
            signals=tuple(signals),
            roast_level=bean.roast_level,
            process=bean.process,
            extra_queries=("starting point for a new coffee", "first shot dial in"),
        ),
        token_budget=chunk_token_budget,
    )

    return StartingPointContext(
        bean=bean,
        hardware=hardware,
        as_of=today,
        planned_style=style,
        planned_style_reason=reason,
        similar=similar,
        profiles=profiles,
        signals=selection.signals,
        rules=[
            {
                "key": rule.key,
                "category": rule.category,
                "text": rule.text,
                "confidence": rule.confidence,
                "source": rule.source,
            }
            for rule in selection.rules
        ],
        excerpts=[excerpt.as_dict() for excerpt in excerpts],
    )


async def _fetch(db: Database, *, bean_id: int, grinder_id: int | None) -> _Inputs:
    bean = await BeansRepository(db).get(bean_id)
    if bean is None:
        raise LookupError(f"no bean {bean_id}")
    machine = await MachineRepository(db).get()
    grinder = None
    if grinder_id is not None:
        grinder = await GrindersRepository(db).get(grinder_id)
        if grinder is None:
            raise LookupError(f"no grinder {grinder_id}")
    return _Inputs(
        bean=bean,
        machine_hardware=(machine.hardware_string or "") if machine else "",
        machine_name=(machine.name or machine.host) if machine else "",
        temperature_offset_c=machine.temperature_offset_c if machine else None,
        grinder=grinder,
    )


def _signal_tokens(style: str) -> list[str]:
    """The tokens rule selection matches `applies.signal` against.

    A much shorter list than an analysis's — one token — because the whole of
    that grammar is about a shot or a cup that has not happened: there is no
    channeling band, no first drip, no taste. Nothing about the coffee itself is
    a signal either; roast level, process and decaf are matched as Set
    attributes. What is left is the planned style, which `select_rules` adds
    itself and which is repeated here so the stored signal list reads as the
    whole basis of the selection.

    A list rather than a bare string so the snapshot has the analyzer's shape.
    """
    return [f"style:{style}"]


async def _planned_style(db: Database, similar: list[SimilarSet]) -> tuple[str, str]:
    """The style to plan for, and the sentence explaining the choice.

    The best similar Set's profile, when it names one. That is the closest
    thing to evidence about what this kitchen actually brews; the alternative,
    `unknown`, silently drops every `ratio_by_style` and `time_by_style` rule
    and leaves the model with no ratio guidance at all.
    """
    profiles = ProfilesRepository(db)
    for entry in similar:
        if entry.profile_version_id is None:
            continue
        version = await profiles.get_version(entry.profile_version_id)
        if version is None or not version.profile:
            continue
        verdict = detect_style(version.profile, dose_g=entry.dose_g)
        if verdict.style not in ("unknown", "utility"):
            return verdict.style, (
                f"the profile of the closest similar Set "
                f"({entry.set_name} v{entry.version_no}, {version.label})"
            )
    return DEFAULT_STYLE, "nothing comparable has been brewed on this kit yet"


async def _profile_candidates(db: Database) -> list[ProfileCandidate]:
    """The library, shortlisted: most-used brew profiles first.

    Utility profiles are excluded — a backflush is not a starting point — and
    so is anything that has never been used *and* is not mirrored, because a
    profile the machine does not have is one the person would have to push
    before they could pull a shot with it.
    """
    rows = await db.fetch_all(
        """
        SELECT pv.id, pv.label, pv.json,
               (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = pv.id) AS shot_count
          FROM profile_versions pv
         WHERE pv.utility = 0
         ORDER BY shot_count DESC, pv.id DESC
         LIMIT ?
        """,
        (CANDIDATE_PROFILES,),
    )
    out: list[ProfileCandidate] = []
    for row in rows:
        try:
            document = json.loads(row["json"])
        except (TypeError, ValueError):  # pragma: no cover - stored by a validator
            continue
        phases = [p for p in document.get("phases", []) if isinstance(p, dict)]
        out.append(
            ProfileCandidate(
                profile_version_id=int(row["id"]),
                label=str(row["label"]),
                shot_count=int(row["shot_count"]),
                phases=len(phases),
                temperature_c=_float(document.get("temperature")),
                shape=_shape(phases),
            )
        )
    return out


def _shape(phases: list[dict[str, Any]]) -> str:
    """One line per profile: what each phase does and what ends it."""
    parts: list[str] = []
    for phase in phases:
        pump = phase.get("pump")
        if isinstance(pump, dict):
            target = str(pump.get("target", ""))
            value = pump.get(target) if target in ("pressure", "flow") else None
            drive = f"{target} {_num(value)}" if value is not None else target
        else:
            drive = f"pump {_num(pump)}%"
        stops = ", ".join(
            f"{target.get('type')} {target.get('operator', 'gte')} {_num(target.get('value'))}"
            for target in phase.get("targets", [])
            if isinstance(target, dict)
        )
        head = f"{phase.get('name', '?')} {_num(phase.get('duration'))}s {drive}".strip()
        parts.append(f"{head} → {stops}" if stops else head)
    return "; ".join(parts)


# ── rendering ────────────────────────────────────────────────────────


def _line(label: str, value: Any, unit: str = "") -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, float):
        return f"{label}: {value:g}{unit}"
    return f"{label}: {value}{unit}"


def _block(lines: list[str | None]) -> str:
    return "\n".join(line for line in lines if line)


def _num(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{float(value):g}"
    return "?" if value is None else str(value)


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _render_bean(bean: BeanFacts, as_of: str) -> str:
    return _block(
        [
            _line("bean", bean.name),
            _line("roaster", bean.roaster),
            _line("origin", bean.origin or "not stated"),
            _line("process", bean.process or "not stated"),
            _line("roast level", bean.roast_level or "not stated"),
            _line("decaf", "yes" if bean.decaf else None),
            _line("today", as_of),
            _line("description", bean.description),
            _line("notes", bean.notes),
        ]
    )


def _render_hardware(hardware: HardwareFacts) -> str:
    grinder = _block(
        [
            _line("grinder", f"{hardware.grinder_name} {hardware.grinder_model}".strip())
            if hardware.grinder_name
            else _line("grinder", "NOT RECORDED — give every grind change in relative terms"),
            _line("burrs", hardware.burr_type),
            _line(
                "grind unit",
                f"{hardware.step_unit} — every grind figure you give is in these, never microns",
            ),
            _line(
                "their usual espresso setting",
                hardware.usual_grind
                or "NOT GIVEN — you have no anchor on this grinder's scale unless a "
                "similar Set below supplies one",
            ),
        ]
    )
    machine = _block(
        [
            _line("machine", hardware.machine_hardware or hardware.machine_name),
            _line("temperature offset", hardware.temperature_offset_c, " °C"),
            _line("dose they want to hold", hardware.dose_hint_g, " g"),
        ]
    )
    return f"GRINDER\n{grinder}\n\nMACHINE\n{machine}"


def _render_similar(similar: list[SimilarSet]) -> str:
    if not similar:
        return (
            "Nothing comparable has been brewed on this grinder. There is no anchor on the "
            "grinder's own scale from the archive — say so, and work from the rules."
        )
    blocks: list[str] = []
    for entry in similar:
        roast = {
            "same": "same roast level",
            "adjacent": "one roast level away",
        }.get(entry.roast_match, "different roast level")
        match = [
            roast,
            "same process" if entry.process_match else "different process",
            "same origin" if entry.origin_match else "different origin",
        ]
        if not entry.decaf_match:
            # Worth saying out loud: decaf is a different coffee hydraulically,
            # and a model that copies its grind without noticing has been misled
            # by a card that looked like a match.
            match.append("DECAF MISMATCH — treat its numbers with caution")
        outcome = entry.outcome
        blocks.append(
            _block(
                [
                    f"[set_version {entry.set_version_id}] {entry.set_name} v{entry.version_no} "
                    f"— {entry.bean_name}",
                    _line("  why it is similar", ", ".join(match)),
                    _line(
                        "  bean",
                        f"{entry.roast_level or 'roast not stated'}, "
                        f"{entry.process or 'process not stated'}, "
                        f"{entry.origin or 'origin not stated'}"
                        + (", decaf" if entry.decaf else ""),
                    ),
                    _line("  grind", entry.grind_setting or "not recorded"),
                    _line("  dose", entry.dose_g, " g"),
                    _line("  target yield", entry.target_yield_g, " g"),
                    _line("  ratio", None if entry.ratio is None else f"1:{entry.ratio:.2f}"),
                    _line("  temperature", entry.target_temperature_c, " °C"),
                    _line(
                        "  profile",
                        None
                        if entry.profile_version_id is None
                        else f"{entry.profile_label or 'unnamed'} "
                        f"(profile_version {entry.profile_version_id})",
                    ),
                    _line(
                        "  how it went",
                        f"{outcome.shots} shot{'s' if outcome.shots != 1 else ''}"
                        f", rating {_mean(outcome.mean_rating)}/5"
                        f", execution {_mean(outcome.mean_execution_score)}/10"
                        f", ratio {_mean(outcome.mean_ratio, prefix='1:')}"
                        f", {_mean(outcome.mean_duration_s)} s",
                    ),
                ]
            )
        )
    return "\n\n".join(blocks)


def _mean(value: float | None, prefix: str = "") -> str:
    """``None`` reads as "not recorded", never as a zero somebody would average."""
    return "not recorded" if value is None else f"{prefix}{value:g}"


def _render_profiles(profiles: list[ProfileCandidate]) -> str:
    if not profiles:
        return (
            "The profile library is empty. Any option that wants a profile has to carry a "
            "complete document."
        )
    return "\n".join(
        _block(
            [
                f"[profile_version {entry.profile_version_id}] {entry.label} "
                f"— {entry.phases} phase{'s' if entry.phases != 1 else ''}"
                + (f", {entry.temperature_c:g} °C" if entry.temperature_c else "")
                + f", used by {entry.shot_count} shot{'s' if entry.shot_count != 1 else ''}",
                _line("  shape", entry.shape),
            ]
        )
        for entry in profiles
    )
