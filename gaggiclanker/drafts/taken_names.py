"""Whether a name is taken: the one question every path asks.

A name is taken when another live profile is called that (by :func:`name_key`), or is called that
**on the machine**: its own name and the version of its Recipe. Two profiles never share either,
whichever way the name arrives: typed (a new profile, a rename), given at the display (a file
renamed there, kept or taken when the machine's side is chosen) or carried by a file the app has
never seen. A profile's own machine name never counts against itself. A pin's copy is a profile
with its own name, which counts as a name like any other.
"""

from __future__ import annotations

from dataclasses import dataclass

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.lineage import machine_name_sentence, name_key, person_taken_sentence
from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository
from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.domain.machine_names import machine_name

__all__ = ["NameHolder", "TakenNames"]


@dataclass(frozen=True, slots=True)
class NameHolder:
    """The live profile a name is taken by, and what it is called there."""

    row: BoardRow
    #: The name that matched: the profile's own, or its machine name.
    shown: str
    #: Whether it matched the machine name (the profile's own name does not).
    on_the_machine: bool

    @property
    def sentence(self) -> str:
        """The refusal a person reads: a profile with that name, or the profile that is called
        that on the machine."""
        if self.on_the_machine:
            return machine_name_sentence(self.shown, self.row.label)
        return person_taken_sentence(self.row.label)


class TakenNames:
    def __init__(self, board: ProfileBoardRepository, recipes: RecipesRepository) -> None:
        self.board = board
        self.recipes = recipes

    @classmethod
    def over(cls, db: Database) -> TakenNames:
        return cls(ProfileBoardRepository(db), RecipesRepository(db))

    async def holder(
        self, label: str, *, excluding: int | None = None, on_file: bool = False
    ) -> NameHolder | None:
        """Another live profile (not ``excluding``) that has this name, by :func:`name_key`: a
        profile's own name first, else a machine name.

        ``on_file`` is for a name that arrives from the machine: a profile's file is also called
        what the app last recorded for it until the next save, which may be a version the Recipe
        has moved on from, and a name given at the display meets that file as it is. Only a profile
        a Recipe brews has such a name: every other file is called its profile's name."""
        wanted = name_key(label)
        rows = [r for r in await self.board.list_rows() if r.id != excluding]
        for row in rows:
            if name_key(row.label) == wanted:
                return NameHolder(row, row.label, False)
        versions = await self.recipes.version_labels_by_profile()
        for row in rows:
            if row.id in versions:
                shown = machine_name(row.label, versions[row.id])
                if name_key(shown) == wanted:
                    return NameHolder(row, shown, True)
        if on_file:
            for row in rows:
                if (
                    row.id in versions
                    and row.device_label is not None
                    and name_key(row.device_label) == wanted
                ):
                    return NameHolder(row, row.device_label, True)
        return None

    async def keys(self) -> set[str]:
        """Every taken name, as :func:`name_key`: each live profile's own and machine name and the
        name its file has now when a Recipe brews it (what an unseen file joining the list is
        renamed away from)."""
        versions = await self.recipes.version_labels_by_profile()
        taken: set[str] = set()
        for row in await self.board.list_rows():
            taken.add(name_key(row.label))
            if row.id in versions:
                taken.add(name_key(machine_name(row.label, versions[row.id])))
                if row.device_label is not None:
                    taken.add(name_key(row.device_label))
        return taken
