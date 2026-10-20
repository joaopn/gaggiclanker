"""Duplicating a Recipe and previewing the duplicate are a person's.

A duplicate puts a new profile on the profile list (and so, at the next sync, on the machine) and
starts a Recipe. Neither may be started by a chat tool or the MCP server. This file pins it by name:
no tool is called for it, no module of the tool package refers to its service, its writer or the
copy function, and the routes it is meant to be reached by still exist (so the check does not pass
for words nothing uses any more).
"""

from __future__ import annotations

from gaggiclanker.api.duplicates import duplicate_version, preview_duplicate
from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.drafts.duplicates import DuplicateService
from gaggiclanker.tools.registry import registry
from tests.tools.test_no_proposal_is_accepted_by_a_tool import _code_objects, _tool_modules

DUPLICATE_WRITERS = ("duplicate", "create_in_transaction", "copy_profile")
DUPLICATE_NAMES = ("DuplicateService", "DuplicateRequest", "ProfileCopier")


def test_the_duplicate_writers_and_routes_are_where_this_file_thinks_they_are() -> None:
    assert callable(DuplicateService.duplicate) and callable(DuplicateService.preview)
    assert callable(RecipesRepository.create_in_transaction)
    assert callable(duplicate_version) and callable(preview_duplicate)


def test_no_tool_is_named_for_a_duplicate() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    assert not [name for name in every if "duplicate" in name or "copy" in name], every


def test_no_module_of_the_tool_package_refers_to_the_duplicate_or_its_writers() -> None:
    files = _tool_modules()
    assert files, "the walk found no tool modules, so it proved nothing"
    offenders: list[str] = []
    for path in files:
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            for word in DUPLICATE_WRITERS:
                if word in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} refers to {word!r}")
            for name in DUPLICATE_NAMES:
                if name in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} names {name}")
    assert not offenders, offenders
