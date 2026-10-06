"""Only a person starts a reading: the badge or the shot page's button, through its one route.

A reading spends a provider call and writes to a shot, so the question "who can
start one" has exactly one answer. Four checks, each covering a way it could
stop being true:

1. **No registered tool can.** Walked over the registry itself (every tool, so
   the Set, General and design scopes and the stdio MCP server, which registers
   from the same registry, are all covered), over each tool function's code and
   over the context a tool is handed.
2. **No code outside the route and the boot wiring refers to the service.**
   Walked over the bytecode of every module in the package, so a sync hook, a
   timer, a task or a chat path that reached for it fails here however it got
   there.
3. **Booting an archive full of shots starts nothing**, and a sync pass has no
   path to one (the package walk above is what covers it: `sync` is one of the
   packages it reads).
4. **The names it looks for still exist.** A check for "nothing refers to
   `ReviewService`" passes trivially the day the class is renamed.

What the static walk cannot see is a dynamic lookup: a module fetched with
``importlib`` and a name read with ``getattr`` leave no trace in bytecode. A new
dynamic lookup of the review service anywhere is a question for review, not
something this file will catch.
"""

from __future__ import annotations

import dataclasses
import importlib
import pkgutil
from pathlib import Path
from types import CodeType

import gaggiclanker
from gaggiclanker.api import shots as shots_api
from gaggiclanker.review.service import ReviewService, review_task_name
from gaggiclanker.settings import EnvSettings
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import DESIGN_TOOLS, GENERAL_TOOLS, SET_TOOLS
from tests.conftest import running_app
from tests.review.conftest import build_fixture

#: How code that starts a review has to name it: the service, its task name,
#: its dependency, the direct form and the route.
STARTING = frozenset(
    {
        "ReviewService",
        "ReviewServiceDep",
        "get_review_service",
        "review_task_name",
        "run_review",
    }
)

#: The only modules allowed to name any of it: the review package itself, the
#: route behind the button (with the dependency that hands it the service), and
#: the boot wiring that builds the one instance.
ALLOWED = frozenset(
    {
        "gaggiclanker/review/__init__.py",
        "gaggiclanker/review/context.py",
        "gaggiclanker/review/evidence.py",
        "gaggiclanker/review/models.py",
        "gaggiclanker/review/reading.py",
        "gaggiclanker/review/service.py",
        "gaggiclanker/review/style.py",
        "gaggiclanker/api/shots.py",
        "gaggiclanker/api/deps.py",
        "gaggiclanker/main.py",
    }
)


def _code_objects(code: CodeType) -> list[CodeType]:
    """Every code object inside one, including comprehensions and nested defs."""
    found = [code]
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            found.extend(_code_objects(constant))
    return found


def _package_files() -> list[Path]:
    root = Path(gaggiclanker.__file__).parent
    for module in pkgutil.walk_packages(gaggiclanker.__path__, prefix="gaggiclanker."):
        if module.name.endswith("__main__"):
            continue
        importlib.import_module(module.name)
    return sorted(root.rglob("*.py"))


def test_no_registered_tool_can_start_a_review() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    # Every scope's tools are registered ones, so walking the registry walks
    # all three scopes and the MCP server built from it.
    assert SET_TOOLS | GENERAL_TOOLS | DESIGN_TOOLS <= set(every)

    assert not [name for name in every if "review" in name or "analys" in name]
    offenders: list[str] = []
    for name in every:
        spec = registry.get(name)
        assert spec is not None
        for code in _code_objects(spec.fn.__code__):
            found = STARTING & set(code.co_names)
            if found or "reviews" in code.co_names:
                offenders.append(f"{name}: {sorted(found) or ['reviews']}")
    assert not offenders, offenders


def test_the_context_a_tool_is_handed_holds_no_review_service() -> None:
    fields = {field.name for field in dataclasses.fields(ToolContext)}
    assert not [field for field in fields if "review" in field or "analy" in field]


def test_nothing_outside_the_route_and_the_boot_wiring_names_the_service() -> None:
    root = Path(gaggiclanker.__file__).parent.parent
    files = _package_files()
    assert len(files) > 100, "the walk found too few modules to prove anything"

    offenders: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in ALLOWED:
            continue
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            found = STARTING & set(code.co_names)
            if found:
                offenders.append(f"{relative}:{code.co_name} names {sorted(found)}")
    assert not offenders, offenders


async def test_booting_an_archive_of_shots_starts_no_review(env: EnvSettings) -> None:
    async with running_app(env) as (app, _client):
        fixture = await build_fixture(app.state.db)
    # A second boot over the filled archive: reconciliation, seeding, the
    # final-weight pass and the rest, with six shots none of which has a review.
    async with running_app(env) as (app, _client):
        names = app.state.tasks.names
        assert not [name for name in names if name.startswith("review:")]
        count = await app.state.db.fetch_value("SELECT COUNT(*) FROM shot_reviews")
        assert count == 0
        assert fixture.shots


def test_the_names_this_file_looks_for_still_exist() -> None:
    """Otherwise the walks above are looking for words nothing uses any more."""
    assert callable(ReviewService.start)
    assert callable(ReviewService.run_review)
    assert review_task_name(3) == "review:3"
    route_names = {
        name for code in _code_objects(shots_api.run_review.__code__) for name in code.co_names
    }
    assert "start" in route_names
    assert "review_task_name" in route_names
