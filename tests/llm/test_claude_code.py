"""The CLI provider: argv, environment, stdin, the scratch dir and the envelope.

Every assertion here is about process hygiene, which is the part of this
provider that is dangerous and the part a passing integration test would not
notice. The spawn function is injected, so the whole file runs without the
binary and without a subscription.
"""

from __future__ import annotations

import json
import os
import shlex
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.providers.base import ProviderCall
from gaggiclanker.llm.providers.claude_code import (
    PROBE_MODEL,
    PROBE_PROMPT,
    PROBE_TIMEOUT_S,
    ClaudeCodeProvider,
    SpawnResult,
    build_call_argv,
    build_child_env,
    format_prompt,
    parse_cli_json_output,
)
from gaggiclanker.llm.types import LlmMessage
from tests.llm.conftest import Answer


@dataclass
class RecordedSpawn:
    """Stands in for the child process, and remembers how it was invoked."""

    result: SpawnResult
    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    stdin: str | None = None
    timeout_s: float = 0.0
    cwd_existed: bool = False

    async def __call__(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        cwd: str,
        stdin: str | None,
        timeout_s: float,
    ) -> SpawnResult:
        self.argv = list(argv)
        self.env = dict(env)
        self.cwd = cwd
        self.stdin = stdin
        self.timeout_s = timeout_s
        # Checked here rather than afterwards: the directory is removed as soon
        # as the call returns, which is the behaviour being asserted.
        self.cwd_existed = Path(cwd).is_dir()
        return self.result


def envelope(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"verdict": "ok", "score": 1}',
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }
    body.update(overrides)
    return json.dumps(body)


def ok(stdout: str) -> SpawnResult:
    return SpawnResult(returncode=0, stdout=stdout, stderr="")


def call() -> ProviderCall:
    return ProviderCall(
        mode="json_schema",
        model="haiku",
        messages=[
            LlmMessage(role="system", content="be brief"),
            LlmMessage(role="user", content="how was it?"),
        ],
        output_model=Answer,
        timeout_s=30.0,
        effort="high",
    )


# -- argv -----------------------------------------------------------------


def test_the_flags_lock_the_cli_down() -> None:
    argv = build_call_argv(model="haiku", effort="high", schema={"type": "object"})

    assert argv[:2] == ["-p", "--output-format"]
    # No tools, no ambient settings, no MCP, no session on disk.
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    # The harness prompt replaces ~52k tokens of agent instructions.
    assert "headless JSON generation service" in argv[argv.index("--system-prompt") + 1]
    # Last, because --tools and --setting-sources are variadic and must be
    # followed by a flag.
    assert argv[-2] == "--json-schema"
    assert json.loads(argv[-1]) == {"type": "object"}


def test_an_unknown_effort_is_dropped_rather_than_passed_on() -> None:
    argv = build_call_argv(model="haiku", effort="turbo")

    assert "--effort" not in argv


def test_no_model_means_no_model_flag() -> None:
    assert "--model" not in build_call_argv(model="   ")


# -- the environment ------------------------------------------------------


def test_the_api_key_is_never_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ambient key would silently shadow the subscription token."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-travel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-not")

    env = build_child_env(scratch_home="/tmp/scratch", oauth_token="tok")

    assert "ANTHROPIC_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


def test_the_allow_list_carries_tls_and_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:3128")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/certs/corp.pem")

    env = build_child_env(scratch_home="/tmp/scratch")

    assert env["HTTPS_PROXY"] == "http://proxy.test:3128"
    assert env["NODE_EXTRA_CA_CERTS"] == "/certs/corp.pem"
    assert env["PATH"] == os.environ["PATH"]


