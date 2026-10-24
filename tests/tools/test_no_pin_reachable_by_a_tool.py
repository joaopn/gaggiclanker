"""Pinning a Recipe version to the machine, unpinning it and previewing the pin are a person's.

A pin puts a profile on the profile list and so, at the next sync, on the machine; an unpin takes
it off. Neither may be started by a chat tool or the MCP server. The object-graph walk in
``test_no_machine_reachable`` proves no tool is handed the pin service or the copy function; this
file pins the other half by name: no tool is called for it, no module of the tool package refers
to its writers or its storage, and the routes it is meant to be reached by still exist (so the
walk does not pass for words nothing uses any more).
"""

from __future__ import annotations

from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.drafts.copies import ProfileCopier
from gaggiclanker.drafts.pins import PinService
from gaggiclanker.tools.registry import registry
from tests.tools.test_no_proposal_is_accepted_by_a_tool import _code_objects, _tool_modules

#: The writers of a pin and of the copy it is made from, looked for as attribute names.
PIN_WRITERS = (
    "pin",
    "unpin",
    "unpin_recipe",
    "mark_unpinned",
    "revive",
    "copy_profile",
)
#: The pin storage and services, which no tool module may name at all.
PIN_STORAGE_NAMES = ("PinsRepository", "PinWrite", "PinService", "ProfileCopier")
PIN_TABLES = ("recipe_version_pins",)


def test_the_pin_writers_and_routes_are_where_this_file_thinks_they_are() -> None:
    assert callable(PinService.pin) and callable(PinService.unpin) and callable(PinService.preview)
    assert callable(PinsRepository.unpin) and callable(PinsRepository.unpin_recipe)
    assert callable(PinsRepository.mark_unpinned) and callable(PinsRepository.revive)
    assert callable(ProfileCopier.copy_profile)


def test_no_tool_is_named_for_a_pin() -> None:
    every = registry.names()
    assert every, "an empty registry would pass this vacuously"
    assert not [name for name in every if "pin" in name.split("_")], every


def test_no_module_of_the_tool_package_refers_to_a_pin_writer_or_its_storage() -> None:
    files = _tool_modules()
    assert files, "the walk found no tool modules, so it proved nothing"
    offenders: list[str] = []
    for path in files:
        for code in _code_objects(compile(path.read_text(), str(path), "exec")):
            for word in PIN_WRITERS:
                if word in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} refers to {word!r}")
            for name in PIN_STORAGE_NAMES:
                if name in code.co_names:
                    offenders.append(f"{path.name}:{code.co_name} names {name}")
            for const in code.co_consts:
                if isinstance(const, str) and any(table in const for table in PIN_TABLES):
                    offenders.append(f"{path.name}:{code.co_name} holds SQL on a pin table")
    assert not offenders, offenders
