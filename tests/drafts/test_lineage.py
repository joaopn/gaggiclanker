"""The one lineage rule: where a draft's version lands, whoever asks."""

from __future__ import annotations

from gaggiclanker.db.repos.lineage import (
    change_destination,
    lineage_owner,
    name_key,
    place_draft,
    taken_name_sentence,
)

PROFILES = {"X [AI]": "x-profile", "Mine": "mine-profile"}


class _Lookup:
    async def by_recipe(self, set_id: int) -> str | None:
        return "Mine"

    async def by_label(self, label: str) -> str | None:
        return label if label in PROFILES else None

    def label_of(self, profile: str) -> str:
        return profile


async def _owner(label: str, **over: object) -> str | None:
    return await lineage_owner(_Lookup(), set_id=over.get("set_id"), version_label=label)  # type: ignore[arg-type]


async def test_a_draft_continues_the_profile_with_exactly_its_name() -> None:
    assert await _owner("X [AI]") == "X [AI]"
    assert await _owner("Y [AI]") is None


async def test_a_recipes_draft_goes_to_the_recipes_profile_only_when_that_is_the_name() -> None:
    assert await _owner("Mine", set_id=3) == "Mine"
    # Renamed, it is a profile of its own (or the one that already has that name), never a
    # rename of the Recipe's profile.
    assert await _owner("Renamed", set_id=3) is None
    assert await _owner("X [AI]", set_id=3) == "X [AI]"


def test_the_refusal_names_the_profile_and_says_what_to_do() -> None:
    assert taken_name_sentence("Adaptive Bloom") == (
        "Adaptive Bloom is already a profile: draft a change from it, or choose another name "
        "for a new one."
    )


# ── where a draft goes, decided once, and what a put does with the answer ───


class _Live:
    """A tiny profile list: profiles are (name, base versions, pinned) by id."""

    def __init__(self) -> None:
        self.profiles = {
            1: ("Espresso", {10, 11}, False),
            2: ("Lungo", {20}, False),
            3: ("espresso v1", set(), True),
            4: ("Espresso V1", {40}, False),
        }

    def label_of(self, profile: int) -> str:
        return self.profiles[profile][0]

    async def by_name_key(self, label: str) -> int | None:
        wanted = name_key(label)
        return next((i for i, p in self.profiles.items() if name_key(p[0]) == wanted), None)

    async def base_profile(self, version_id: int) -> int | None:
        return next((i for i, p in self.profiles.items() if version_id in p[1]), None)

    async def frozen_sentence(self, profile: int) -> str | None:
        return "pinned" if self.profiles[profile][2] else None


async def test_a_change_goes_to_the_profile_its_base_belongs_to() -> None:
    assert await change_destination(_Live(), base_version_id=11, base_label="old") == 1
    assert await change_destination(_Live(), base_version_id=20, base_label="old") == 2


async def test_a_base_on_no_live_profile_goes_to_the_live_profile_with_its_name_else_nowhere() -> (
    None
):
    live = _Live()
    assert await change_destination(live, base_version_id=99, base_label="LUNGO ") == 2
    assert await change_destination(live, base_version_id=99, base_label="Gone") is None
    assert await change_destination(live, base_version_id=99, base_label=None) is None


async def test_a_put_to_a_destination_keeps_its_name_and_a_pinned_one_is_refused() -> None:
    live = _Live()
    placed = await place_draft(live, label="anything", destination=2)
    assert (placed.owner, placed.name, placed.refused) == (2, "Lungo", False)
    frozen = await place_draft(live, label="anything", destination=3)
    assert (frozen.owner, frozen.frozen) == (None, "pinned") and frozen.refused


async def test_a_new_profile_is_refused_only_when_a_live_profile_has_its_name() -> None:
    live = _Live()
    free = await place_draft(live, label=" Fresh ", destination=None)
    assert (free.owner, free.taken, free.name, free.refused) == (None, None, "Fresh", False)
    taken = await place_draft(live, label=" LUNGO", destination=None)
    assert (taken.taken, taken.frozen, taken.refused) == (2, None, True)


async def test_a_name_taken_by_the_pinned_profile_the_draft_comes_from_says_it_is_pinned() -> None:
    live = _Live()
    # The base is a version of the pinned profile: the sentence is about the pin.
    live.profiles[3] = ("espresso v1", {30}, True)
    from_pin = await place_draft(live, label="Espresso v1", destination=None, base_version_id=30)
    assert (from_pin.taken, from_pin.frozen) == (3, "pinned")
    # From another profile it is just a taken name.
    elsewhere = await place_draft(live, label="Espresso v1", destination=None, base_version_id=20)
    assert (elsewhere.taken, elsewhere.frozen) == (3, None)
