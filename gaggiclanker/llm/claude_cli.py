"""Updating the Claude Code CLI from the settings page, without rebuilding the image.

The image carries one pinned ``claude`` binary (the Dockerfile's
``CLAUDE_CODE_VERSION``). Claude Code ships often, and a rebuild just to take a
new release is a lot to ask of a box that otherwise only ever pulls. So a
person can install a different release from Settings → LLM, the way cvclanker's
Claude Code panel does; this module is that installer.

It cannot work the way cvclanker's does. cvclanker runs ``npm install -g`` in
its container; this runtime deliberately has no node, and ``/usr/local/bin`` is
root's while the app runs unprivileged with an empty capability set. What npm
would have done is small, though: the ``@anthropic-ai/claude-code`` package is
a wrapper whose postinstall picks a per-platform package
(``@anthropic-ai/claude-code-linux-x64`` and so on), and that package's tarball
holds one self-contained binary. So the installer does exactly that, in Python:

* resolve the requested channel (``stable``, ``latest``) or exact version
  against the wrapper package's dist-tags, so "stable" means what npm means;
* fetch the platform package's metadata for that version and download its
  tarball from the registry, through the same outbound client as every other
  external call (no ``.netrc``, no credentialed proxy, no redirects);
* check the tarball against the registry's ``sha512`` integrity before
  anything is unpacked, and refuse one larger than any real release;
* unpack only ``package/claude`` into ``DATA_DIR/claude-code/<version>/``, and
  run it (``--version``) before switching to it — a binary that will not start
  there (a ``noexec`` mount, the wrong libc) is refused instead of installed.

The install lives under ``DATA_DIR`` because that is the one place the app may
write and the one place that survives a container recreate. The switch is a
one-line ``current`` file, replaced atomically, so a crash mid-install leaves
the previous binary in use. A call already running keeps its old binary: on
Linux an unlinked executable stays alive until its process exits.

**Which binary runs.** The managed install replaces the image's only while the
``claudeCodeBin`` setting is left at its default, ``claude``: a person who
pointed that setting at a binary of their own chose it, and an update here must
not quietly override them. And the image wins again once it catches up: at boot,
when the image's own binary has changed since the install and is at least as
new, the install is removed, so pulling an image that bumped the pin does not
leave the box on an older release installed by hand months ago. An install
older than the image it was made on is a deliberate downgrade (a release that
broke the provider) and survives restarts until the image moves.

Nothing here runs on its own. The CLI's auto-updater stays disabled in every
child (see ``providers/claude_code.py``), and there is no timer: an update is a
button a person presses, because the provider parses this CLI's flags and JSON
envelope and a new release is a change to test, not a background event.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import platform
import re
import shutil
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx2
import structlog

from gaggiclanker.infra.outbound import outbound_http_client
from gaggiclanker.llm.providers.claude_code import ClaudeCodeProvider

__all__ = [
    "CHANNELS",
    "DEFAULT_BINARY",
    "ClaudeCliManager",
    "InstallError",
    "installed_binary",
    "parse_version",
    "resolve_binary",
    "valid_target",
]

log = structlog.get_logger(__name__)

#: The ``claudeCodeBin`` default: the image's copy, found on PATH.
DEFAULT_BINARY = "claude"
WRAPPER_PACKAGE = "@anthropic-ai/claude-code"
REGISTRY = "https://registry.npmjs.org"
#: npm's own dist-tags on the wrapper package. ``stable`` is what the image pins.
CHANNELS: tuple[str, ...] = ("stable", "latest")
#: The directory under DATA_DIR, and the pointer file inside it.
INSTALL_DIR = "claude-code"
CURRENT_FILE = "current"
#: The image's own version when the install was made; see ``reconcile``.
IMAGE_FILE = "image"
BINARY_NAME = "claude"
#: Inside the platform package's tarball.
TARBALL_MEMBER = "package/claude"

#: A release is ~230 MB unpacked and ~70 MB packed (2.1.x). Ten times that is a
#: generous ceiling that still stops a runaway download from filling the disk
#: the archive lives on.
MAX_TARBALL_BYTES = 800 * 1024 * 1024
MAX_BINARY_BYTES = 1500 * 1024 * 1024
#: The metadata requests are small JSON documents; the download is not.
REGISTRY_TIMEOUT_S = 10.0
DOWNLOAD_TIMEOUT_S = 600.0
#: How long a dist-tag lookup is reused. The panel asks on every settings load,
#: and npm is not something to hit on every render.
TAGS_TTL_S = 600.0

_VERSION = re.compile(r"^\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$")
_VERSION_IN_TEXT = re.compile(r"\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?")

JobState = Literal["idle", "running", "done", "failed"]


class InstallError(Exception):
    """An install that could not finish, in words the settings page can show."""


def valid_target(target: str) -> bool:
    """A channel name or an exact version; nothing else reaches a registry URL."""
    return target in CHANNELS or bool(_VERSION.match(target))


def parse_version(text: str) -> str | None:
    """The version in ``claude --version`` output (``2.1.267 (Claude Code)``)."""
    match = _VERSION_IN_TEXT.search(text or "")
    return match.group(0) if match else None


def _version_key(version: str) -> tuple[int, int, int, int]:
    """Orders versions; a prerelease sorts before its release, as semver says."""
    core, _, pre = version.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return (major, minor, patch, 0 if pre else 1)


def platform_package(
    system: str | None = None, machine: str | None = None, libc: str | None = None
) -> str | None:
    """The per-platform npm package holding this machine's native binary.

    Mirrors the wrapper's postinstall. None where it publishes nothing, which
    the panel reports rather than guessing.
    """
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if arch is None or system not in ("linux", "darwin"):
        return None
    if system == "darwin":
        return f"{WRAPPER_PACKAGE}-darwin-{arch}"
    libc = platform.libc_ver()[0] if libc is None else libc
    suffix = "" if libc == "glibc" else "-musl"
    return f"{WRAPPER_PACKAGE}-linux-{arch}{suffix}"


def _root(data_dir: str | Path) -> Path:
    return Path(data_dir) / INSTALL_DIR


def installed_version(data_dir: str | Path) -> str | None:
    """The version the ``current`` pointer names, if it names a usable binary."""
    if not str(data_dir):
        return None
    try:
        version = (_root(data_dir) / CURRENT_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    # The pointer becomes a path component, so it is validated like input.
    if not _VERSION.match(version):
        return None
    binary = _root(data_dir) / version / BINARY_NAME
    return version if binary.is_file() and os.access(binary, os.X_OK) else None


def installed_binary(data_dir: str | Path) -> Path | None:
    """The managed binary's absolute path, or None when nothing is installed."""
    version = installed_version(data_dir)
    return None if version is None else _root(data_dir) / version / BINARY_NAME


