"""The one lineage rule: where a draft's version lands, whoever asks."""

from __future__ import annotations

from gaggiclanker.db.repos.lineage import draft_label, lineage_owner

PROFILES = {"X [AI]": "x-profile", "Mine": "mine-profile"}


class _Lookup:
    async def by_set(self, set_id: int) -> str | None:
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


async def test_a_sets_draft_goes_to_the_sets_profile_only_when_that_is_the_name() -> None:
    assert await _owner("Mine", set_id=3) == "Mine"
    # Renamed, it is a profile of its own (or the one that already has that name), never a
    # rename of the Set's profile.
    assert await _owner("Renamed [AI]", set_id=3) is None
    assert await _owner("X [AI]", set_id=3) == "X [AI]"


def test_the_suffix_is_added_to_a_rename_and_to_what_is_written_from_scratch() -> None:
    assert draft_label("Mine", "Mine") == "Mine"  # a change that keeps the profile's name
    assert draft_label("Other", "Mine") == "Other [AI]"  # a rename
    assert draft_label("Mine", None) == "Mine [AI]"  # from scratch, named like an existing one
    assert draft_label("Fresh [AI]", None) == "Fresh [AI]"  # never doubled
