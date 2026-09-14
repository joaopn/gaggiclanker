"""Fixtures for profile drafts and push: a fake machine, a mirrored archive, and a scripted model.

The app is the real app with the real lifespan, pointed at
:class:`~gaggiclanker.device.fake.FakeDevice` through `GAGGIMATE_HOST`. That
matters more here than anywhere else in the suite: the thing under test is a
*write* to a machine, and a mocked client would test the mock. The fake speaks
the real wire protocol, generates ids the way `generateShortID` does,
auto-favourites new profiles the way `saveProfile` does, and serialises what it
stored the way `writeProfile` does — which is what the round-trip check is
actually compared against.

Writes are **off** in every fixture here except :func:`writes_on`, because off is
the shipped default and a suite that turned them on in a session fixture would
never notice if the default moved.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice, build_fake_device
from gaggiclanker.domain.models import Profile
from gaggiclanker.drafts.service import ProfileDraftService
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.llm.conftest import FakeProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: The profile every push test starts from. One phase, a volumetric target, a
#: pressure-controlled pump — the simplest real profile the firmware ships, so a
#: failing assertion is about the push rather than about the profile.
BASE_LABEL = "9 Bar Espresso"


def profile_fixture(name: str) -> dict[str, Any]:
    """One shipped profile document, straight off disk."""
    return json.loads((FIXTURES / "profiles" / f"{name}.json").read_text())  # type: ignore[no-any-return]


def every_profile_fixture() -> list[tuple[str, dict[str, Any]]]:
    """Every profile this repository ships, for the parametrised policy tests."""
    return [
        (path.stem, json.loads(path.read_text()))
        for path in sorted((FIXTURES / "profiles").glob("*.json"))
    ]


@pytest.fixture
async def fake_device() -> AsyncIterator[FakeDevice]:
    device = build_fake_device(FIXTURES)
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
def provider() -> FakeProvider:
    """The scripted model. A test sets `provider.script` to what it wants back."""
    return FakeProvider()


@pytest.fixture
async def live(
    env: EnvSettings,
    fake_device: FakeDevice,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """The app, connected to the fake machine, with a scripted model behind it."""
    monkeypatch.setenv("GAGGIMATE_HOST", fake_device.address)
    monkeypatch.setenv("GAGGIMATE_TIMEOUT_S", "5")
    async with running_app(env) as (app, client):
        # The lifespan starts the device client's socket in the background and
        # returns without waiting for it, and `sync_profiles` refuses to send on
        # a socket that is not up yet. On an idle machine the handshake nearly
        # always wins that race; with a dozen test workers starting at once it
        # often does not, the mirror stays empty, and the test fails far from
        # the cause with "the mirror has no profile labelled ...". So wait for
        # the socket, and refuse to hand a test an app whose mirror failed.
        assert await app.state.connection.client.wait_connected(5.0), (
            "the fake machine did not connect"
        )
        _rewire_llm(app, provider)
        # The profile mirror is what a draft is based on, and the machine row is
        # what the push writes the mirror back through. Both come from one sync.
        run = await app.state.connection.engine.sync_profiles(trigger="test")
        assert run.status == "ok", f"the profile mirror failed: {run.error}"
        yield app, client


def _rewire_llm(app: FastAPI, provider: FakeProvider) -> None:
    """Point the app's LLM service — and the draft service holding it — at the fake.

    The same substitution `tests/analyzer/test_api.py` makes, and for the same
    reason: both services are app-scoped, so replacing `app.state.llm` alone
    would leave the draft service talking to the real provider factory.
    """
    app.state.llm = LlmService(
        app.state.settings_service,
        observer=app.state.llm.observer,
        budget=RateLimitBudget(retries=0),
        mode_memory=ModeMemory(),
        provider_factory=lambda _config, _name: provider,
    )
    app.state.drafts = ProfileDraftService(
        app.state.db,
        app.state.llm,
        PromptService(app.state.drafts.prompts.repo),
        app.state.settings_service,
        connection=app.state.connection,
    )


@pytest.fixture
async def writes_on(live: tuple[FastAPI, httpx.AsyncClient]) -> tuple[FastAPI, httpx.AsyncClient]:
    """The same app with `deviceWritesEnabled` turned on, as a person would.

    Through the settings service rather than by poking the object, so the test
    exercises the same precedence chain the Settings page does.
    """
    app, client = live
    await app.state.settings_service.apply({"deviceWritesEnabled": True})
    return app, client


async def base_version_id(app: FastAPI, label: str = BASE_LABEL) -> int:
    """The mirrored version id of a profile, by label."""
    version = await ProfilesRepository(app.state.db).find_version_by_label(label)
    assert version is not None, f"the mirror has no profile labelled {label!r}"
    return version.id


async def base_profile(app: FastAPI, label: str = BASE_LABEL) -> Profile:
    version = await ProfilesRepository(app.state.db).find_version_by_label(label)
    assert version is not None
    return Profile.model_validate(version.profile)


def data(response: httpx.Response) -> Any:
    assert response.status_code < 400, response.text
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


def error(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert body["ok"] is False, body
    return dict(body["error"])
