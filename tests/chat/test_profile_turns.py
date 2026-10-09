"""The turns a profile card sends the agent, and the prompts that understand them.

A person answers a proposed profile where it is shown, and the answer reaches the agent as their
own message in the conversation. The strings below are the contract between the card and the
prompts: the web builds these exact turns, the prompts name them, and this module is the one
place they are written down.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.chat.runner import DESIGN_CHAT_PROMPT, GENERAL_CHAT_PROMPT, SET_CHAT_PROMPT
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.db.schema import create_schema
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts

#: Each turn with its placeholders, read from the file the web builds them from: one source, so
#: the card and the prompts cannot drift apart. A renamed profile ends ``(you proposed it as
#: <old name>).`` in place of the closing full stop.
TURNS: dict[str, str] = json.loads(
    (Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "profileTurns.json").read_text(
        encoding="utf-8"
    )
)


def renamed(turn: str) -> str:
    """A turn for a profile the person renamed: the full stop becomes the ending."""
    assert turn.endswith(".")
    return turn[:-1] + TURNS["renamed_ending"]


@pytest.fixture
async def prompts(tmp_path: Path) -> AsyncIterator[PromptService]:
    db = Database(tmp_path / "prompts.db")
    await db.connect()
    await create_schema(db)
    repo = PromptsRepository(db)
    await seed_prompts(repo, DEFAULT_PROMPTS_DIR)
    try:
        yield PromptService(repo)
    finally:
        await db.close()


async def _system(prompts: PromptService, name: str) -> str:
    loaded = await prompts.load(name, {"scope": "", "shot_fields": ""})
    return " ".join(loaded.system.split())


def test_a_renamed_turn_replaces_the_full_stop_with_the_ending() -> None:
    assert renamed("Approved: Gentle Bloom, a new profile.") == (
        "Approved: Gentle Bloom, a new profile (you proposed it as <old name>)."
    )


async def test_the_set_prompt_understands_the_turns_of_a_profile_card(
    prompts: PromptService,
) -> None:
    system = await _system(prompts, SET_CHAT_PROMPT)

    assert f'"{TURNS["approved_for_set"]}"' in system
    assert '(you proposed it as <old name>)."' in system
    assert '"Declined: <note>"' in system and '"Declined: no reason given."' in system
    # The Set records the version only when the profile reaches the machine, so the message
    # does not end the conversation's version by itself.
    approved = system[system.index(TURNS["approved_for_set"]) :]
    assert "only once the profile has reached the machine" in approved
    assert "not that it is recorded, unless `get_set` already shows the new version" in approved
    assert "with Writes on the sync may have finished by now" in approved
    assert "start a new conversation" in approved
    # The rule for a grind, dose or yield change stays true.
    assert "There is nothing to push, make active, stage or log, and no queue" in system


async def test_the_general_prompt_understands_the_turns_of_a_profile_card(
    prompts: PromptService,
) -> None:
    system = await _system(prompts, GENERAL_CHAT_PROMPT)

    assert f'"{TURNS["approved_new_version"]}"' in system
    assert f'"{TURNS["approved_new_profile"]}"' in system
    assert '(you proposed it as <old name>)."' in system
    assert '"Declined: <note>"' in system and '"Declined: no reason given."' in system


@pytest.mark.parametrize("name", [SET_CHAT_PROMPT, GENERAL_CHAT_PROMPT, DESIGN_CHAT_PROMPT])
async def test_no_prompt_sends_the_person_to_the_profiles_page_to_answer_a_proposal(
    prompts: PromptService, name: str
) -> None:
    system = await _system(prompts, name)

    assert "make it active" not in system and "makes it active" not in system
    assert "for them to make active" not in system
