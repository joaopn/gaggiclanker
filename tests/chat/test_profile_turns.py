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

from gaggiclanker.chat.runner import DESIGN_CHAT_PROMPT, GENERAL_CHAT_PROMPT, RECIPE_CHAT_PROMPT
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


async def test_the_recipe_prompt_understands_the_turns_of_a_profile_card(
    prompts: PromptService,
) -> None:
    system = await _system(prompts, RECIPE_CHAT_PROMPT)

    assert f'"{TURNS["approved_for_recipe"]}"' in system
    assert '(you proposed it as <old name>)."' in system
    assert '"Declined: <note>"' in system and '"Declined: no reason given."' in system
    # The Recipe follows its profile the moment the proposal is approved: it has recorded the
    # version already, and the profile reaches the machine with the next sync.
    approved = system[system.index(TURNS["approved_for_recipe"]) :]
    assert "The Recipe has recorded that version already" in approved
    assert "`get_recipe` shows it" in approved
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


@pytest.mark.parametrize("name", [RECIPE_CHAT_PROMPT, GENERAL_CHAT_PROMPT, DESIGN_CHAT_PROMPT])
async def test_no_prompt_sends_the_person_to_the_profiles_page_to_answer_a_proposal(
    prompts: PromptService, name: str
) -> None:
    system = await _system(prompts, name)

    assert "make it active" not in system and "makes it active" not in system
    assert "for them to make active" not in system


async def test_the_recipe_prompt_reads_a_first_recipe_accepted_under_another_name(
    prompts: PromptService,
) -> None:
    system = await _system(prompts, RECIPE_CHAT_PROMPT)

    clause = TURNS["accepted_first_recipe_renamed_clause"].removeprefix(", ")
    assert f"{clause} (you proposed it as <old name>)" in system
    assert "and from then on you use the new name" in system
