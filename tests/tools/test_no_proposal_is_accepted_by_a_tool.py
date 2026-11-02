"""Nothing a model drives can accept or decline a proposed change, or answer a proposed grade.

A proposal exists so that a person decides. **Signatures are the one named exception** (the
maintainer's decision, 2026-10-08): what an agent proposes as a profile's expectations, and an
override of one limit on a Recipe version, are written in force and stay so unless a person
rejects them. Every other proposal is still guarded, and the guard below still fails if a tool
starts writing anything else in force: the exception is exactly the list in
:data:`SIGNATURES_IN_FORCE` and nothing wider. An agent that could accept its own
proposal would have exactly the power the proposal was invented to take away —
changing what the next shot is filed under with nobody agreeing to it — and it
would do it while looking like the feature working.

Three checks, and each covers a way that could stop being true:

1. **No tool is named for it.** Walked over the registry itself, so a
   `propose_recipe_version_and_accept` added next year fails here rather than in a
   review.
2. **No code in the tool package calls it.** Walked over the bytecode of every
   module under ``gaggiclanker.tools``, so a tool that reached the proposals
   repository and called ``accept`` on it fails too, however it got there.
3. **The names it looks for still exist**, on the repository and in the router.
   A check for "nothing calls `accept`" passes trivially the day the method is
   renamed, and a green test that proves nothing is worse than no test.
4. **The signature exception is exactly as wide as it was decided to be.** The tools reach
   signatures through two named service methods, and the service has no other way to put
   something in force; the person's side (reject, restore, tier) is as banned as accept is.

What this does not prove: that no *other* part of gaggiclanker accepts a
proposal. Two do, and they are the two routes a person presses. That is the
design; the scope of this file is what a model can reach.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from types import CodeType

import gaggiclanker.tools
from gaggiclanker.db.repos.insight_deletions import InsightDeletionsRepository
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository
from gaggiclanker.db.repos.outcome_proposals import OutcomeProposalsRepository
from gaggiclanker.db.repos.patterns import PatternProposalsRepository
from gaggiclanker.db.repos.recipe_proposals import RecipeProposalsRepository
from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.db.repos.signature_requests import SignatureRequestsRepository
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.signatures.change_requests import SignatureRequestService
from gaggiclanker.signatures.service import SignatureService
from gaggiclanker.tools.registry import registry

#: The decisions, and the writes that make one count. Looked for as attribute
#: names, which is how a call to any of them would appear in any code that made
#: one. The first two answer a proposed change. `change` and `dismiss` answer a
#: proposed grade with another outcome or none; `record_in_transaction` and
#: `grade_in_transaction` are the writes that put a grade on a version, and
#: `set_outcome` and `clear_outcome` are the Recipe page's own: a tool that
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
    # An insight a Recipe's conversation proposed is added (confirmed) or dismissed
    # by a person: `set_confirmed` is the add and the take back, `dismiss` the
    # other card button.
    "set_confirmed",
    # An added insight leaves only by a person's press: `delete` is the Delete on the
    # Recipe page and the Knowledge page, `delete_in_transaction` the write every removal
    # runs (a replacement's Add and an accepted deletion proposal included), and
    # `keep` the other button on an agent's deletion proposal.
    "delete",
    "delete_in_transaction",
    "keep",
    # The private methods behind them: the add of a replacement and the take back, which
    # also stales waiting deletion proposals.
    "_add_in_transaction",
    "_take_back_in_transaction",
    # A pattern proposal is approved (writes a general insight and deletes the Recipe insights
    # it came from) by a person's press on the Knowledge page, and nothing else.
    "approve",
    # A signature's expectations and a Recipe version's override are rejected and restored by a
    # person's press on the Profiles and Recipe pages: these are those buttons and the tier move.
    # (What a tool *proposes* there is in force at once; see SIGNATURES_IN_FORCE.)
    "reject",
    "restore",
    "reject_override",
    "restore_override",
    "set_tier",
    # The writes underneath the exception: a tool reaches them only through the two service
    # methods named below, never directly, so it cannot write a signature row of its own
    # making (a status, a needs-phase row) or an override with a status.
    "add_valid",
    "add_override",
    # A signature change request is answered by a person's press on its card, or with its draft,
    # and by nothing else: `approve` and `decline` are banned above, and these are the writes
    # behind them (the one that applies the changes, the one that moves a status, the hooks that
    # make a request stale). A tool reaches `request` and nothing more.
    "apply_for_draft",
    "_apply_changes",
    "request_for_draft",
    "decide",
    "mark_stale",
    "set_written",
    "stale_naming",
    "stale_where",
    "stale_for_drafts",
    # The service's repository: a tool reads through `SignatureService` methods only, so
    # `service.repo.add(...)` (the one write with no name of its own) is out of reach too.
    "repo",
)

#: Names a tool module may not mention at all: the repository and its write models. A tool
#: that imported them could write a signature row of any status, whatever method it called.
SIGNATURE_STORAGE_NAMES = (
    "SignatureRepository",
    "ExpectationWrite",
    "OverrideWrite",
    "SignatureRequestsRepository",
    "SignatureRequestWrite",
)
#: Raw SQL against the signature tables is refused the same way, by the strings a module holds.
SIGNATURE_TABLES = (
    "signature_expectations",
    "recipe_version_signature_overrides",
    "profile_signatures",
    "signature_change_requests",
)

#: The signature exception: the only service methods through which a tool puts something in
#: force. `propose` and `propose_override` are what the two propose tools call; `add_valid`
#: (a draft's expectations, called by the draft store, not a tool) and the carrying are the
#: service's other writers, none of them a tool's.
SIGNATURE_SERVICE_READERS = frozenset({"phase_names", "count_in_force"})
SIGNATURES_IN_FORCE = frozenset({"propose", "propose_override"})
SIGNATURE_SERVICE_WRITERS = frozenset(
    {"propose", "propose_override", "add_valid", "carry", "carry_quietly"}
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


#: Tools whose name contains a decision's word and is not that decision: it **asks** for a
#: change a person answers (`SignatureRequestService.request` is all it reaches; the walk below
#: and the signature-request guard further down keep it that way).
NAMES_THAT_ASK = frozenset({"request_signature_change"})


def _names_a_decision(tool_name: str) -> bool:
    return tool_name not in NAMES_THAT_ASK and any(word in tool_name for word in DECIDING)


def test_a_decision_is_found_in_a_name_and_a_request_for_one_is_not() -> None:
    assert _names_a_decision("propose_recipe_version_and_accept")
    assert _names_a_decision("approve_signature_change")
    assert not _names_a_decision("request_signature_change")
    # Only the one name is let through, not every name that has "request" in it.
    assert _names_a_decision("request_and_accept")


def test_no_tool_is_named_for_a_decision_a_person_makes() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    assert not [name for name in every if _names_a_decision(name)], (
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
    assert callable(RecipeProposalsRepository.accept)
    assert callable(RecipeProposalsRepository.decline)
    assert callable(OutcomeProposalsRepository.accept)
    assert callable(OutcomeProposalsRepository.change)
    assert callable(OutcomeProposalsRepository.dismiss)
    assert callable(OutcomeProposalsRepository.record_in_transaction)
    assert callable(RecipesRepository.grade_in_transaction)
    assert callable(RecipesRepository.set_outcome)
    assert callable(RecipesRepository.clear_outcome)
    assert callable(InsightsRepository.set_confirmed)
    assert callable(InsightsRepository.dismiss)
    assert callable(InsightsRepository.delete)
    assert callable(InsightsRepository.delete_in_transaction)
    assert callable(InsightsRepository._add_in_transaction)
    assert callable(InsightsRepository._take_back_in_transaction)
    assert callable(InsightDeletionsRepository.accept)
    assert callable(InsightDeletionsRepository.keep)
    assert callable(SignatureRepository.reject)
    assert callable(SignatureRepository.restore)
    assert callable(SignatureRepository.reject_override)
    assert callable(SignatureRepository.restore_override)
    assert callable(SignatureRepository.add_override)
    assert callable(SignatureService.add_valid)
    assert callable(SignatureRepository.set_tier)
    assert callable(SignatureRequestService.approve)
    assert callable(SignatureRequestService.decline)
    assert callable(SignatureRequestService.apply_for_draft)
    assert callable(SignatureRequestService._apply_changes)
    assert callable(SignatureRequestsRepository.decide)
    assert callable(SignatureRequestsRepository.mark_stale)
    assert callable(PatternProposalsRepository.approve)
    assert callable(PatternProposalsRepository.dismiss)


def test_the_signature_exception_is_the_named_service_methods_and_nothing_wider() -> None:
    """Whoever adds a service method that writes signatures must come here and decide."""
    public = {
        name
        for name, member in vars(SignatureService).items()
        if inspect.iscoroutinefunction(member) and not name.startswith("_")
    }
    # The readers only read; every other coroutine of the service writes.
    assert public - SIGNATURE_SERVICE_READERS == SIGNATURE_SERVICE_WRITERS
    assert SIGNATURES_IN_FORCE <= SIGNATURE_SERVICE_WRITERS


def test_the_signature_tools_reach_only_the_named_methods() -> None:
    """The two propose tools are the whole of the exception: every other service writer is
    unreachable from the tool package, and so is the repository's raw write."""
    reachable: set[str] = set()
    for path in _tool_modules():
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            reachable |= SIGNATURE_SERVICE_WRITERS & set(code.co_names)
    assert reachable == SIGNATURES_IN_FORCE, reachable