def test_telemetry_and_autoupdate_are_switched_off() -> None:
    env = build_child_env(scratch_home="/tmp/scratch")

    assert env["DISABLE_TELEMETRY"] == "1"
    assert env["DISABLE_ERROR_REPORTING"] == "1"
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_an_ambient_token_never_reaches_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """The child's token is the setting's, or there is none — never this process's own."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "ambient-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient")

    configured = build_child_env(scratch_home="/x", oauth_token="explicit")
    assert configured["CLAUDE_CODE_OAUTH_TOKEN"] == "explicit"

    unconfigured = build_child_env(scratch_home="/x")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in unconfigured
    assert "ambient-token" not in unconfigured.values()
    assert "sk-ant-ambient" not in unconfigured.values()


async def test_an_ambient_token_does_not_count_as_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "ambient-token")
    spawn = RecordedSpawn(ok(json.dumps({"loggedIn": True})))
    provider = ClaudeCodeProvider(spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok is False
    assert spawn.argv == []


# -- stdin and the scratch directory --------------------------------------


def test_the_prompt_is_a_numbered_transcript() -> None:
    rendered = format_prompt(
        [LlmMessage(role="system", content="be brief"), LlmMessage(role="user", content="hi")]
    )

    assert rendered.startswith("Transcript:\n\n")
    assert "Message 1 (SYSTEM):\nbe brief" in rendered
    assert "Message 2 (USER):\nhi" in rendered


async def test_the_prompt_goes_on_stdin_not_in_argv() -> None:
    """argv is world-readable in /proc; shot notes are not ours to publish."""
    spawn = RecordedSpawn(ok(envelope()))
    provider = ClaudeCodeProvider(spawn=spawn)

    await provider.complete(call())

    assert spawn.stdin is not None
    assert "how was it?" in spawn.stdin
    assert not any("how was it?" in argument for argument in spawn.argv)


async def test_home_and_cwd_are_a_scratch_dir_that_is_then_removed() -> None:
    spawn = RecordedSpawn(ok(envelope()))
    provider = ClaudeCodeProvider(spawn=spawn)

    await provider.complete(call())

    assert spawn.cwd_existed
    assert spawn.env["HOME"] == spawn.cwd
    assert spawn.cwd != os.environ.get("HOME")
    assert not Path(spawn.cwd).exists()


async def test_the_configured_binary_is_what_runs() -> None:
    spawn = RecordedSpawn(ok(envelope()))
    provider = ClaudeCodeProvider(binary="/opt/claude/bin/claude", spawn=spawn)

    await provider.complete(call())

    assert spawn.argv[0] == "/opt/claude/bin/claude"


# -- the envelope ---------------------------------------------------------


def test_stray_lines_before_the_result_are_skipped() -> None:
    stdout = "\n".join(
        [
            "npm warn: something irrelevant",
            json.dumps({"type": "system", "subtype": "init"}),
            envelope(),
        ]
    )

    text, usage = parse_cli_json_output(stdout)

    assert json.loads(text) == {"verdict": "ok", "score": 1}
    assert usage.prompt_tokens == 20


def test_structured_output_beats_the_prose_result() -> None:
    stdout = envelope(result="here you go", structured_output={"verdict": "ok", "score": 3})

    text, _ = parse_cli_json_output(stdout)

    assert json.loads(text) == {"verdict": "ok", "score": 3}


def test_a_null_structured_output_falls_through_to_the_result() -> None:
    """`null` would stringify to "null" and pass as a successful empty answer."""
    stdout = envelope(result='{"verdict": "ok", "score": 2}', structured_output=None)

    text, _ = parse_cli_json_output(stdout)

    assert json.loads(text) == {"verdict": "ok", "score": 2}


def test_is_error_is_the_failure_signal_and_carries_the_http_status() -> None:
    """The CLI exits 0 on a bad token. The exit code is not the signal."""
    stdout = envelope(is_error=True, result="Invalid API key", api_error_status=401)

    with pytest.raises(LlmApiError) as caught:
        parse_cli_json_output(stdout)

    assert caught.value.status == 401
    assert "Invalid API key" in caught.value.message


def test_cache_tokens_are_added_to_the_input_count() -> None:
    stdout = envelope(
        usage={
            "input_tokens": 20,
            "cache_creation_input_tokens": 2000,
            "cache_read_input_tokens": 300,
            "output_tokens": 44,
        }
    )

    _, usage = parse_cli_json_output(stdout)

    assert usage.prompt_tokens == 2320
    assert usage.completion_tokens == 44


def test_no_result_event_is_an_error() -> None:
    with pytest.raises(LlmApiError, match="result event"):
        parse_cli_json_output(json.dumps({"type": "system"}))


def test_empty_output_is_an_error() -> None:
    with pytest.raises(LlmApiError, match="no output"):
        parse_cli_json_output("   \n ")


# -- failure modes of the child -------------------------------------------


async def test_a_timeout_is_reported_as_one_and_the_child_was_killed() -> None:
    spawn = RecordedSpawn(SpawnResult(returncode=-9, stdout="", stderr="", timed_out=True))
    provider = ClaudeCodeProvider(spawn=spawn)

    with pytest.raises(LlmApiError, match="timed out"):
        await provider.complete(call())


async def test_empty_stdout_reports_what_stderr_said() -> None:
    spawn = RecordedSpawn(SpawnResult(returncode=1, stdout="", stderr="node: not found"))
    provider = ClaudeCodeProvider(spawn=spawn)

    with pytest.raises(LlmApiError, match="node: not found"):
        await provider.complete(call())


async def test_a_missing_binary_says_how_to_fix_it() -> None:
    async def missing(*_args: Any) -> SpawnResult:
        raise FileNotFoundError(2, "No such file or directory", "claude")

    provider = ClaudeCodeProvider(binary="claude", spawn=missing)

    with pytest.raises(LlmApiError, match="claudeCodeBin"):
        await provider.complete(call())


# -- auth and models ------------------------------------------------------


@dataclass
class SpawnSequence:
    """One scripted child per spawn, in order, each invocation remembered."""

    results: list[SpawnResult]
    calls: list[tuple[list[str], str | None, float]] = field(default_factory=list)

    async def __call__(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        cwd: str,
        stdin: str | None,
        timeout_s: float,
    ) -> SpawnResult:
        self.calls.append((list(argv), stdin, timeout_s))
        return self.results[len(self.calls) - 1]


LOGGED_IN = ok(
    json.dumps({"loggedIn": True, "email": "user@example.test", "subscriptionType": "max"})
)


async def test_auth_status_is_the_free_presence_check() -> None:
    spawn = RecordedSpawn(LOGGED_IN)
    provider = ClaudeCodeProvider(oauth_token="tok", spawn=spawn)

    check = await provider.auth_status()

    assert spawn.argv[1:] == ["auth", "status", "--json"]
    assert spawn.stdin is None
    assert check.ok
    assert "user@example.test" in check.detail


async def test_validate_makes_one_tiny_real_call_after_auth_status() -> None:
    """`auth status` says logged in for any token; only a call can tell."""
    spawn = SpawnSequence([LOGGED_IN, ok(envelope(result="OK"))])
    provider = ClaudeCodeProvider(oauth_token="tok", effort="max", spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok
    assert "user@example.test" in check.detail
    assert PROBE_MODEL in check.detail
    (status_argv, _, _), (probe_argv, stdin, timeout_s) = spawn.calls
    assert status_argv[1:] == ["auth", "status", "--json"]
    assert probe_argv[1] == "-p"
    assert probe_argv[probe_argv.index("--model") + 1] == PROBE_MODEL
    # The cheapest call: no effort setting, no tools, the one-word prompt.
    assert "--effort" not in probe_argv
    assert probe_argv[probe_argv.index("--tools") + 1] == ""
    assert stdin == PROBE_PROMPT
    assert timeout_s == PROBE_TIMEOUT_S


async def test_a_token_the_api_refuses_does_not_validate() -> None:
    refused = envelope(is_error=True, api_error_status=401, result="Invalid bearer token")
    spawn = SpawnSequence([LOGGED_IN, ok(refused)])
    provider = ClaudeCodeProvider(oauth_token="revoked", spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok is False
    assert "401" in check.detail
    assert "setup-token" in check.detail


async def test_a_probe_that_dies_says_why_from_stderr() -> None:
    died = SpawnResult(returncode=1, stdout="", stderr="unknown option --model")
    spawn = SpawnSequence([LOGGED_IN, died])
    provider = ClaudeCodeProvider(oauth_token="tok", spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok is False
    assert "unknown option --model" in check.detail


async def test_a_probe_that_hangs_is_reported_not_waited_on() -> None:
    hung = SpawnResult(returncode=-9, stdout="", stderr="", timed_out=True)
    spawn = SpawnSequence([LOGGED_IN, hung])
    provider = ClaudeCodeProvider(oauth_token="tok", spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok is False
    assert "did not answer" in check.detail


async def test_no_probe_is_spent_when_auth_status_already_failed() -> None:
    spawn = SpawnSequence([ok(json.dumps({"loggedIn": False}))])
    provider = ClaudeCodeProvider(oauth_token="tok", spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok is False
    assert len(spawn.calls) == 1


async def test_a_logged_out_cli_says_how_to_log_in() -> None:
    spawn = RecordedSpawn(ok(json.dumps({"loggedIn": False})))
    provider = ClaudeCodeProvider(oauth_token="tok", spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok is False
    assert "setup-token" in check.detail


async def test_no_token_is_refused_before_the_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer is already known, and it names the fix rather than the symptom."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    spawn = RecordedSpawn(ok(json.dumps({"loggedIn": True})))
    provider = ClaudeCodeProvider(spawn=spawn)

    check = await provider.validate_credentials()

    assert check.ok is False
    assert spawn.argv == []
    # The reason a logged-in box still needs a token is the scratch HOME.
    assert "scratch HOME" in check.detail


async def test_models_are_a_static_suggestion_list() -> None:
    provider = ClaudeCodeProvider(spawn=RecordedSpawn(ok("")))

    models = await provider.list_models()

    assert "sonnet" in models
    assert "haiku" in models


# -- the real spawner -----------------------------------------------------
#
# Everything above injects a fake child. These two run a real one, because the
# thing being asserted is that a wedged process is actually dead afterwards -
# which is a property of the signal sequence, not of the code around it.


async def test_the_real_spawner_feeds_stdin_and_collects_both_streams() -> None:
    from gaggiclanker.llm.providers.claude_code import _spawn

    result = await _spawn(
        ["/bin/sh", "-c", "cat; echo oops >&2"], {"PATH": os.environ["PATH"]}, ".", "hello", 10.0
    )

    assert result.stdout == "hello"
    assert result.stderr.strip() == "oops"
    assert result.timed_out is False


async def test_a_wedged_child_is_killed_rather_than_left_running(tmp_path: Path) -> None:
    from gaggiclanker.llm.providers.claude_code import _spawn

    # Ignores SIGTERM on purpose: SIGTERM alone would leave it running, holding
    # an event-loop transport open for the life of the process.
    #
    # `exec` so the shell *becomes* the sleeping process (an ignored signal
    # stays ignored across exec). Without it `sleep` is a grandchild: the
    # SIGKILL lands on the shell, the orphaned `sleep` keeps the stdout pipe
    # open, and `Process.wait()` - which waits for the pipes as well as the
    # exit - sat there until the sleep ran out. That made this test take the
    # full thirty seconds while proving nothing about the process left behind.
    pid_file = tmp_path / "child.pid"
    script = f"trap '' TERM; echo $$ > {shlex.quote(str(pid_file))}; exec sleep 30"
    # The timeout is the shell's head start: a SIGTERM that arrived before the
    # trap was set would kill it outright and prove nothing, which a loaded
    # machine running the suite in parallel can make happen at a few hundred
    # milliseconds. The grace is injected because its real three seconds would
    # only be waited out.
    timeout_s, grace_s = 1.0, 0.2
    started = time.monotonic()
    result = await _spawn(
        ["/bin/sh", "-c", script],
        {"PATH": os.environ["PATH"]},
        ".",
        None,
        timeout_s,
        kill_grace_s=grace_s,
    )
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    # The grace ran out, so SIGTERM really was ignored and SIGKILL is what
    # ended it - and it ended long before the sleep would have.
    assert timeout_s + grace_s <= elapsed < 10
    # Nothing left running: the pid is gone, reaped rather than a zombie.
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
