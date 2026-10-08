"""The one lineage rule: where a draft's version lands, whoever asks."""

from __future__ import annotations

from gaggiclanker.db.repos.lineage import lineage_owner, taken_name_sentence

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
    assert await _owner("Renamed", set_id=3) is None
    assert await _owner("X [AI]", set_id=3) == "X [AI]"


def test_the_refusal_names_the_profile_and_says_what_to_do() -> None:
    assert taken_name_sentence("Adaptive Bloom") == (
        "Adaptive Bloom is already a profile: draft a change from it, or choose another name "
        "for a new one."
    )