def test_no_tool_module_names_the_signature_storage_or_its_tables() -> None:
    """The guard above is by method name; this one closes the two doors it cannot see: the
    repository's own `add` (a name too common to ban alone) and raw SQL. Not guarded, and not
    claimed: code outside `gaggiclanker.tools` that a tool is handed a reference to."""
    offenders: list[str] = []
    for path in _tool_modules():
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            for name in SIGNATURE_STORAGE_NAMES:
                if name in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} names {name}")
            for const in code.co_consts:
                if isinstance(const, str) and any(table in const for table in SIGNATURE_TABLES):
                    offenders.append(f"{path.name}:{code.co_name} holds SQL on a signature table")
    assert not offenders, offenders


#: What the signature-change service lets a tool do: ask. Everything that answers, applies or
#: makes a request stale is a person's press, a draft's approval or the transaction of the cause.
REQUEST_SERVICE_READERS = frozenset({"phase_names", "problem_list", "problems", "view", "views"})
REQUEST_SERVICE_WRITERS = frozenset(
    {"request", "request_for_draft", "approve", "decline", "apply_for_draft"}
)


def test_the_request_service_has_exactly_the_writers_this_file_knows() -> None:
    public = {
        name
        for name, member in vars(SignatureRequestService).items()
        if inspect.iscoroutinefunction(member) and not name.startswith("_")
    }
    assert public - REQUEST_SERVICE_READERS == REQUEST_SERVICE_WRITERS


def test_a_tool_reaches_the_request_service_only_to_ask() -> None:
    reachable: set[str] = set()
    for path in _tool_modules():
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            reachable |= REQUEST_SERVICE_WRITERS & set(code.co_names)
    assert reachable == {"request"}, reachable
