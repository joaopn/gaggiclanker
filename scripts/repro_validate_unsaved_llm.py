#!/usr/bin/env python
"""Reproduce: "Validate credentials" tests what is stored, not what is typed.

    uv run python scripts/repro_validate_unsaved_llm.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

Settings → LLM has a "Validate credentials" button, and the obvious thing to do
with it is to paste a token and press it. The button posted an empty body, and
``POST /api/llm/validate`` built the provider from the database alone, so a
token that had been typed but not saved was invisible to it: a fresh box
answered "No Claude Code OAuth token is configured" for a token sitting in the
box right above the button. Switching the provider picker and validating had
the same problem the other way round: it tested the provider that was saved.

This script posts the typed values the way the settings page now does, with a
stand-in ``claude`` that answers ``auth status`` as logged in only when a token
reached it, and checks nothing was written while doing so. It also checks that
a stored key is not lent to a provider the form has switched to: validating a
different provider must not send the saved provider's key to it.
"""

from __future__ import annotations

import asyncio
import stat
import sys
import tempfile
from pathlib import Path

import httpx

from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

#: Logged in exactly when the child got a token, which is the whole question.
FAKE_CLAUDE = """#!/bin/sh
if [ "$1 $2" = "auth status" ]; then
  if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
    echo '{"loggedIn": true, "email": "repro@example.test", "subscriptionType": "max"}'
  else
    echo '{"loggedIn": false}'
  fi
  exit 0
fi
echo "0.0.0 (Claude Code)"
"""


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fake = root / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        data = root / "data"
        data.mkdir()

        env = EnvSettings(DATA_DIR=str(data), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
        app = create_app(env, web_dist=root / "no-dist")
        failures: list[str] = []
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                response = await client.post(
                    "/api/llm/validate",
                    json={
                        "settings": {
                            "llmProvider": "claude_code",
                            "claudeCodeOauthToken": "sk-ant-oat01-typed-not-saved",
                            "claudeCodeBin": str(fake),
                        }
                    },
                )
                payload = response.json()
                check = payload.get("data") or {}
                if not check.get("ok"):
                    failures.append(
                        f"a typed, unsaved token was not validated: {response.status_code} "
                        f"{check.get('detail') or payload.get('error')}"
                    )

                stored = (await client.get("/api/settings")).json()["data"]
                if stored["claudeCodeOauthToken"]["source"] != "default":
                    failures.append("validating stored the typed token")

                # A key saved for OpenRouter must not reach OpenAI because the
                # picker was moved and Validate pressed before saving. Without a
                # key the OpenAI preset refuses before any network call.
                await client.patch(
                    "/api/settings",
                    json={"llmProvider": "openrouter", "llmApiKey": "sk-or-saved"},
                )
                sent: list[tuple[str, str]] = []
                service = app.state.llm
                original = service._build_provider

                def spy(config, name):  # type: ignore[no-untyped-def]
                    target = name or config.provider
                    sent.append((target, config.credential_for(target)))
                    return original(config, name)

                service._build_provider = spy
                await client.post("/api/llm/validate", json={"settings": {"llmProvider": "openai"}})
                if ("openai", "sk-or-saved") in sent:
                    failures.append("the saved OpenRouter key was lent to OpenAI by a validate")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: validate tests the typed values, stores nothing, and lends no key")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
