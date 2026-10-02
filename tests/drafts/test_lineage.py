"""The one lineage rule: where a draft's version lands, whoever asks."""

from __future__ import annotations

from gaggiclanker.db.repos.lineage import lineage_owner


class _Lookup:
    async def by_set(self, set_id: int) -> str | None:
        return "set-profile"

    async def by_version(self, version_id: int) -> str | None:
        return "base-profile"

    async def by_device(self, device_id: str) -> str | None:
        return None

    def is_app_made(self, profile: str) -> bool:
        return True


async def _owner(**over: object) -> str | None:
    args: dict[str, object] = {
        "set_id": None,
        "base_label": "X [AI]",
        "version_label": "X [AI]",
        "base_version_id": 1,
        "base_device_profile_id": None,
    }
    args.update(over)
    return await lineage_owner(_Lookup(), **args)  # type: ignore[arg-type]


async def test_an_exact_label_continues_the_base_profile() -> None:
    assert await _owner() == "base-profile"
    assert await _owner(version_label="Y [AI]") is None


async def test_a_new_draft_never_continues_its_stored_base_even_with_the_same_label() -> None:
    assert await _owner(is_new=True) is None


async def test_a_set_chain_is_followed_whatever_the_labels_say() -> None:
    assert await _owner(set_id=3, version_label="Renamed [AI]") == "set-profile"
