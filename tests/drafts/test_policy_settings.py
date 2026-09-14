"""The safety policy's bounds are settings, and a setting is a text box.

The point of layer 2 is that it is **narrower** than the firmware. A bound a
person can widen from the Settings page to the firmware's own limit is a layer
configured to allow everything the layer below it allows, which is a layer that
does nothing — and it fails silently, because `bounds_from` falls back per key
on an unusable value, so a bad bound that was still *stored* reads as configured
and behaves as the default.

So every one of the seven keys is validated on write, and the two that come in
pairs are checked against each other. The eight cases below are the ones that
were accepted before this file existed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.domain.profile_policy import POLICY_SETTING_KEYS
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.settings import SETTINGS_REGISTRY
from tests.drafts.conftest import data, error


async def patch(client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    return await client.patch("/api/settings", json=body)


def test_every_policy_key_carries_a_validator() -> None:
    """A key added to the registry without one is a bound with no floor."""
    for key in POLICY_SETTING_KEYS:
        assert SETTINGS_REGISTRY[key].validate is not None, key


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("profilePolicyTemperatureMaxC", 200, "narrower than the firmware"),
        ("profilePolicyTemperatureMinC", -10, "narrower than the firmware"),
        ("profilePolicyPhaseDurationMaxS", 900, "narrower than the firmware"),
        ("profilePolicyPhaseDurationMinS", 0, "narrower than the firmware"),
        ("profilePolicyPressureMaxBar", 50, "narrower than the firmware"),
        ("profilePolicyFlowMaxMlS", 40, "narrower than the firmware"),
        ("profilePolicyMaxPhases", 0, "at least one phase"),
        ("profilePolicyMaxPhases", -3, "at least one phase"),
    ],
    ids=[
        "200-degrees",
        "minus-ten-degrees",
        "nine-hundred-second-phase",
        "zero-second-phase",
        "fifty-bar",
        "forty-ml-per-second",
        "zero-phases",
        "minus-three-phases",
    ],
)
async def test_a_bound_outside_the_firmware_s_own_limits_is_refused(
    client: httpx.AsyncClient, key: str, value: Any, expected: str
) -> None:
    """The eight the review found. Each names the field and what it wants."""
    response = await patch(client, {key: value})

    assert response.status_code == 400, response.text
    body = error(response)
    assert body["details"][0]["field"] == key
    assert expected in body["details"][0]["message"]

    # And nothing was stored: `apply` validates the whole body before writing.
    settings = data(await client.get("/api/settings"))
    assert settings[key]["source"] == "default"


async def test_an_inverted_temperature_pair_is_refused(client: httpx.AsyncClient) -> None:
    """`clamp` cannot clamp into an empty range, so it stops clamping entirely.

    A per-key validator cannot see this: 120 °C is a legitimate *maximum* and an
    absurd minimum, and which it is depends on its sibling.
    """
    response = await patch(client, {"profilePolicyTemperatureMinC": 120})

    assert response.status_code == 400
    assert "must not be above the maximum" in error(response)["details"][0]["message"]


async def test_an_inverted_duration_pair_is_refused(client: httpx.AsyncClient) -> None:
    response = await patch(client, {"profilePolicyPhaseDurationMinS": 200})

    assert response.status_code == 400
    assert "must not be longer than the longest" in error(response)["details"][0]["message"]


async def test_both_halves_of_a_pair_may_move_at_once(client: httpx.AsyncClient) -> None:
    """The whole reason the check is on the effective value rather than the body.

    Raising the minimum past the *current* maximum is refused; raising both in
    one PATCH is how you get there, and it has to work or the bounds are
    unmovable upwards.
    """
    accepted = data(
        await patch(
            client,
            {"profilePolicyTemperatureMinC": 88, "profilePolicyTemperatureMaxC": 96},
        )
    )
    assert accepted["profilePolicyTemperatureMinC"]["value"] == 88.0
    assert accepted["profilePolicyTemperatureMaxC"]["value"] == 96.0


async def test_tightening_a_bound_is_still_allowed(client: httpx.AsyncClient) -> None:
    """Narrower is the direction this is for. crema's own ceiling is 96 °C."""
    accepted = data(await patch(client, {"profilePolicyTemperatureMaxC": 96}))
    assert accepted["profilePolicyTemperatureMaxC"]["value"] == 96.0
    assert accepted["profilePolicyTemperatureMaxC"]["source"] == "database"


async def test_a_refused_bound_never_reaches_the_policy(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The end the validation is for: the layer's own bounds are unchanged.

    Asserted through the object that builds drafts rather than through the
    settings API, because "the PATCH was refused" and "the layer still holds"
    are different claims and it is the second one that matters. Through
    `DraftProposals` rather than through the route-facing service because that
    is where a bound is read now — it is the half a chat tool proposes with, and
    a widened bound that reached only it would be the dangerous one.
    """
    app, client = live
    proposals: DraftProposals = app.state.draft_proposals
    before = await proposals.bounds()

    assert (await patch(client, {"profilePolicyTemperatureMaxC": 200})).status_code == 400

    assert (await proposals.bounds()) == before
    # The service the routes use reads the same object, so it moved either.
    assert (await app.state.drafts.bounds()) == before