def resolve_binary(setting: str, data_dir: str | Path) -> str:
    """What to run: the managed install, unless the setting names another binary."""
    setting = setting.strip() or DEFAULT_BINARY
    if setting != DEFAULT_BINARY:
        return setting
    managed = installed_binary(data_dir)
    return str(managed) if managed is not None else setting


def _check_integrity(digest: bytes, integrity: str) -> bool:
    """Compare against an SRI string; only sha512, which npm has published for years."""
    for token in integrity.split():
        algorithm, _, encoded = token.partition("-")
        if algorithm == "sha512":
            try:
                return base64.b64decode(encoded, validate=True) == digest
            except ValueError:
                return False
    return False


def _unpack(tarball: Path, destination: Path) -> None:
    """Copy the one binary out of the tarball; every other member is ignored.

    Nothing is extracted by name from the archive into the filesystem, so a
    hostile member path (``../``, a symlink) has nowhere to go.
    """
    with tarfile.open(tarball, "r:gz") as archive:
        member = next((m for m in archive if m.name == TARBALL_MEMBER), None)
        if member is None or not member.isfile():
            raise InstallError(f"the package has no {TARBALL_MEMBER}")
        if member.size > MAX_BINARY_BYTES:
            raise InstallError("the packaged binary is larger than any real release")
        source = archive.extractfile(member)
        if source is None:
            raise InstallError(f"could not read {TARBALL_MEMBER} from the package")
        with source, destination.open("wb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
    destination.chmod(0o755)


@dataclass
class InstallJob:
    """The last install, kept in memory: the page polls it while one runs.

    Not a database row, because nothing about it outlives the process worth
    keeping: an install cut short by a restart leaves only a temporary
    directory, which the next boot removes, and the binary in use is whatever
    ``current`` says.
    """

    state: JobState = "idle"
    target: str = ""
    version: str = ""
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "target": self.target,
            "version": self.version,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


type ClientFactory = Callable[[], httpx2.AsyncClient]
type VersionProbe = Callable[[str], Any]


async def _probe_version(binary: str) -> str | None:
    """``<binary> --version`` through the provider's own sandboxed spawn."""
    return parse_version(await ClaudeCodeProvider(binary=binary).version())


@dataclass
class ClaudeCliManager:
    """The installer behind ``/api/llm/claude-cli``: one per process."""

    data_dir: str
    #: Replaced in tests, to reach a mock transport instead of npm.
    client_factory: ClientFactory = outbound_http_client
    #: Replaced in tests, so no real binary has to run.
    probe: VersionProbe = _probe_version
    job: InstallJob = field(default_factory=InstallJob)
    _tags: dict[str, str] = field(default_factory=dict)
    _tags_at: float = 0.0
    #: The image's binary cannot change under a running process, so it is
    #: probed once rather than on every settings page load.
    _bundled: tuple[str | None, str | None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def root(self) -> Path:
        return _root(self.data_dir)

    @property
    def running(self) -> bool:
        return self.job.state == "running"

    # -- reading ---------------------------------------------------------

    async def dist_tags(self, *, refresh: bool = False) -> dict[str, str]:
        """The wrapper package's channels, cached; empty when npm is unreachable."""
        if not refresh and self._tags and time.monotonic() - self._tags_at < TAGS_TTL_S:
            return dict(self._tags)
        try:
            async with self.client_factory() as client:
                document = await self._get_json(client, f"/{quote(WRAPPER_PACKAGE, safe='@')}")
        except InstallError as exc:
            log.info("claude_cli_tags_unavailable", reason=str(exc))
            return dict(self._tags)
        raw = document.get("dist-tags")
        tags = {
            name: value
            for name, value in (raw.items() if isinstance(raw, dict) else ())
            if name in CHANNELS and isinstance(value, str) and _VERSION.match(value)
        }
        self._tags, self._tags_at = tags, time.monotonic()
        return dict(tags)

    async def status(self, *, configured_bin: str) -> dict[str, Any]:
        """What the panel shows: which binary runs, the image's, the channels, the job."""
        setting = configured_bin.strip() or DEFAULT_BINARY
        bundled_path, bundled = await self.bundled()
        managed = installed_version(self.data_dir)
        return {
            "platform_package": platform_package(),
            "bundled": {"path": bundled_path, "version": bundled},
            "managed": {
                "path": str(installed_binary(self.data_dir)) if managed else None,
                "version": managed,
            },
            # A custom claudeCodeBin wins over both; the panel says so.
            "overridden": setting != DEFAULT_BINARY,
            "active_binary": resolve_binary(setting, self.data_dir),
            "channels": await self.dist_tags(),
            "job": self.job.as_dict(),
        }

    async def bundled(self) -> tuple[str | None, str | None]:
        """The image's own binary on PATH, and its version."""
        if self._bundled is None:
            path = shutil.which(DEFAULT_BINARY)
            self._bundled = (path, await self.probe(path) if path else None)
        return self._bundled

    # -- installing ------------------------------------------------------

    def begin(self, target: str) -> None:
        """Claim the job synchronously, so a second press is refused, not queued."""
        self.job = InstallJob(state="running", target=target, started_at=_now())

    async def install(self, target: str) -> None:
        """Download, verify and switch to ``target``. Never raises: the job records it."""
        if not self.running:
            self.begin(target)
        async with self._lock:
            try:
                version = await self._install(target)
            except InstallError as exc:
                self._finish("failed", str(exc))
                log.warning("claude_cli_install_failed", target=target, reason=str(exc))
            except Exception as exc:  # the job must never be left "running"
                self._finish("failed", f"unexpected error: {type(exc).__name__}")
                log.exception("claude_cli_install_crashed", target=target)
            else:
                self.job.version = version
                self._finish("done", f"Claude Code {version} installed")
                log.info("claude_cli_installed", target=target, version=version)

    def _finish(self, state: JobState, message: str) -> None:
        self.job.state = state
        self.job.message = message[:500]
        self.job.finished_at = _now()

    async def _install(self, target: str) -> str:
        if not valid_target(target):
            raise InstallError(f"{target!r} is not stable, latest or an exact version")
        package = platform_package()
        if package is None:
            raise InstallError(
                f"Claude Code publishes no binary for {platform.system()} {platform.machine()}"
            )
        async with self.client_factory() as client:
            version = target
            if target in CHANNELS:
                tags = await self.dist_tags(refresh=True)
                if target not in tags:
                    raise InstallError(f"could not resolve the {target} channel from npm")
                version = tags[target]
            self.job.version = version
            meta = await self._get_json(client, f"/{quote(package, safe='@')}/{version}")
            raw_dist = meta.get("dist")
            dist: dict[str, Any] = raw_dist if isinstance(raw_dist, dict) else {}
            tarball = str(dist.get("tarball", ""))
            integrity = str(dist.get("integrity", ""))
            # Only ever from the registry itself: the metadata names the URL,
            # and a URL elsewhere is not one this installer trusts.
            parts = urlsplit(tarball)
            if parts.scheme != "https" or parts.netloc != urlsplit(REGISTRY).netloc:
                raise InstallError("the registry named a tarball outside registry.npmjs.org")
            if not integrity:
                raise InstallError("the registry published no integrity hash for that release")

            self.root.mkdir(parents=True, exist_ok=True)
            staging = self.root / f".staging-{version}-{os.getpid()}"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            try:
                archive = staging / "package.tgz"
                await self._download(client, tarball, archive, integrity)
                binary = staging / BINARY_NAME
                await asyncio.to_thread(_unpack, archive, binary)
                archive.unlink()
                probed = await self.probe(str(binary))
                if probed != version:
                    raise InstallError(
                        f"the downloaded binary would not run here (reported {probed or 'nothing'})"
                    )
                _, image = await self.bundled()
                self._activate(staging, version, image)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return version

    async def _get_json(self, client: httpx2.AsyncClient, path: str) -> dict[str, Any]:
        try:
            response = await client.get(
                f"{REGISTRY}{path}",
                headers={"accept": "application/json"},
                timeout=REGISTRY_TIMEOUT_S,
            )
        except httpx2.HTTPError as exc:
            raise InstallError(f"npm did not answer: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise InstallError("npm has no such release")
        if response.status_code != 200:
            raise InstallError(f"npm answered HTTP {response.status_code}")
        try:
            document = response.json()
        except ValueError as exc:
            raise InstallError("npm returned something that is not JSON") from exc
        if not isinstance(document, dict):
            raise InstallError("npm returned an unexpected document")
        return document

    async def _download(
        self, client: httpx2.AsyncClient, url: str, destination: Path, integrity: str
    ) -> None:
        digest = hashlib.sha512()
        received = 0
        try:
            async with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S) as response:
                if response.status_code != 200:
                    raise InstallError(f"the download answered HTTP {response.status_code}")
                with destination.open("wb") as target:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        received += len(chunk)
                        if received > MAX_TARBALL_BYTES:
                            raise InstallError("the download is larger than any real release")
                        digest.update(chunk)
                        target.write(chunk)
        except httpx2.HTTPError as exc:
            raise InstallError(f"the download failed: {type(exc).__name__}") from exc
        if not _check_integrity(digest.digest(), integrity):
            raise InstallError("the download does not match the registry's sha512; not installed")

    def _activate(self, staging: Path, version: str, image: str | None) -> None:
        """Move the verified binary into place, flip ``current``, drop the rest."""
        final = self.root / version
        shutil.rmtree(final, ignore_errors=True)
        final.mkdir()
        os.replace(staging / BINARY_NAME, final / BINARY_NAME)
        self._write(IMAGE_FILE, image or "")
        self._write(CURRENT_FILE, version)
        # One release is ~230 MB, and only the one in use is worth the disk.
        self._prune(keep=version)

    def _write(self, name: str, value: str) -> None:
        """Replace a one-line file atomically, so a crash never leaves half of one."""
        temporary = self.root / f".{name}.tmp"
        temporary.write_text(value + "\n", encoding="utf-8")
        os.replace(temporary, self.root / name)

    def _prune(self, *, keep: str | None) -> None:
        if not self.root.is_dir():
            return
        for entry in self.root.iterdir():
            if entry.name in (keep, CURRENT_FILE, IMAGE_FILE):
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    entry.unlink()

    # -- reverting and boot -------------------------------------------------

    def remove(self) -> None:
        """Go back to the image's binary."""
        self._clear()
        log.info("claude_cli_reverted")

    def _clear(self) -> None:
        if not self.root.is_dir():
            return
        with contextlib.suppress(FileNotFoundError):
            (self.root / CURRENT_FILE).unlink()
        with contextlib.suppress(FileNotFoundError):
            (self.root / IMAGE_FILE).unlink()
        self._prune(keep=None)
        with contextlib.suppress(OSError):
            self.root.rmdir()

    async def reconcile(self) -> None:
        """At boot: clear half-finished installs, and step aside for a newer image.

        Only when the image itself moved: an install made over the same image
        is a person's choice, a downgrade included, and a restart must not
        undo it. Once a new image carries that release or a later one, keeping
        the install would pin the box to the older of the two for ever.
        """
        managed = installed_version(self.data_dir)
        if managed is None:
            # Nothing usable: a staging directory from an install cut short,
            # or a pointer to a binary that is gone. Either way, clean slate.
            self._clear()
            return
        self._prune(keep=managed)
        _, bundled = await self.bundled()
        try:
            installed_over = (self.root / IMAGE_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            installed_over = ""
        if (
            bundled is not None
            and bundled != installed_over
            and _version_key(bundled) >= _version_key(managed)
        ):
            log.info("claude_cli_superseded_by_image", managed=managed, bundled=bundled)
            self.remove()
