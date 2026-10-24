"""Renaming a profile is a person's: no chat tool, MCP tool or agent path does it.

A rename changes what the machine's brew screen and every list call a profile, so only the person's
Rename (``PUT /api/profile-board/{id}/name``) may do it. The object-graph walk in
``test_no_machine_reachable`` proves no tool is handed the board service; this file pins the other
half by name: no tool is called for a rename, no module of the tool package refers to the writer, to
the row patch that carries a name or to the route, and the writer and the route are where this file
thinks they are (so it does not pass for words nothing uses any more).
"""

from __future__ import annotations

from gaggiclanker.db.repos.profile_board import BoardRowPatch
from gaggiclanker.drafts.board import BoardService
from gaggiclanker.tools.registry import registry
from tests.tools.test_no_proposal_is_accepted_by_a_tool import _code_objects, _tool_modules

#: Looked for as attribute and global names in every tool module.
RENAME_WRITERS = ("rename", "rename_profile", "BoardRowPatch", "BoardService")
ROUTE_WORDS = ("/name", "profile-board")


def test_the_writer_and_its_route_are_where_this_file_thinks_they_are() -> None:
    assert callable(BoardService.rename)
    assert "label" in BoardRowPatch.model_fields


def test_no_tool_is_named_for_a_rename() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    assert not [name for name in every if "rename" in name or name.endswith("_name")], every
    # Every kind of tool, as the chat and the MCP server list them.
    for permissions in (frozenset({"read"}), frozenset({"read", "propose"})):
        assert not [s.name for s in registry.specs(permissions) if "rename" in s.name]


def test_no_module_of_the_tool_package_refers_to_the_writer_the_patch_or_the_route() -> None:
    files = _tool_modules()
    assert files, "the walk found no tool modules, so it proved nothing"
    offenders: list[str] = []
    for path in files:
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            for word in RENAME_WRITERS:
                if word in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} refers to {word!r}")
            for const in code.co_consts:
                if isinstance(const, str) and any(word in const for word in ROUTE_WORDS):
                    offenders.append(f"{path.name}:{code.co_name} holds {const!r}")
    assert not offenders, offenders
