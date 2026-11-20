"""A document the model wrote inherits its base's dose; one a person supplied keeps what they gave.

The model's output schema leaves `dose` out (the agent is not asked for one), so every whole
document it writes arrives with none. A draft of a profile that has a dose, and a ratio stop that
needs it, would otherwise lose the dose and brew something else. Each half is shown with the rule
taken away.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.profile_policy import check
from gaggiclanker.drafts import service as drafts_service
from gaggiclanker.drafts.proposals import with_the_bases_dose
from tests.drafts.conftest import base_profile, data
from tests.drafts.helpers import forked
from tests.llm.conftest import FakeProvider

DOSE = 18.0


async def base_with_a_dose_and_a_ratio_stop(app: FastAPI) -> tuple[int, Profile]:
    """A version of the 9 bar profile with a dose and a ratio stop in its last phase."""
    document = (await base_profile(app)).model_dump(mode="json", exclude={"annotations", "id"})
    document["label"] = "Ratio base"
    document["dose"] = DOSE
    document["phases"][-1]["targets"] = [{"type": "ratio", "operator": "gte", "value": 2.0}]
    profile = Profile.model_validate(document)
    version, _ = await ProfilesRepository(app.state.db).ensure_version(profile, owner=None)
    return version.id, profile


def the_models_document(base: Profile) -> dict[str, Any]:
    """What the model returns: the whole base, as its schema describes it (no dose), under a
    name of its own."""
    document = base.model_dump(mode="json", exclude={"annotations", "id", "dose"})
    document["label"] = forked(base.label)
    document["temperature"] = 92.0
    return document


async def test_a_draft_the_model_wrote_keeps_its_bases_dose_and_its_ratio_stop(
    live: tuple[FastAPI, httpx.AsyncClient],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = live
    version_id, base = await base_with_a_dose_and_a_ratio_stop(app)
    reply = json.dumps({"profile": the_models_document(base), "change_summary": "cooler"})
    provider.script = [reply, reply]

    # Without the rule the draft loses the dose, and the ratio stop with it ends nothing.
    with monkeypatch.context() as without:
        without.setattr(drafts_service, "with_the_bases_dose", lambda _base, drafted, **_: drafted)
        lost = data(
            await client.post(
                "/api/profile-drafts", json={"base_version_id": version_id, "notes": "cooler"}
            )
        )
    stored = await ProfilesRepository(app.state.db).get_version(lost["draft_version_id"])
    assert stored is not None and stored.profile is not None
    assert "dose" not in stored.profile

    kept = data(
        await client.post(
            "/api/profile-drafts", json={"base_version_id": version_id, "notes": "cooler"}
        )
    )
    stored = await ProfilesRepository(app.state.db).get_version(kept["draft_version_id"])
    assert stored is not None and stored.profile is not None
    assert stored.profile["dose"] == DOSE
    assert stored.profile["temperature"] == 92.0
    assert stored.profile["phases"][-1]["targets"] == [
        {"type": "ratio", "operator": "gte", "value": 2.0}
    ]
    assert kept["clamp_changes"] == []
    # The stored document passes the policy, and the stop still ends it.
    assert check(Profile.model_validate(stored.profile)) == []


async def test_a_refinement_keeps_the_dose_too(
    live: tuple[FastAPI, httpx.AsyncClient], provider: FakeProvider
) -> None:
    app, client = live
    version_id, base = await base_with_a_dose_and_a_ratio_stop(app)
    reply = json.dumps({"profile": the_models_document(base), "change_summary": "cooler"})
    provider.script = [reply, reply]
    first = data(
        await client.post(
            "/api/profile-drafts", json={"base_version_id": version_id, "notes": "cooler"}
        )
    )

    refined = data(
        await client.post(f"/api/profile-drafts/{first['id']}/refine", json={"notes": "again"})
    )

    stored = await ProfilesRepository(app.state.db).get_version(refined["draft_version_id"])
    assert stored is not None and stored.profile is not None
    assert stored.profile["dose"] == DOSE


async def test_a_document_a_person_supplied_keeps_exactly_what_they_gave(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = live
    version_id, base = await base_with_a_dose_and_a_ratio_stop(app)
    document = the_models_document(base)
    document["phases"][-1]["targets"] = [{"type": "volumetric", "operator": "gte", "value": 36}]

    draft = await app.state.drafts.create_manual(
        base_version_id=version_id,
        document=document,
    )

    stored = await ProfilesRepository(app.state.db).get_version(draft.draft_version_id or 0)
    assert stored is not None and stored.profile is not None
    assert "dose" not in stored.profile, "the person removed the dose by leaving it out"


def test_an_own_dose_stays_and_a_source_without_one_gives_none() -> None:
    source = Profile.model_validate({**_minimal(), "dose": 18})
    bare = Profile.model_validate(_minimal())
    assert with_the_bases_dose(source, bare).dose == 18
    assert with_the_bases_dose(bare, bare).dose is None
    own = Profile.model_validate({**_minimal(), "dose": 20})
    assert with_the_bases_dose(source, own).dose == 20
    zero = Profile.model_validate({**_minimal(), "dose": 0})
    assert with_the_bases_dose(source, zero).dose == 0, "a dose the document states, 0 included"


async def test_a_refinement_keeps_the_dose_a_person_set_in_the_draft_not_the_bases(
    live: tuple[FastAPI, httpx.AsyncClient],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model refines the draft it is shown, so a dose of 20 in that draft survives a
    refinement whose base had 18; a first draft still takes the base's 18."""
    app, client = live
    version_id, base = await base_with_a_dose_and_a_ratio_stop(app)
    reply = json.dumps({"profile": the_models_document(base), "change_summary": "cooler"})
    provider.script = [reply]
    first = data(
        await client.post(
            "/api/profile-drafts", json={"base_version_id": version_id, "notes": "cooler"}
        )
    )
    versions = ProfilesRepository(app.state.db)
    assert (await versions.get_version(first["draft_version_id"])).profile["dose"] == DOSE  # type: ignore[union-attr,index]

    # The person sets 20 in the JSON editor: a draft of their own.
    edited = the_models_document(base) | {"dose": 20, "label": forked(base.label) + " 2"}
    person = await app.state.drafts.create_manual(base_version_id=version_id, document=edited)
    stored = await versions.get_version(person.draft_version_id or 0)
    assert stored is not None and stored.profile is not None and stored.profile["dose"] == 20

    provider.script = [reply, reply]
    # Taking the dose from the base (the round-two behaviour) would give 18 here.
    with monkeypatch.context() as from_base:
        from_base.setattr(
            type(app.state.drafts), "_given_document", lambda _self, b, _parent: _async(b)
        )
        wrong = data(
            await client.post(f"/api/profile-drafts/{person.id}/refine", json={"notes": "again"})
        )
    again = await versions.get_version(wrong["draft_version_id"])
    assert again is not None and again.profile is not None and again.profile["dose"] == DOSE

    person = await app.state.drafts.create_manual(
        base_version_id=version_id, document=edited | {"label": forked(base.label) + " 3"}
    )
    refined = data(
        await client.post(f"/api/profile-drafts/{person.id}/refine", json={"notes": "again"})
    )
    kept = await versions.get_version(refined["draft_version_id"])
    assert kept is not None and kept.profile is not None and kept.profile["dose"] == 20


async def _async(value: Profile) -> Profile:
    return value


def _minimal() -> dict[str, Any]:
    return {
        "label": "x",
        "type": "standard",
        "temperature": 93,
        "phases": [
            {
                "name": "p",
                "phase": "brew",
                "valve": 1,
                "duration": 28,
                "pump": 100,
                "targets": [{"type": "ratio", "operator": "gte", "value": 2}],
            }
        ],
    }
