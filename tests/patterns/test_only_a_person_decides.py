"""Only a person starts a pattern run, approves a proposal or dismisses one.

A run spends a provider call, and approving writes a general insight and deletes Set insights,
so "who can do it" has one answer: the Knowledge page's buttons, through their routes. Five
checks, each covering a way that could stop being true:

1. **No registered tool can start a run or answer a proposal.** Walked over the registry
   itself (the Set, General and design scopes and the stdio MCP server, which registers from
   the same registry) and each tool function's code.
2. **No code outside the package, the repositories' module, the routes and the boot wiring
   names the service or the proposals' repository.** Walked over the bytecode of every module,
   so a sync hook, a timer, a task or a chat path that reached for either fails here however
   it got there.
3. **The tool context holds neither.**
4. **Booting an archive full of insights starts no run.**
5. **The names it looks for still exist**: a walk for "nothing refers to `PatternsService`"
   passes trivially the day the class is renamed.

What the static walk cannot see is a dynamic lookup (``importlib``, ``getattr``): a new one of
either is a question for review.
"""

from __future__ import annotations

import dataclasses
import dis
import importlib
import pkgutil
from pathlib import Path
from types import CodeType

import gaggiclanker
from gaggiclanker.api import patterns as patterns_api
from gaggiclanker.db.repos.patterns import PatternProposalsRepository
from gaggiclanker.patterns.service import PATTERNS_TASK, PatternsService
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import DESIGN_TOOLS, GENERAL_TOOLS, SET_TOOLS
from tests.conftest import running_app
from tests.patterns.world import build_pattern_world, build_talking

#: How code that starts a run has to name it.
STARTING = frozenset(
    {"PatternsService", "PatternsServiceDep", "get_patterns_service", "PATTERNS_TASK"}
)

#: How code that answers a proposal has to name its writer and its routes.
ANSWERING = frozenset(
    {
        "PatternProposalsRepository",
        "PatternProposalsRepoDep",
        "get_pattern_proposals_repo",
        "approve_proposal",
        "dismiss_proposal",
    }
)

#: The only modules allowed to name any of it: the package itself, the repository module,
#: the routes (with the dependencies that hand them the objects) and the boot wiring.
ALLOWED = frozenset(
    {
        "gaggiclanker/patterns/__init__.py",
        "gaggiclanker/patterns/context.py",
        "gaggiclanker/patterns/models.py",
        "gaggiclanker/patterns/service.py",
        "gaggiclanker/db/repos/patterns.py",
        "gaggiclanker/api/patterns.py",
        "gaggiclanker/api/deps.py",
        "gaggiclanker/main.py",
    }
)


def _code_objects(code: CodeType) -> list[CodeType]:
    found = [code]
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            found.extend(_code_objects(constant))
    return found


#: The only modules that may touch ``app.state.patterns``: the boot wiring that builds the one
#: instance, and the dependency that hands it to the start route.
STATE_ALLOWED = frozenset({"gaggiclanker/main.py", "gaggiclanker/api/deps.py"})

#: The packages where a lookup by string (``getattr(state, "patterns")``) would be a way round.
REACHING = ("tools", "chat", "sync", "device", "drafts", "starting", "review", "knowledge")


def _state_accesses(code: CodeType) -> list[str]:
    """``<x>.state.patterns`` loads, found in the instruction stream of one code object."""
    found: list[str] = []
    previous = ""
    for instruction in dis.get_instructions(code):
        if instruction.opname in {"LOAD_ATTR", "STORE_ATTR"}:
            if previous == "state" and instruction.argval == "patterns":
                found.append(code.co_name)
            previous = str(instruction.argval)
        elif instruction.opname not in {"CACHE", "RESUME", "COPY", "SWAP", "NOP"}:
            previous = ""
    return found


