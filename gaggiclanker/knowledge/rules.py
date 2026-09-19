"""Seeding the rule tier, and choosing which rules an analysis is told about.

Two jobs, and they are here together because they are two halves of one
contract: the seed file declares what a rule applies to, and
:func:`select_rules` is the only code that reads those declarations.

**Selection is deterministic.** That is the acceptance criterion for this chunk
and the reason none of this is SQL: given the same Set attributes, the same
detected style and the same diagnostic signals, two runs produce the same rule
ids in the same order — (category rank, category, key). A prompt that quietly
changed its knowledge between two runs would make "why did it say that" an
unanswerable question, and the model is asked to name the rules it used
precisely so that a rule that misleads can be found and turned off.

**The signal grammar.** `applies.signal` is a list of tokens, and a rule matches
if *any* of them is present. The tokens are built by
:func:`gaggiclanker.analyzer.context.signal_tokens` and there are eight shapes:

    ``<metric>:<LABEL>``   a diagnostics band, e.g. ``channeling_risk:HIGH``
    ``primary:<name>``     a channeling indicator that fired, e.g. ``primary:pressure_cliff``
    ``taste:<slug>``       a flavour-wheel taste note the user recorded, and every
                           node inside it (``taste:other.chemical.bitter`` and
                           ``taste:other.chemical``, ``taste:other``); plus
                           ``taste:sour_and_bitter`` when the cup is on both sides
    ``aroma:<slug>``       the same for an aroma note
    ``balance:<value>``    the user's sour/balanced/bitter verdict
    ``first_drip:<fast|slow>``, ``avg_flow:<high|low>``, ``temp:<cold|hot>``
    ``scale:absent``       the shot was pulled without a scale
    ``style:<style>``      the detected shot style, also matched by `applies.style`

A rule with no `applies` at all matches everything, which is what the procedure
categories (dial-in order, increments, safety bounds) want: they are not facts
about a bean, they are how the job is done.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gaggiclanker.db.repos.knowledge import RuleRow, RulesRepository, RuleWrite
from gaggiclanker.domain.vocab import RULE_CATEGORIES

__all__ = [
    "DEFAULT_RULES_FILE",
    "RuleSeedError",
    "RuleSelection",
    "SetContext",
    "load_seed_rules",
    "render_rules",
    "seed_rules",
    "select_rules",
]

log = structlog.get_logger(__name__)

#: Shipped inside the package, next to the prompts and the migrations, for the
#: same reason: the wheel and the container image carry ``gaggiclanker/`` and
#: nothing else.
DEFAULT_RULES_FILE = Path(__file__).resolve().parent / "seed" / "rules.yaml"

#: The dimensions `applies` may name. Anything else is a typo in the seed file
#: and is rejected loudly rather than silently matching everything — a rule that
#: applies to every shot because somebody wrote `roast` for `roast_level` is the
#: worst kind of bug here, because the output still looks plausible.
_APPLIES_KEYS = frozenset({"roast_level", "process", "style", "burr_type", "decaf", "signal"})


class RuleSeedError(Exception):
    """The seed file is not a valid set of rules."""


class _SeedFile(BaseModel):
    """The YAML schema. Strict: an unknown key is a typo, not an extension."""

    model_config = ConfigDict(extra="forbid")

    rules: list[RuleWrite] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SetContext:
    """The Set attributes rule selection filters on.

    A flat record rather than the `SetRow`/`BeanRow` pair, because selection is
    also run by the tests and by a future chat turn that has the attributes but
    not the rows, and because every field here is allowed to be unknown: a Set
    with no grinder, a bag whose roaster printed no process, a shot with no Set
    at all. ``None`` means "not stated", and a rule that filters on an unstated
    dimension does not match — saying nothing is better than reasoning from a
    guess.
    """

    roast_level: str | None = None
    process: str | None = None
    burr_type: str | None = None
    decaf: bool = False


@dataclass(frozen=True, slots=True)
class RuleSelection:
    """What an analysis was told, and what it was allowed to cite."""

    rules: list[RuleRow] = field(default_factory=list)
    #: The signal tokens the selection was made against. Stored on the analysis
    #: input snapshot so a later reader can see *why* a rule was chosen, not
    #: only that it was.
    signals: list[str] = field(default_factory=list)

    @property
    def keys(self) -> set[str]:
        """The keys the model may name in ``rules_used``."""
        return {rule.key for rule in self.rules}

    def render(self) -> str:
        """The rules as prompt text, grouped by category in selection order."""
        return render_rules(
            [{"category": rule.category, "key": rule.key, "text": rule.text} for rule in self.rules]
        )


def render_rules(rules: list[dict[str, str]]) -> str:
    """Selected rules as prompt text, grouped by category in selection order.

    Takes dicts rather than rows because the analysis context stores its rules
    as plain JSON — the snapshot on the row has to render the same way months
    later, when the `knowledge_rules` table has moved on. One renderer, so the
    live prompt and the stored one cannot disagree about what was said.
    """
    lines: list[str] = []
    current = ""
    for rule in rules:
        if rule["category"] != current:
            current = rule["category"]
            lines.append(f"\n{current}:")
        lines.append(f"  [{rule['key']}] {rule['text']}")
    return "\n".join(lines).strip() or "(no rules matched this shot)"


def load_seed_rules(path: Path | None = None) -> list[RuleWrite]:
    """Parse the seed file. Raises :class:`RuleSeedError` on anything wrong."""
    source = path or DEFAULT_RULES_FILE
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuleSeedError(f"could not read {source}: {exc}") from None
    except yaml.YAMLError as exc:
        raise RuleSeedError(f"{source} is not valid YAML: {exc}") from None
    if not isinstance(document, dict):
        raise RuleSeedError(f"{source} must be a YAML mapping with a `rules` list")
    try:
        parsed = _SeedFile.model_validate(document)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        raise RuleSeedError(f"{source} does not match the rule schema: {details}") from None

    seen: set[tuple[str, str]] = set()
    for rule in parsed.rules:
        if rule.category not in RULE_CATEGORIES:
            raise RuleSeedError(
                f"rule {rule.key!r} has category {rule.category!r}, which is not one of "
                f"{', '.join(RULE_CATEGORIES)}"
            )
        unknown = set(rule.applies) - _APPLIES_KEYS
        if unknown:
            raise RuleSeedError(
                f"rule {rule.category}/{rule.key} filters on {', '.join(sorted(unknown))}, "
                f"which is not a known dimension ({', '.join(sorted(_APPLIES_KEYS))})"
            )
        identity = (rule.category, rule.key)
        if identity in seen:
            raise RuleSeedError(f"two rules share the key {rule.category}/{rule.key}")
        seen.add(identity)
    return parsed.rules


async def seed_rules(repo: RulesRepository, path: Path | None = None) -> int:
    """Upsert every rule in the seed file. Returns how many rows changed.

    The three-way upsert `prompts` uses, for the same reasons
    (:mod:`gaggiclanker.llm.prompts`): insert what is new, take the new text
    where nobody has edited the row, and record only the new *default* where
    somebody has — so an upgrade reaches every untouched rule and "reset to
    default" converges on the new wording rather than on the version the user
    forked from.

    A rule whose file entry has vanished is left alone. An image that dropped a
    rule should not delete a row the user may have edited, and disabling is what
    "I do not want this rule" means here.

    A broken seed file is logged and skipped rather than raised: the archive
    must still boot, and an analysis that runs with no rules says so in its
    context instead of taking the app down.
    """
    try:
        rules = load_seed_rules(path)
    except RuleSeedError as exc:
        log.error("rule_seed_invalid", error=str(exc))
        return 0

    changed = 0
    for rule in rules:
        existing = await repo.get_by_key(rule.category, rule.key)
        if existing is None:
            await repo.insert(rule)
            changed += 1
            continue
        if existing.edited:
            await repo.refresh_default(rule)
            continue
        if existing.value == rule.value and existing.unit == rule.unit:
            continue
        await repo.refresh_both(rule)
        changed += 1

    if changed:
        log.info("knowledge_rules_seeded", changed=changed, total=len(rules))
    return changed


def _matches(applies: dict[str, Any], context: SetContext, style: str, signals: set[str]) -> bool:
    """Whether one rule's conditions hold. Every stated dimension must match."""
    if not applies:
        return True

    roast = applies.get("roast_level")
    if roast is not None and (context.roast_level is None or context.roast_level not in roast):
        return False

    process = applies.get("process")
    if process is not None and (context.process is None or context.process not in process):
        return False

    burr = applies.get("burr_type")
    if burr is not None and (context.burr_type is None or context.burr_type not in burr):
        return False

    styles = applies.get("style")
    if styles is not None and style not in styles:
        return False

    decaf = applies.get("decaf")
    if decaf is not None and bool(decaf) != context.decaf:
        return False

    tokens = applies.get("signal")
    # Any-match, not all-match. A taste rule lists every note that points at the
    # same suspect (a sour note, the sour balance verdict); requiring all of
    # them would mean the rule only fired for somebody who ticked every box.
    return not (tokens is not None and not (set(tokens) & signals))


async def select_rules(
    repo: RulesRepository,
    context: SetContext,
    style: str,
    signals: set[str] | list[str],
) -> RuleSelection:
    """The enabled rules that apply, in a stable order.

    Deterministic by construction: the repository returns rules sorted by
    (category rank, category, key), and this filters that list without
    reordering it. Two analyses of the same shot therefore select the same rules
    in the same order, which is what the chunk's first acceptance criterion asks
    for.

    Disabled rules are excluded *here* rather than in the caller, so turning a
    rule off in the UI removes it from the very next analysis with nothing else
    to remember.
    """
    tokens = set(signals)
    # The style is a signal as well as a dimension: a band rule can key on it
    # without every style rule having to spell out a `style` filter too.
    tokens.add(f"style:{style}")
    rules = [
        rule
        for rule in await repo.list_rules(enabled=True)
        if _matches(rule.applies or {}, context, style, tokens)
    ]
    return RuleSelection(rules=rules, signals=sorted(tokens))
