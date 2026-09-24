"""The Claude Code updater: npm resolution, verification, the switch, and its routes.

npm is a ``MockTransport`` and the binary is never run: the manager's ``probe``
seam stands in for ``claude --version``, so these tests check what the
installer decides, not what a real release prints.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import shutil
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from fastapi import FastAPI

from gaggiclanker.infra.outbound import outbound_http_client
from gaggiclanker.llm import claude_cli
from gaggiclanker.llm.claude_cli import (
    ClaudeCliManager,
    installed_binary,
    platform_package,
    resolve_binary,
    valid_target,
)
from gaggiclanker.llm.config import load_llm_config

PACKAGE = "@anthropic-ai/claude-code-linux-x64"
TARBALL = (
    "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-{v}.tgz"
)


def make_tarball(version: str, *, extra: dict[str, bytes] | None = None) -> bytes:
    """A platform package shaped like npm's: the binary under ``package/``."""
    buffer = io.BytesIO()
    members = {"package/claude": f"#!/bin/sh\necho '{version} (Claude Code)'\n".encode()}
    members |= extra or {}
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def sri(payload: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode()


class FakeNpm:
    """The two registry documents and the tarball, with knobs to break each."""

    def __init__(self, tags: dict[str, str] | None = None) -> None:
        self.tags = tags or {"stable": "2.1.273", "latest": "2.1.281"}
        self.tarballs: dict[str, bytes] = {}
        self.integrity: dict[str, str] = {}
        self.tarball_url: Callable[[str], str] = lambda v: TARBALL.format(v=v)
        self.requests: list[str] = []

    def publish(self, version: str, payload: bytes | None = None) -> None:
        payload = payload if payload is not None else make_tarball(version)
        self.tarballs[version] = payload
        self.integrity[version] = sri(payload)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.requests.append(url)
        path = request.url.path
        if path == "/@anthropic-ai/claude-code":
            return httpx2.Response(200, json={"dist-tags": self.tags})
        if path.startswith(f"/{PACKAGE}/-/"):
            version = path.rsplit("-", 1)[1].removesuffix(".tgz")
            return httpx2.Response(200, content=self.tarballs[version])
        if path.startswith(f"/{PACKAGE}/"):
            version = path.rsplit("/", 1)[1]
            if version not in self.tarballs:
                return httpx2.Response(404, json={"error": "not found"})
            dist = {"tarball": self.tarball_url(version), "integrity": self.integrity[version]}
            return httpx2.Response(200, json={"version": version, "dist": dist})
        return httpx2.Response(404)


def manager_for(
    data_dir: Path, npm: FakeNpm, *, runs: bool = True, bundled: str | None = None
) -> ClaudeCliManager:
    """A manager on the fake registry whose probe reads the script's version."""

    async def probe(binary: str) -> str | None:
        if binary == "/image/claude":
            return bundled
        if not runs:
            return None
        return claude_cli.parse_version(Path(binary).read_text())

    return ClaudeCliManager(
        data_dir=str(data_dir),
        client_factory=lambda: outbound_http_client(transport=httpx2.MockTransport(npm.handler)),
        probe=probe,
    )


@pytest.fixture(autouse=True)
def linux_x64_with_an_image_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_cli, "platform_package", lambda: PACKAGE)
    monkeypatch.setattr(shutil, "which", lambda name: "/image/claude")


# -- resolving and installing ----------------------------------------------


