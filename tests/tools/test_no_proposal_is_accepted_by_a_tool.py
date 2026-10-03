"""Nothing a model drives can accept or decline a proposed change, or answer a proposed grade.

A proposal exists so that a person decides. An agent that could accept its own
proposal would have exactly the power the proposal was invented to take away —
changing what the next shot is filed under with nobody agreeing to it — and it
would do it while looking like the feature working.

Three checks, and each covers a way that could stop being true:

1. **No tool is named for it.** Walked over the registry itself, so a
   `propose_set_version_and_accept` added next year fails here rather than in a
   review.
2. **No code in the tool package calls it.** Walked over the bytecode of every
   module under ``gaggiclanker.tools``, so a tool that reached the proposals
   repository and called ``accept`` on it fails too, however it got there.
3. **The names it looks for still exist**, on the repository and in the router.
   A check for "nothing calls `accept`" passes trivially the day the method is
   renamed, and a green test that proves nothing is worse than no test.

What this does not prove: that no *other* part of gaggiclanker accepts a
proposal. Two do, and they are the two routes a person presses. That is the
design; the scope of this file is what a model can reach.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import CodeType

import gaggiclanker.tools
from gaggiclanker.api.knowledge import delete_insight, dismiss_insight, patch_insight
from gaggiclanker.api.patterns import approve_proposal, dismiss_proposal
from gaggiclanker.api.sets import (
    accept_insight_deletion,
    accept_outcome_proposal,
    accept_proposal,
    change_outcome_proposal,
    decline_proposal,
    dismiss_outcome_proposal,
    keep_insight_deletion,
)
from gaggiclanker.db.repos.insight_deletions import InsightDeletionsRepository
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repos.outcome_proposals import OutcomeProposalsRepository
from gaggiclanker.db.repos.patterns import PatternProposalsRepository
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.tools.registry import registry

#: The decisions, and the writes that make one count. Looked for as attribute
#: names, which is how a call to any of them would appear in any code that made
#: one. The first two answer a proposed change. `change` and `dismiss` answer a
#: proposed grade with another outcome or none; `record_in_transaction` and
#: `grade_in_transaction` are the writes that put a grade on a version, and
#: `set_outcome` and `clear_outcome` are the Set page's own: a tool that
#: reached any of them would be recording an outcome nobody agreed to.
DECIDING = (
    "accept",
    "decline",
    "change",
    "dismiss",
    "record_in_transaction",
    "grade_in_transaction",
    "set_outcome",
    "clear_outcome",
    # An insight a Set's conversation proposed is added (confirmed) or dismissed
    # by a person: `set_confirmed` is the add and the take back, `dismiss` the
    # other card button.
    "set_confirmed",
    # An added insight leaves only by a person's press: `delete` is the Delete on the
    # Set page and the Knowledge page, `delete_in_transaction` the write every removal
    # runs (a replacement's Add and an accepted deletion proposal included), and
    # `keep` the other button on an agent's deletion proposal.
    "delete",
    "delete_in_transaction",
    "keep",
    # The private methods behind them: the add of a replacement and the take back, which
    # also stales waiting deletion proposals.
    "_add_in_transaction",
    "_take_back_in_transaction",
    # A pattern proposal is approved (writes a general insight and deletes the Set insights
    # it came from) by a person's press on the Knowledge page, and nothing else.
    "approve",
)


def _code_objects(code: CodeType) -> list[CodeType]:
    """Every code object inside one, including comprehensions and nested defs."""
    found = [code]
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            found.extend(_code_objects(constant))
    return found


def _tool_modules() -> list[Path]:
    """Every source file of the tool package, the MCP server included."""
    root = Path(gaggiclanker.tools.__file__).parent
    names = [
        module.name
        for module in pkgutil.walk_packages(
            gaggiclanker.tools.__path__, prefix="gaggiclanker.tools."
        )
    ]
    for name in names:
        importlib.import_module(name)
    return sorted(root.rglob("*.py"))


def test_no_tool_is_named_for_a_decision_a_person_makes() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    assert not [name for name in every if any(word in name for word in DECIDING)], (
        "accept and decline are routes a person presses, never tools"
    )


def test_no_code_in_the_tool_package_accepts_or_declines_anything() -> None:
    files = _tool_modules()
    assert files, "the walk found no tool modules, so it proved nothing"

    offenders: list[str] = []
    for path in files:
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            for word in DECIDING:
                if word in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} refers to {word!r}")
    assert not offenders, offenders


def test_the_two_decisions_are_where_this_file_thinks_they_are() -> None:
    """Otherwise the walk above is looking for words nothing uses any more."""
    assert callable(SetProposalsRepository.accept)
    assert callable(SetProposalsRepository.decline)
    assert callable(OutcomeProposalsRepository.accept)
    assert callable(OutcomeProposalsRepository.change)
    assert callable(OutcomeProposalsRepository.dismiss)
    assert callable(OutcomeProposalsRepository.record_in_transaction)
    assert callable(SetsRepository.grade_in_transaction)
    assert callable(SetsRepository.set_outcome)
    assert callable(SetsRepository.clear_outcome)
    assert callable(InsightsRepository.set_confirmed)
    assert callable(InsightsRepository.dismiss)
    assert callable(InsightsRepository.delete)
    assert callable(InsightsRepository.delete_in_transaction)
    assert callable(InsightsRepository._add_in_transaction)
    assert callable(InsightsRepository._take_back_in_transaction)
    assert callable(InsightDeletionsRepository.accept)
    assert callable(InsightDeletionsRepository.keep)
    assert callable(PatternProposalsRepository.approve)
    assert callable(PatternProposalsRepository.dismiss)
    assert callable(approve_proposal)
    assert callable(dismiss_proposal)
    for route in (
        accept_outcome_proposal,
        change_outcome_proposal,
        dismiss_outcome_proposal,
        patch_insight,
        dismiss_insight,
        delete_insight,
        accept_insight_deletion,
        keep_insight_deletion,
    ):
        assert callable(route)
    assert callable(accept_proposal)
    assert callable(decline_proposal)
