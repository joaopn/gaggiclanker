"""Nothing a model drives can accept or decline a proposed change.

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
from gaggiclanker.api.sets import accept_proposal, decline_proposal
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.tools.registry import registry

#: The two decisions. Looked for as attribute names, which is how a call to
#: either would appear in any code that made one.
DECIDING = ("accept", "decline")


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
    assert callable(accept_proposal)
    assert callable(decline_proposal)
