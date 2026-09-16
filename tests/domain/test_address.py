"""The machine's address: what a person types, reduced to what the client builds URLs from."""

import pytest

from gaggiclanker.domain.address import host_problem, machine_host


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("192.168.1.50", "192.168.1.50"),
        ("  192.168.1.50  ", "192.168.1.50"),
        ("http://192.168.1.50", "192.168.1.50"),
        ("http://192.168.1.50/", "192.168.1.50"),
        ("HTTP://192.168.1.50/", "192.168.1.50"),
        ("ws://127.0.0.1:8080", "127.0.0.1:8080"),
        ("gaggimate.local", "gaggimate.local"),
        ("127.0.0.1:8090/", "127.0.0.1:8090"),
        ("[fe80::1]:8080", "[fe80::1]:8080"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_machine_host_reduces_a_typed_address_to_host_and_port(typed: str, expected: str) -> None:
    assert machine_host(typed) == expected
    assert machine_host(machine_host(typed)) == expected


@pytest.mark.parametrize(
    "typed", ["", "192.168.1.50", "http://192.168.1.50/", "ws://127.0.0.1:8080", "gaggimate.local"]
)
def test_usable_addresses_have_no_problem(typed: str) -> None:
    assert host_problem(typed) is None


@pytest.mark.parametrize(
    "typed",
    [
        "https://192.168.1.50",
        "wss://192.168.1.50",
        "ftp://192.168.1.50",
        "http://",
        "192.168.1.50/gaggimate",
        "http://192.168.1.50/api/settings",
        "192.168.1.50?x=1",
        "user:secret@192.168.1.50",
    ],
)
def test_unusable_addresses_are_refused_without_quoting_them(typed: str) -> None:
    problem = host_problem(typed)
    assert problem is not None
    assert "192.168" not in problem and "secret" not in problem
