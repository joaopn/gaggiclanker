"""Archiving a Recipe, bringing it back, and switching a profile on or off are a person's.

Archive unpins the Recipe's pins and switches its own profile off, so at the next sync the machine
loses files; Unarchive switches the profile on again. Neither may be started by a chat tool or the
MCP server. This file pins it by name: no tool is called for it, no module of the tool package
refers to the writers, and the writers it looks for still exist (so the walk does not pass for
words nothing uses any more).
"""

from __future__ import annotations

from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.drafts.board import BoardService
from gaggiclanker.tools.registry import registry
from tests.tools.test_no_proposal_is_accepted_by_a_tool import _code_objects, _tool_modules

WRITERS = ("archive", "unarchive", "_switch_profile", "recipe_on_machine")


def test_the_writers_are_where_this_file_thinks_they_are() -> None:
    assert callable(RecipesRepository.archive) and callable(RecipesRepository.unarchive)
    assert callable(RecipesRepository._switch_profile)
    assert callable(BoardService.recipe_on_machine)


def test_no_tool_is_named_for_archiving_or_the_switch() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    assert not [name for name in every if "archive" in name or "on_machine" in name], every


def test_no_module_of_the_tool_package_refers_to_one_of_the_writers() -> None:
    files = _tool_modules()
    assert files, "the walk found no tool modules, so it proved nothing"
    offenders: list[str] = []
    for path in files:
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            for word in WRITERS:
                if word in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} refers to {word!r}")
    assert not offenders, offenders
