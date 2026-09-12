"""`/api/profiles` and `/api/profile-versions` — the mirror of the machine's `/p/`.

Read-only, and it stays read-only until profile push is built: the prototype
writes nothing to the device, and a profile with zero phases crashes brew start
on the display.

Two resources because a profile has two identities. `/api/profiles` is *where* a
profile lives — a device id, its star, its position, whether it is selected.
`/api/profile-versions` is *what* it brews — an immutable, content-hashed
document that a shot from March still resolves to after the profile on the
machine has been edited twice.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from gaggiclanker.api.deps import ProfilesRepoDep
from gaggiclanker.db.repos.profiles import (
    DeviceProfileSummary,
    ProfileVersionRow,
    ProfileVersionSummary,
)
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound

__all__ = ["router", "versions_router"]

router = APIRouter(prefix="/profiles", tags=["profiles"])
versions_router = APIRouter(prefix="/profile-versions", tags=["profiles"])


class ProfileListData(BaseModel):
    """Every profile the machine has, with its current version's summary."""

    model_config = ConfigDict(extra="forbid")

    items: list[DeviceProfileSummary]


class ProfileVersionListData(BaseModel):
    """One page of profile versions, newest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ProfileVersionSummary]
    total: int
    limit: int
    offset: int


class ProfileDetailData(BaseModel):
    """One device profile plus the full version document behind it."""

    model_config = ConfigDict(extra="forbid")

    profile: DeviceProfileSummary
    version: ProfileVersionRow


@router.get(
    "",
    response_model=ApiResponse[ProfileListData],
    summary="The profiles mirrored from the machine",
)
async def list_profiles(
    profiles: ProfilesRepoDep,
    machine_id: Annotated[int | None, Query()] = None,
    include_deleted: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """Ordered as the machine orders them: its own `profileOrder`, then label.

    Deleted profiles are hidden by default and never actually removed —
    `include_deleted=true` brings back the tombstones, because "the profile I
    used in March" has to keep resolving after somebody deletes it from the
    display.
    """
    items = await profiles.list_device_profiles(machine_id, include_deleted=include_deleted)
    return envelope_response(ProfileListData(items=items).model_dump(mode="json"))


@router.get(
    "/{device_id}",
    response_model=ApiResponse[ProfileDetailData],
    summary="One mirrored profile and its current version",
)
async def get_profile(
    device_id: str,
    profiles: ProfilesRepoDep,
    machine_id: Annotated[int | None, Query()] = None,
) -> JSONResponse:
    summary = await profiles.get_device_profile_summary(device_id, machine_id)
    if summary is None:
        raise NotFound(f"No mirrored profile {device_id!r}")
    version = await profiles.get_version(summary.current_version_id)
    if version is None:  # pragma: no cover - the join in the summary guarantees it
        raise NotFound(f"Profile {device_id!r} points at a version that is not stored")
    return envelope_response(
        ProfileDetailData(profile=summary, version=version).model_dump(mode="json")
    )


#: A page bigger than this is a scrape, not a screen. The same reasoning as
#: `shots.MAX_LIMIT`, and the same number, so the two cannot drift apart in a
#: reader's head.
MAX_LIMIT = 500


@versions_router.get(
    "",
    response_model=ApiResponse[ProfileVersionListData],
    summary="Every stored profile version, mirrored or imported",
)
async def list_profile_versions(
    profiles: ProfilesRepoDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    source: Annotated[Literal["device", "import"] | None, Query()] = None,
) -> JSONResponse:
    """Newest first, with `mirrored` saying whether the machine still has it.

    `/api/profiles` lists what is on the machine right now. This lists what the
    archive can resolve a shot to, which is a superset: a profile edited on the
    display leaves its previous version behind, and a version imported from a
    file never had a device profile at all. Offset paging rather than a cursor —
    versions are inserted rarely and the list is short enough to page by number.
    """
    page = await profiles.list_versions(limit=limit, offset=offset, source=source)
    return envelope_response(
        ProfileVersionListData(
            items=page.items, total=page.total, limit=limit, offset=offset
        ).model_dump(mode="json")
    )


@versions_router.get(
    "/{version_id}",
    response_model=ApiResponse[ProfileVersionRow],
    summary="One immutable profile version",
)
async def get_profile_version(version_id: int, profiles: ProfilesRepoDep) -> JSONResponse:
    version = await profiles.get_version(version_id)
    if version is None:
        raise NotFound(f"No profile version {version_id}")
    return envelope_response(version.model_dump(mode="json"))
