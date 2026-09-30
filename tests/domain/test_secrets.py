"""`without_secrets`: what may never be kept from a document the machine sent."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gaggiclanker.domain.secrets import SECRET_KEYS, is_secret_key, without_secrets


def test_the_firmwares_three_credentials_are_removed_not_masked() -> None:
    document = {
        "pid": "1,2,3",
        "wifiPassword": "w",
        "apPassword": "a",
        "haPassword": "h",
        "temperatureOffset": 2.5,
    }

    assert without_secrets(document) == {"pid": "1,2,3", "temperatureOffset": 2.5}


def test_the_closed_list_names_exactly_what_the_firmware_sends() -> None:
    assert set(SECRET_KEYS) == {"wifiPassword", "apPassword", "haPassword"}


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "PASSWORD",
        "WifiPassword",
        "mqttPassWord",
        "authToken",
        "api_token",
        "Secret",
        "clientSecret",
        "token",
        "passwordHint",
    ],
)
def test_any_key_naming_a_password_token_or_secret_goes_whatever_its_case(name: str) -> None:
    assert is_secret_key(name)
    assert without_secrets({name: "x", "keep": 1}) == {"keep": 1}


@pytest.mark.parametrize(
    "name", ["wifiSsid", "mdnsName", "pid", "brewDelay", "homekit", "startupMode", "pass", "tok"]
)
def test_other_keys_are_untouched(name: str) -> None:
    assert not is_secret_key(name)
    assert without_secrets({name: "x"}) == {name: "x"}


def test_secrets_are_removed_at_every_depth_including_inside_lists() -> None:
    document: dict[str, Any] = {
        "wifi": {"ssid": "home", "Password": "p", "deeper": {"apiToken": "t", "ok": [1, 2]}},
        "networks": [{"ssid": "a", "secret": "s"}, {"ssid": "b"}, 3, "text"],
        "list": [[{"token": "t", "n": 1}]],
    }

    assert without_secrets(document) == {
        "wifi": {"ssid": "home", "deeper": {"ok": [1, 2]}},
        "networks": [{"ssid": "a"}, {"ssid": "b"}, 3, "text"],
        "list": [[{"n": 1}]],
    }


def test_the_argument_is_not_modified() -> None:
    document = {"a": {"wifiPassword": "w", "b": 1}, "l": [{"token": "t"}]}
    before = copy.deepcopy(document)

    without_secrets(document)

    assert document == before


def test_scalars_and_empty_documents_pass_through() -> None:
    assert without_secrets({}) == {}
    assert without_secrets(None) is None
    assert without_secrets("password") == "password"
    assert without_secrets(5) == 5


def test_a_secret_key_is_removed_whatever_its_value() -> None:
    """The firmware sends `haPassword: ""` whenever Home Assistant is unused."""
    document = {"haPassword": "", "wifiPassword": "", "apPassword": None, "token": 0, "n": 0}

    assert without_secrets(document) == {"n": 0}