def _package_files() -> list[Path]:
    root = Path(gaggiclanker.__file__).parent
    for module in pkgutil.walk_packages(gaggiclanker.__path__, prefix="gaggiclanker."):
        if module.name.endswith("__main__"):
            continue
        importlib.import_module(module.name)
    return sorted(root.rglob("*.py"))


def test_no_registered_tool_can_start_a_run_or_answer_a_proposal() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    assert SET_TOOLS | GENERAL_TOOLS | DESIGN_TOOLS <= set(every)

    assert not [name for name in every if "pattern" in name]
    offenders: list[str] = []
    for name in every:
        spec = registry.get(name)
        assert spec is not None
        for code in _code_objects(spec.fn.__code__):
            found = (STARTING | ANSWERING | {"declined", "approve"}) & set(code.co_names)
            if found:
                offenders.append(f"{name}: {sorted(found)}")
    assert not offenders, offenders


def test_the_context_a_tool_is_handed_holds_neither() -> None:
    fields = {field.name for field in dataclasses.fields(ToolContext)}
    assert not [field for field in fields if "pattern" in field]


def test_nothing_outside_the_route_and_the_boot_wiring_names_either() -> None:
    root = Path(gaggiclanker.__file__).parent.parent
    files = _package_files()
    assert len(files) > 100, "the walk found too few modules to prove anything"

    offenders: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in ALLOWED:
            continue
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            found = (STARTING | ANSWERING) & set(code.co_names)
            if found:
                offenders.append(f"{relative}:{code.co_name} names {sorted(found)}")
    assert not offenders, offenders


def test_app_state_patterns_is_touched_only_by_the_boot_wiring_and_its_dependency() -> None:
    root = Path(gaggiclanker.__file__).parent.parent
    offenders: list[str] = []
    for path in _package_files():
        relative = path.relative_to(root).as_posix()
        if relative in STATE_ALLOWED:
            continue
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            offenders.extend(
                f"{relative}:{name} reads app.state.patterns" for name in _state_accesses(code)
            )
            reaching = relative.split("/")[1] in REACHING
            if reaching and "patterns" in code.co_consts:
                offenders.append(f"{relative}:{code.co_name} names 'patterns' as a string")
    assert not offenders, offenders


def test_the_state_walk_sees_the_accesses_it_is_there_to_forbid() -> None:
    """Otherwise it would pass on a Python whose bytecode it can no longer read."""
    root = Path(gaggiclanker.__file__).parent.parent
    seen = {
        relative
        for relative in STATE_ALLOWED
        if any(
            _state_accesses(code)
            for code in _code_objects(compile((root / relative).read_text(), relative, "exec"))
        )
    }
    assert seen == STATE_ALLOWED

    probe = compile("def f(app):\n    return app.state.patterns\n", "probe", "exec")
    assert any(_state_accesses(code) for code in _code_objects(probe))


async def test_booting_an_archive_full_of_insights_starts_no_run(env: EnvSettings) -> None:
    async with running_app(env) as (app, _client):
        await build_talking(await build_pattern_world(app.state.db))
    async with running_app(env) as (app, _client):
        assert PATTERNS_TASK not in app.state.tasks.names
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM pattern_runs") == 0
        assert await app.state.db.fetch_value("SELECT COUNT(*) FROM pattern_proposals") == 0


def test_the_names_this_file_looks_for_still_exist() -> None:
    """Otherwise the walks above are looking for words nothing uses any more."""
    assert callable(PatternsService.start)
    assert callable(PatternsService.run)
    assert PATTERNS_TASK == "patterns"
    assert callable(PatternProposalsRepository.approve)
    assert callable(PatternProposalsRepository.dismiss)
    start_names = {
        name for code in _code_objects(patterns_api.start_run.__code__) for name in code.co_names
    }
    assert "start" in start_names
    for route in (patterns_api.approve_proposal, patterns_api.dismiss_proposal):
        names = {name for code in _code_objects(route.__code__) for name in code.co_names}
        assert names & {"approve", "dismiss"}