async def test_installing_the_stable_channel_switches_to_that_release(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.273")
    manager = manager_for(tmp_path, npm)

    await manager.install("stable")

    assert manager.job.state == "done", manager.job
    assert manager.job.version == "2.1.273"
    binary = tmp_path / "claude-code" / "2.1.273" / "claude"
    assert installed_binary(tmp_path) == binary
    assert (tmp_path / "claude-code" / "current").read_text().strip() == "2.1.273"
    assert resolve_binary("claude", tmp_path) == str(binary)
    # Nothing but the release and the pointer is left behind.
    assert sorted(p.name for p in (tmp_path / "claude-code").iterdir()) == [
        "2.1.273",
        "current",
        "image",
    ]


async def test_an_exact_version_skips_the_channel_lookup(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.250")
    manager = manager_for(tmp_path, npm)

    await manager.install("2.1.250")

    assert manager.job.state == "done"
    assert not any(url.endswith("/@anthropic-ai/claude-code") for url in npm.requests)


async def test_a_new_install_replaces_and_prunes_the_previous_one(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.273")
    npm.publish("2.1.281")
    manager = manager_for(tmp_path, npm)

    await manager.install("stable")
    await manager.install("latest")

    assert installed_binary(tmp_path) == tmp_path / "claude-code" / "2.1.281" / "claude"
    assert not (tmp_path / "claude-code" / "2.1.273").exists()


async def test_a_download_that_fails_its_integrity_check_is_not_installed(
    tmp_path: Path,
) -> None:
    npm = FakeNpm()
    npm.publish("2.1.273")
    npm.publish("2.1.281")
    manager = manager_for(tmp_path, npm)
    await manager.install("stable")
    npm.integrity["2.1.281"] = sri(b"something else")

    await manager.install("latest")

    assert manager.job.state == "failed"
    assert "sha512" in manager.job.message
    # The release that was working stays the one in use, and no staging is left.
    assert installed_binary(tmp_path) == tmp_path / "claude-code" / "2.1.273" / "claude"
    assert sorted(p.name for p in (tmp_path / "claude-code").iterdir()) == [
        "2.1.273",
        "current",
        "image",
    ]


async def test_a_tarball_named_outside_the_registry_is_refused(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.273")
    npm.tarball_url = lambda v: f"https://evil.example/claude-{v}.tgz"
    manager = manager_for(tmp_path, npm)

    await manager.install("stable")

    assert manager.job.state == "failed"
    assert "outside registry.npmjs.org" in manager.job.message
    assert not any("evil.example" in url for url in npm.requests)
    assert installed_binary(tmp_path) is None


async def test_a_binary_that_will_not_run_here_is_not_switched_to(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.273")
    manager = manager_for(tmp_path, npm, runs=False)

    await manager.install("stable")

    assert manager.job.state == "failed"
    assert "would not run" in manager.job.message
    assert installed_binary(tmp_path) is None


async def test_an_unknown_release_fails_with_npms_answer(tmp_path: Path) -> None:
    manager = manager_for(tmp_path, FakeNpm())

    await manager.install("9.9.9")

    assert manager.job.state == "failed"
    assert manager.job.message == "npm has no such release"


async def test_only_the_binary_is_unpacked_from_the_tarball(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish(
        "2.1.273",
        make_tarball("2.1.273", extra={"../../escaped": b"x", "package/README.md": b"hi"}),
    )
    manager = manager_for(tmp_path / "data", npm)

    await manager.install("stable")

    assert manager.job.state == "done"
    assert not (tmp_path / "escaped").exists()
    assert sorted(p.name for p in (tmp_path / "data" / "claude-code" / "2.1.273").iterdir()) == [
        "claude"
    ]


def test_targets_are_a_channel_or_an_exact_version() -> None:
    assert valid_target("stable") and valid_target("latest") and valid_target("2.1.267")
    assert valid_target("2.2.0-beta.1")
    for bad in ("", "next", "--registry=x", "2.1", "../2.1.0", "2.1.0 ; rm"):
        assert not valid_target(bad), bad


# -- which binary runs -------------------------------------------------------


def test_a_custom_binary_setting_wins_over_the_managed_install(tmp_path: Path) -> None:
    (tmp_path / "claude-code" / "2.1.281").mkdir(parents=True)
    binary = tmp_path / "claude-code" / "2.1.281" / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (tmp_path / "claude-code" / "current").write_text("2.1.281\n")

    assert resolve_binary("claude", tmp_path) == str(binary)
    assert resolve_binary("", tmp_path) == str(binary)
    assert resolve_binary("/opt/claude", tmp_path) == "/opt/claude"


def test_a_pointer_that_is_not_a_version_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "claude-code").mkdir()
    (tmp_path / "claude-code" / "current").write_text("../../usr/bin\n")

    assert resolve_binary("claude", tmp_path) == "claude"


async def test_the_llm_config_runs_the_managed_install(tmp_path: Path, settings: Any) -> None:
    npm = FakeNpm()
    npm.publish("2.1.281")
    await manager_for(tmp_path, npm).install("2.1.281")

    config = await load_llm_config(settings, data_dir=str(tmp_path))

    assert config.claude_code_bin == str(tmp_path / "claude-code" / "2.1.281" / "claude")


# -- boot ----------------------------------------------------------------------


async def test_boot_drops_an_install_a_newer_image_has_caught_up_with(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.273")
    await manager_for(tmp_path, npm, bundled="2.1.267").install("stable")

    await manager_for(tmp_path, npm, bundled="2.1.273").reconcile()

    assert installed_binary(tmp_path) is None
    assert not (tmp_path / "claude-code").exists()


async def test_boot_keeps_a_downgrade_made_over_the_same_image(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.250")
    await manager_for(tmp_path, npm, bundled="2.1.267").install("2.1.250")

    await manager_for(tmp_path, npm, bundled="2.1.267").reconcile()

    assert installed_binary(tmp_path) == tmp_path / "claude-code" / "2.1.250" / "claude"


async def test_boot_keeps_an_install_newer_than_the_image(tmp_path: Path) -> None:
    npm = FakeNpm()
    npm.publish("2.1.281")
    await manager_for(tmp_path, npm).install("latest")
    (tmp_path / "claude-code" / ".staging-2.1.290-1").mkdir()

    await manager_for(tmp_path, npm, bundled="2.1.267").reconcile()

    assert installed_binary(tmp_path) is not None
    assert sorted(p.name for p in (tmp_path / "claude-code").iterdir()) == [
        "2.1.281",
        "current",
        "image",
    ]


def test_the_platform_package_follows_the_wrappers_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    assert platform_package("Linux", "x86_64", "glibc") == "@anthropic-ai/claude-code-linux-x64"
    assert platform_package("Linux", "aarch64", "glibc") == "@anthropic-ai/claude-code-linux-arm64"
    assert platform_package("Linux", "x86_64", "") == "@anthropic-ai/claude-code-linux-x64-musl"
    assert platform_package("Darwin", "arm64") == "@anthropic-ai/claude-code-darwin-arm64"
    assert platform_package("Windows", "AMD64") is None
    assert platform_package("Linux", "riscv64", "glibc") is None


# -- the routes ----------------------------------------------------------------


def use_fake_npm(app: FastAPI, data_dir: Path, npm: FakeNpm) -> ClaudeCliManager:
    manager = manager_for(data_dir, npm, bundled="2.1.267")
    app.state.claude_cli = manager
    return manager


async def poll(client: httpx.AsyncClient) -> dict[str, Any]:
    for _ in range(200):
        data: dict[str, Any] = (await client.get("/api/llm/claude-cli")).json()["data"]
        if data["job"]["state"] != "running":
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("the install never finished")


async def test_the_routes_install_report_and_revert(
    app: FastAPI, client: httpx.AsyncClient, data_dir: Path
) -> None:
    npm = FakeNpm()
    npm.publish("2.1.281")
    use_fake_npm(app, data_dir, npm)

    before = (await client.get("/api/llm/claude-cli")).json()["data"]
    assert before["bundled"] == {"path": "/image/claude", "version": "2.1.267"}
    assert before["managed"] == {"path": None, "version": None}
    assert before["channels"] == {"stable": "2.1.273", "latest": "2.1.281"}
    assert before["active_binary"] == "claude"

    started = await client.post("/api/llm/claude-cli/install", json={"version": "latest"})
    assert started.status_code == 202
    assert started.json()["data"]["job"]["state"] == "running"

    after = await poll(client)
    assert after["job"]["state"] == "done", after["job"]
    assert after["managed"]["version"] == "2.1.281"
    assert after["active_binary"] == after["managed"]["path"]

    status = (await client.get("/api/llm/status")).json()["data"]
    assert status["claude_code"]["binary"] == after["managed"]["path"]

    reverted = await client.delete("/api/llm/claude-cli")
    assert reverted.json()["data"]["managed"]["version"] is None
    assert reverted.json()["data"]["active_binary"] == "claude"


async def test_a_second_install_while_one_runs_is_a_conflict(
    app: FastAPI, client: httpx.AsyncClient, data_dir: Path
) -> None:
    manager = use_fake_npm(app, data_dir, FakeNpm())
    manager.begin("stable")

    response = await client.post("/api/llm/claude-cli/install", json={"version": "stable"})
    assert response.status_code == 409
    assert (await client.delete("/api/llm/claude-cli")).status_code == 409


async def test_an_invalid_version_is_refused_without_echoing_it(
    app: FastAPI, client: httpx.AsyncClient, data_dir: Path
) -> None:
    use_fake_npm(app, data_dir, FakeNpm())

    response = await client.post(
        "/api/llm/claude-cli/install", json={"version": "--registry=http://x"}
    )

    assert response.status_code == 400
    assert "registry" not in json.dumps(response.json()["error"]["details"])


async def test_a_custom_binary_setting_is_reported_as_overriding(
    app: FastAPI, client: httpx.AsyncClient, data_dir: Path
) -> None:
    use_fake_npm(app, data_dir, FakeNpm())
    await client.patch("/api/settings", json={"claudeCodeBin": "/opt/claude"})

    data = (await client.get("/api/llm/claude-cli")).json()["data"]

    assert data["overridden"] is True
    assert data["active_binary"] == "/opt/claude"
