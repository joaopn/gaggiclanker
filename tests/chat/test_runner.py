"""The tool loop: what it does, what it refuses to keep doing, and what it stores.

Every test here scripts the provider and asserts on rows and events rather than
on timing. The properties being pinned are the ones a person notices when they
break: the answer arrives, the trace shows what was called, the run stops when
the bounds say so, cancel means cancel, and a reconnect does not lose the
middle of an answer.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from gaggiclanker.chat.runner import ChatRunner, _to_chat_message, run_task_name
from gaggiclanker.db.repos.chat import ChatEventsRepository, ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, InsightWrite
from gaggiclanker.db.repos.sets import DesignBrief, SetsRepository, SetWrite
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.chat_types import (
    ChatToolCall,
    ChatTurn,
    with_field_meanings,
    without_field_meanings,
)
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.types import Usage
from gaggiclanker.shotinfo.catalogue import CATALOGUE, default_tiers
from gaggiclanker.shotinfo.glossary import render_glossary
from gaggiclanker.tools.scope import DESIGN_TOOLS, GENERAL_TOOLS, SET_TOOLS
from tests.llm.conftest import FakeProvider
from tests.review.conftest import Fixture


async def finish(tasks: TaskRegistry, run_id: int) -> None:
    """Wait for one run's background task, whatever it did."""
    task = tasks.get(run_task_name(run_id))
    if task is not None:
        await asyncio.wait([task], timeout=10)


async def send(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, message: str = "how is it going?"
) -> int:
    run, _ = await runner.send(thread, message, tasks=tasks)
    await finish(tasks, run.id)
    return run.id


def kinds(events: list[Any]) -> list[str]:
    return [event.kind for event in events]


# -- the happy path --------------------------------------------------------


async def test_a_plain_answer_is_stored_streamed_and_completed(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [
        ChatTurn(text="Pull two more and we will know.", usage=Usage(11, 7))
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    messages = await ChatRepository(runner.db).messages(thread)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].content == "Pull two more and we will know."

    events = await ChatEventsRepository(runner.db).since(run_id)
    assert "delta" in kinds(events)
    assert kinds(events)[-1] == "completed"


async def test_the_thread_is_named_from_its_first_question(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    """Nobody titles a conversation; an untitled list of twenty is unusable."""
    await send(runner, tasks, thread, "Why is this shot sour? I tried two clicks finer.")

    row = await ChatRepository(runner.db).get_thread(thread)
    assert row is not None
    assert row.title.startswith("Why is this shot sour?")


async def test_a_tool_round_executes_dispatches_and_feeds_the_result_back(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [
        ChatTurn(
            text="",
            tool_calls=[
                ChatToolCall(id="c1", name="get_shot", arguments={"shot_id": archive.shots[0]})
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="Shot looks fine."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert (run.status, run.tool_rounds, run.tool_calls) == ("ok", 1, 1)

    roles = [message.role for message in await ChatRepository(runner.db).messages(thread)]
    assert roles == ["user", "assistant", "tool", "assistant"]

    events = kinds(await ChatEventsRepository(runner.db).since(run_id))
    assert "tool_call" in events and "tool_result" in events

    # The second turn was shown the result, which is the point of the loop.
    second = chat_provider.chat_calls[1]
    assert second.messages[-1].role == "tool"
    assert second.messages[-1].tool_results[0].name == "get_shot"


async def test_a_failing_tool_is_shown_to_the_model_rather_than_ending_the_run(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[ChatToolCall(id="c1", name="get_shot", arguments={"shot_id": 999_999})],
            stop_reason="tool_use",
        ),
        ChatTurn(text="There is no shot 999999 in the archive."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.status == "ok"
    result = chat_provider.chat_calls[1].messages[-1].tool_results[0]
    assert result.ok is False
    # A Set conversation refuses a shot that is not its own in the same words
    # whether or not it exists, which is what the model is shown here.
    assert "not a shot of this Set" in result.content


# -- the extended glossary rides with an extended read ----------------------


def _extended_call(call_id: str, name: str, *shots: int) -> ChatToolCall:
    arguments: dict[str, Any] = (
        {"shot_ids": list(shots)} if name == "compare_shots" else {"shot_id": shots[0]}
    )
    return ChatToolCall(id=call_id, name=name, arguments=arguments)


def _round(*calls: ChatToolCall) -> ChatTurn:
    return ChatTurn(text="", tool_calls=list(calls), stop_reason="tool_use")


def _escaped(text: str) -> str:
    """The text as it sits inside a tool result's JSON."""
    return json.dumps(text)[1:-1]


async def test_the_first_successful_extended_read_of_a_run_carries_the_extended_glossary(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """One run: a base read and a failed read carry none and do not use it up; the
    first successful extended, full or compare read carries it, in front of the
    shot's own text; the later ones do not; a new run carries it again."""
    shot = archive.shots[0]
    meanings = render_glossary(default_tiers(), "extended")
    chat_provider.chat_script = [
        _round(
            _extended_call("c1", "get_shot", shot),
            _extended_call("c2", "get_shot_extended", 999_999),
            _extended_call("c3", "get_shot_extended", shot),
            _extended_call("c4", "get_shot_full", shot),
            _extended_call("c5", "compare_shots", shot, archive.shots[1]),
        ),
        ChatTurn(text="done"),
    ]

    await send(runner, tasks, thread)

    results = chat_provider.chat_calls[1].messages[-1].tool_results
    carries = [meanings and _escaped(meanings) in result.content for result in results]
    assert carries == [False, False, True, False, False]
    assert results[2].content.index("field_meanings") < results[2].content.index('"text"')
    assert [result.ok for result in results] == [True, False, True, True, True]

    # A new run whose history no longer holds the earlier copy (the budget dropped it)
    # attaches its own; with the copy in history it would attach none (tested below).
    await runner.llm.settings.store("chatHistoryTokenBudget", 1000)
    # The fake indexes its script by the number of calls so far: pad past the first run's two.
    chat_provider.chat_script = [
        ChatTurn(text=""),
        ChatTurn(text=""),
        _round(_extended_call("d1", "compare_shots", shot, archive.shots[1])),
        ChatTurn(text="again"),
    ]
    await send(runner, tasks, thread, "and again?")
    again = chat_provider.chat_calls[3].messages[-1].tool_results[0]
    assert _escaped(meanings) in again.content
    assert again.content.startswith('{"field_meanings": ')


async def test_the_meanings_come_once_per_run_across_rounds(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """The holder belongs to the run, not to a round: a second round's extended read has none."""
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot_extended", archive.shots[0])),
        _round(_extended_call("c2", "get_shot_extended", archive.shots[1])),
        ChatTurn(text="done"),
    ]

    await send(runner, tasks, thread)

    # The fake keeps the request's history list, which grows: read both from the last request.
    by_id = {r.id: r for m in chat_provider.chat_calls[2].messages for r in m.tool_results}
    first, second = by_id["c1"], by_id["c2"]
    assert "field_meanings" in first.content
    assert "field_meanings" not in second.content
    assert second.ok


async def test_a_failed_compare_does_not_use_up_the_meanings(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [
        _round(
            _extended_call("c1", "compare_shots", archive.shots[0], 999_999),
            _extended_call("c2", "compare_shots", archive.shots[0], archive.shots[1]),
        ),
        ChatTurn(text="done"),
    ]

    await send(runner, tasks, thread)

    failed, ok = chat_provider.chat_calls[1].messages[-1].tool_results
    assert (failed.ok, ok.ok) == (False, True)
    assert "field_meanings" not in failed.content
    assert "field_meanings" in ok.content


async def test_the_tool_result_event_previews_the_shot_not_the_meanings(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="done"),
    ]

    run_id = await send(runner, tasks, thread)

    events = await ChatEventsRepository(runner.db).since(run_id)
    [event] = [e for e in events if e.kind == "tool_result"]
    preview = event.data["content"]
    assert "field_meanings" not in preview and '"text"' in preview
    # The model still got them: only the preview is reduced.
    assert "field_meanings" in chat_provider.chat_calls[1].messages[-1].tool_results[0].content


def _copies(messages: list[Any]) -> list[Any]:
    return [r for m in messages for r in m.tool_results if "field_meanings" in r.content]


def _without_key(stored: str) -> dict[str, Any]:
    return {k: v for k, v in json.loads(stored).items() if k != "field_meanings"}


async def test_a_follow_up_is_sent_exactly_one_copy_when_the_history_has_extended_results(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """The meanings are in the context exactly once whenever extended lines are."""
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="first answer"),
        ChatTurn(text="second answer"),
    ]
    await send(runner, tasks, thread)
    await send(runner, tasks, thread, "and one more thing?")

    follow_up = chat_provider.chat_calls[2]
    assert len(_copies(follow_up.messages)) == 1
    assert follow_up.meanings_in_context is True
    assert chat_provider.chat_calls[1].meanings_in_context is False
    # Stored whole, so the transcript shows what the model saw.
    stored = [r for m in await ChatRepository(runner.db).messages(thread) for r in m.tool_results]
    assert any("field_meanings" in str(r["content"]) for r in stored)


async def test_a_follow_up_is_sent_no_copy_when_the_history_has_no_extended_results(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot", archive.shots[0])),
        ChatTurn(text="first answer"),
        ChatTurn(text="second answer"),
    ]
    await send(runner, tasks, thread)
    await send(runner, tasks, thread, "and one more thing?")

    follow_up = chat_provider.chat_calls[2]
    assert any(m.tool_results for m in follow_up.messages)
    assert _copies(follow_up.messages) == [] and follow_up.meanings_in_context is False


async def test_a_follow_up_that_reads_extended_with_a_copy_in_history_attaches_none(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="first answer"),
        _round(_extended_call("d1", "get_shot_full", archive.shots[1])),
        ChatTurn(text="second answer"),
    ]
    await send(runner, tasks, thread)
    await send(runner, tasks, thread, "and the next one?")

    last = chat_provider.chat_calls[3]
    read = next(r for m in last.messages for r in m.tool_results if r.id == "d1")
    assert read.ok and "field_meanings" not in read.content
    assert len(_copies(last.messages)) == 1, "the history's copy, and only that"


async def test_only_the_newest_of_two_earlier_copies_is_kept_and_the_older_is_exactly_stripped(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    first, second = archive.shots[0], archive.shots[1]
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot_extended", first)),
        ChatTurn(text="one"),
        _round(_extended_call("c2", "get_shot_extended", second)),
        ChatTurn(text="two"),
        ChatTurn(text="three"),
    ]
    await send(runner, tasks, thread)
    # A budget the first answer does not survive, so the second answer attaches its own copy.
    await runner.llm.settings.store("chatHistoryTokenBudget", 1000)
    await send(runner, tasks, thread, "next shot?")
    await runner.llm.settings.store("chatHistoryTokenBudget", 100_000)
    await send(runner, tasks, thread, "and a third question?")

    kept = chat_provider.chat_calls[4].messages
    [copy] = _copies(kept)
    assert json.loads(copy.content)["shot_id"] == second
    older = next(r for m in kept for r in m.tool_results if r.id == "c1")
    stored = {
        str(r["id"]): str(r["content"])
        for m in await ChatRepository(runner.db).messages(thread)
        for r in m.tool_results
    }
    assert "field_meanings" in stored["c1"] and "field_meanings" in stored["c2"]
    # Exactly the one key goes: the rest of the result is as stored.
    assert json.loads(older.content) == _without_key(stored["c1"])


async def test_the_budget_counts_the_copy_that_is_kept_and_may_drop_its_message(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    from gaggiclanker.chat.runner import CHARS_PER_TOKEN, _size

    meanings = render_glossary(default_tiers(), "extended")
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="first answer"),
    ]
    await send(runner, tasks, thread)

    everything, carries = await runner._history(thread, 1_000_000)
    with_copy = sum(_size(m) for m in everything)
    assert carries and with_copy > len(meanings)
    # Room for the copy and everything after it: kept whole.
    roomy, carries = await runner._history(thread, with_copy // CHARS_PER_TOKEN + 10)
    assert len(roomy) == len(everything) and carries
    # Room for every message once stripped, but not for the copy: the message that
    # would carry it goes, and with it the copy (the run attaches its own).
    stripped_total = with_copy - len(meanings)
    tight, carries = await runner._history(thread, stripped_total // CHARS_PER_TOKEN + 50)
    assert len(tight) < len(everything)
    assert not carries and _copies(tight) == []


def _copy_ids(messages: list[Any]) -> list[str]:
    return [r.id for r in _copies(messages)]


async def test_the_copy_lands_on_the_newest_extended_read_even_when_an_older_one_stored_it(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """Run A attached the copy; run B read in detail with none attached (A's was in its
    history). The follow-up is sent one copy, on B's result, rendered like a tool's."""
    chat_provider.chat_script = [
        _round(_extended_call("a1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="a"),
        _round(_extended_call("b1", "get_shot_full", archive.shots[1])),
        ChatTurn(text="b"),
        ChatTurn(text="c"),
    ]
    await send(runner, tasks, thread)
    await send(runner, tasks, thread, "now the next shot?")
    await send(runner, tasks, thread, "so what do you think?")

    follow_up = chat_provider.chat_calls[4]
    assert _copy_ids(follow_up.messages) == ["b1"]
    assert follow_up.meanings_in_context is True
    # The bytes are a tool's: the stored first read, with the copy a tool attached, is what
    # the same text without the key becomes when the history places the copy.
    stored = {
        str(r["id"]): str(r["content"])
        for m in await ChatRepository(runner.db).messages(thread)
        for r in m.tool_results
    }
    placed = next(r for m in follow_up.messages for r in m.tool_results if r.id == "b1")
    assert json.loads(placed.content)["field_meanings"] == render_glossary(
        default_tiers(), "extended"
    )
    assert "field_meanings" not in stored["b1"], "B attached none: A's copy was in its history"
    assert placed.content == with_field_meanings(
        stored["b1"], render_glossary(default_tiers(), "extended")
    )
    attached = next(c for c in stored.values() if "field_meanings" in c)
    assert (
        with_field_meanings(
            without_field_meanings(attached), render_glossary(default_tiers(), "extended")
        )
        == attached
    ), "same bytes as a tool's own attachment"


async def test_a_budget_that_drops_the_copy_s_first_home_moves_it_to_the_run_that_read_after(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """The gap: A carried the copy, B read with none attached, and the budget drops A."""
    from gaggiclanker.chat.runner import CHARS_PER_TOKEN, _size

    chat_provider.chat_script = [
        _round(_extended_call("a1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="a"),
        _round(_extended_call("b1", "get_shot_extended", archive.shots[1])),
        ChatTurn(text="b"),
        ChatTurn(text="c"),
    ]
    run_a = await send(runner, tasks, thread)
    await send(runner, tasks, thread, "now the next shot?")
    rows = await ChatRepository(runner.db).messages(thread)
    a_rows = sum(row.run_id == run_a for row in rows)
    everything, _ = await runner._history(thread, 1_000_000)
    total = sum(_size(m) for m in everything)
    # Room for everything but run A's messages, with the copy on B's result.
    budget = (total - sum(_size(m) for m in everything[:a_rows]) + 200) // CHARS_PER_TOKEN
    await runner.llm.settings.store("chatHistoryTokenBudget", budget)

    await send(runner, tasks, thread, "so what do you think?")

    follow_up = chat_provider.chat_calls[4]
    assert not any(r.id == "a1" for m in follow_up.messages for r in m.tool_results)
    assert _copy_ids(follow_up.messages) == ["b1"]
    assert follow_up.meanings_in_context is True


async def test_the_placed_copy_follows_the_tiers_as_they_are_now(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """The copy is rendered when the history is built, not copied from what was stored."""
    from gaggiclanker.shotinfo.catalogue import effective_tiers
    from gaggiclanker.shotinfo.glossary import extended_meanings

    chat_provider.chat_script = [
        _round(_extended_call("a1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="a"),
        ChatTurn(text="b"),
    ]
    await send(runner, tasks, thread)
    await ShotInfoTiersRepository(archive.db).set_tier(
        ShotInfoTierWrite(item_key="score_confidence", tier="base")
    )
    await send(runner, tasks, thread, "and now?")

    moved = await effective_tiers(archive.db)
    assert extended_meanings(moved) != extended_meanings(default_tiers())
    [copy] = _copies(chat_provider.chat_calls[2].messages)
    assert json.loads(copy.content)["field_meanings"] == extended_meanings(moved)


async def test_a_failed_read_does_not_count_as_extended_lines_to_explain(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """History of a base read and a refused extended read: no copy is placed, and when a
    refused read is the newest, the copy goes on the older successful one."""
    chat_provider.chat_script = [
        _round(
            _extended_call("a1", "get_shot", archive.shots[0]),
            _extended_call("a2", "get_shot_extended", 999_999),
        ),
        ChatTurn(text="a"),
        ChatTurn(text="b"),
        _round(_extended_call("c1", "get_shot_extended", archive.shots[0])),
        ChatTurn(text="c"),
        _round(_extended_call("d1", "get_shot_extended", 999_999)),
        ChatTurn(text="d"),
        ChatTurn(text="e"),
    ]
    await send(runner, tasks, thread)
    await send(runner, tasks, thread, "again?")
    base_only = chat_provider.chat_calls[2]
    assert _copies(base_only.messages) == [] and base_only.meanings_in_context is False

    await send(runner, tasks, thread, "in detail?")
    await send(runner, tasks, thread, "that other one?")
    await send(runner, tasks, thread, "so?")
    last = chat_provider.chat_calls[7]
    assert _copy_ids(last.messages) == ["c1"], "the refused newest read is not explained"


async def test_a_tool_result_cap_leaves_the_extended_glossary_whole(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaggiclanker.chat import runner as runner_module

    monkeypatch.setattr(runner_module, "TOOL_RESULT_CHARS", 300)
    meanings = render_glossary(default_tiers(), "extended")
    chat_provider.chat_script = [
        _round(_extended_call("c1", "get_shot_full", archive.shots[0])),
        ChatTurn(text="done"),
    ]

    await send(runner, tasks, thread)

    content = chat_provider.chat_calls[1].messages[-1].tool_results[0].content
    assert len(_escaped(meanings)) > 300, "the glossary alone is over the cap"
    assert _escaped(meanings) in content
    assert "truncated at 300 characters" in content


# -- the bounds ------------------------------------------------------------


async def test_the_round_budget_ends_the_loop_with_an_answer(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """A model that keeps calling tools still has to produce something to read."""
    await runner.llm.settings.store("chatMaxToolRounds", 2)
    looping = ChatTurn(
        tool_calls=[ChatToolCall(id="c", name="list_sets", arguments={})], stop_reason="tool_use"
    )
    chat_provider.chat_script = [looping, looping, ChatTurn(text="I ran out of budget.")]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.tool_rounds == 2
    # The final turn is asked without tools, so the model cannot keep going.
    assert chat_provider.chat_calls[-1].tools == []
    assert (await ChatRepository(runner.db).messages(thread))[-1].content == "I ran out of budget."


async def test_the_call_budget_caps_the_tools_actually_run(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    await runner.llm.settings.store("chatMaxToolCalls", 2)
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[
                ChatToolCall(id=f"c{index}", name="list_sets", arguments={}) for index in range(5)
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="Enough."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.tool_calls == 2


async def test_an_exhausted_call_budget_leaves_a_history_a_provider_will_accept(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    """The rounds after the budget is spent must not be written down.

    They used to be: an assistant message with empty content and a `tool`
    message with no results. Both are 400s on the Anthropic provider on every
    *later* turn of the thread, so one exhausted budget poisoned the
    conversation for good. The check is the provider's own message builder,
    against the stored history.
    """
    from gaggiclanker.llm.providers.anthropic import _chat_messages

    await runner.llm.settings.store("chatMaxToolCalls", 2)
    await runner.llm.settings.store("chatMaxToolRounds", 5)
    asking = ChatTurn(
        tool_calls=[ChatToolCall(id="c", name="list_sets", arguments={})], stop_reason="tool_use"
    )
    # Two rounds spend the budget; the three rounds left are the ones that used
    # to be written down empty.
    chat_provider.chat_script = [asking, asking, ChatTurn(text="Done.")]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.tool_calls == 2

    stored = await ChatRepository(runner.db).messages(thread)
    assert [message.role for message in stored] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    # No empty rounds: every assistant row either says something or asked for
    # something, and every tool row carries a result.
    for message in stored:
        if message.role == "assistant":
            assert message.content or message.tool_calls
        if message.role == "tool":
            assert message.tool_results

    rendered = _chat_messages([_to_chat_message(message) for message in stored])
    assert all(message["content"] for message in rendered)


async def test_the_budget_nudge_is_appended_once(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    """Repeating it every round would crowd the question out of the history."""
    await runner.llm.settings.store("chatMaxToolCalls", 2)
    asking = ChatTurn(
        tool_calls=[ChatToolCall(id="c", name="list_sets", arguments={})], stop_reason="tool_use"
    )
    chat_provider.chat_script = [asking, asking, ChatTurn(text="Done.")]

    await send(runner, tasks, thread)

    sent = chat_provider.chat_calls[-1].messages
    nudges = [
        message for message in sent if message.role == "user" and "tool budget" in message.content
    ]
    assert len(nudges) == 1
    assert chat_provider.chat_calls[-1].tools == []


async def test_a_slow_tool_does_not_hang_the_run(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    """The per-tool timeout is inside the dispatcher; the run carries on."""
    spec = runner.tools.get("query_shots")
    assert spec is not None
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[
                ChatToolCall(
                    id="c1",
                    name="query_shots",
                    arguments={
                        "sql": (
                            "WITH RECURSIVE spin(n) AS (SELECT 1 UNION ALL "
                            "SELECT n + 1 FROM spin WHERE n < 100000000) SELECT COUNT(*) FROM spin"
                        )
                    },
                )
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="That query was too slow; here is a narrower one."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.status == "ok"
    assert chat_provider.chat_calls[1].messages[-1].tool_results[0].ok is False


# -- cancellation ----------------------------------------------------------


async def test_cancel_mid_run_stores_a_cancelled_row_and_a_cancelled_event(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_delay = 0.2
    chat_provider.chat_script = [ChatTurn(text="never finished")]

    run, _ = await runner.send(thread, "take your time", tasks=tasks)
    await asyncio.sleep(0.02)
    await runner.cancel(run.id)
    await finish(tasks, run.id)

    stored = await ChatRepository(runner.db).get_run(run.id)
    assert stored is not None
    assert stored.status == "cancelled"
    assert kinds(await ChatEventsRepository(runner.db).since(run.id))[-1] == "cancelled"


async def test_cancelling_a_finished_run_is_not_an_error(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    run_id = await send(runner, tasks, thread)

    row = await runner.cancel(run_id)

    assert row.status == "ok", "a cancel after the fact must not rewrite the outcome"


async def test_a_run_left_running_by_a_restart_is_reconciled(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    """A `running` row is only true while a process holds it."""
    run = await ChatRepository(runner.db).start_run(thread)

    assert await runner.reconcile() == 1

    stored = await ChatRepository(runner.db).get_run(run.id)
    assert stored is not None
    assert stored.status == "interrupted"
    assert stored.error


# -- failure ---------------------------------------------------------------


async def test_a_provider_failure_is_a_stored_row_and_an_error_event(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [LlmApiError("no credit left", status=429)]

    run_id = await send(runner, tasks, thread)

    stored = await ChatRepository(runner.db).get_run(run_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error is not None and stored.error.startswith("rate_limited")
    events = await ChatEventsRepository(runner.db).since(run_id)
    assert events[-1].kind == "error"
    assert events[-1].data["code"] == "rate_limited"


# -- the stream ------------------------------------------------------------


async def test_a_reconnect_replays_everything_after_the_last_sequence(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [ChatTurn(text="one two three")]

    run_id = await send(runner, tasks, thread)

    whole = await runner.replay(run_id)
    assert len(whole) > 2
    resumed = await runner.replay(run_id, after_seq=int(whole[0].data["seq"]))
    assert len(resumed) == len(whole) - 1
    assert [event.data["seq"] for event in resumed] == [event.data["seq"] for event in whole[1:]]


async def test_events_are_published_on_the_bus_as_well_as_stored(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    bus: EventBus[SseEvent],
) -> None:
    chat_provider.chat_script = [ChatTurn(text="live")]
    seen: list[str] = []

    with bus.subscribe() as queue:
        run_id = await send(runner, tasks, thread)
        while not queue.empty():
            seen.append(queue.get_nowait().data["kind"])

    assert "completed" in seen
    assert await ChatEventsRepository(runner.db).since(run_id)


# -- the history budget ----------------------------------------------------


async def test_the_oldest_messages_are_dropped_to_fit_the_budget(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    await runner.llm.settings.store("chatHistoryTokenBudget", 1000)
    chat_provider.chat_script = [ChatTurn(text="ok")]
    for index in range(6):
        await send(runner, tasks, thread, f"question {index} " + "x" * 1500)

    sent = chat_provider.chat_calls[-1].messages

    assert sent, "the newest question is always kept"
    assert len(sent) < len(await ChatRepository(runner.db).messages(thread))
    assert sent[0].role != "tool", "an orphaned tool result is a 400 on both APIs"


async def test_the_system_prompt_carries_the_set_scope(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, thread)

    system = chat_provider.chat_calls[0].system
    assert "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET" in system
    assert f"Set {archive.set_id}" in system
    # Not only the Set: the experiment, which is what a grade is made against.
    assert "THE EXPERIMENT SO FAR" in system


async def test_the_set_scope_opens_with_as_many_shots_as_the_setting_says(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [ChatTurn(text="ok")]
    await send(runner, tasks, thread)
    await runner.llm.settings.store("chatRecentShots", 2)
    await send(runner, tasks, thread, "and now?")

    first, second = (call.system for call in chat_provider.chat_calls)
    assert "ALL 6 SHOTS OF v1 (newest first)" in first
    assert "THE LAST 2 OF 6 SHOTS OF v1 (newest first)" in second
    assert f"shot {archive.shots[-1]}\n" in second
    assert f"shot {archive.shots[-2]}\n" in second
    assert f"shot {archive.shots[-3]}\n" not in second


async def test_a_curve_in_base_reaches_the_context_cut_to_the_setting_s_rows(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression `scripts/repro_base_curve_never_loaded.py` reproduces, for the context.

    The opening context loaded its shots without samples, so a curve channel
    moved into base never reached it. It now reads them exactly when base
    carries a curve, and cuts the curve to `chatCurvePoints` read that turn.
    """
    reads: list[list[int]] = []
    real = ShotsRepository.samples_for

    async def counted(self: ShotsRepository, shot_ids: Any) -> Any:
        reads.append(list(shot_ids))
        return await real(self, shot_ids)

    monkeypatch.setattr(ShotsRepository, "samples_for", counted)
    chat_provider.chat_script = [ChatTurn(text="ok")]
    await send(runner, tasks, thread)
    assert reads == [], "at the default tiers the opening context reads no samples"
    await ShotInfoTiersRepository(archive.db).set_tier(
        ShotInfoTierWrite(item_key="curve_pressure", tier="base")
    )
    await runner.llm.settings.store("chatCurvePoints", 10)
    await send(runner, tasks, thread, "and now?")
    await runner.llm.settings.store("chatCurvePoints", 30)
    await send(runner, tasks, thread, "and now?")

    first, second, third = (call.system for call in chat_provider.chat_calls)
    assert len(reads) == 2, "one query per turn for every shot"

    def shot(system: str) -> str:
        """The newest shot's rendering in the opening context, up to the blank line after it."""
        return system.split(f"\nshot {archive.shots[-1]}\n", 1)[1].split("\n\n", 1)[0]

    def curve(system: str) -> list[str]:
        """The curve group's lines, up to the next group."""
        lines = shot(system).split("[Curve]\n", 1)[1].splitlines()
        return lines[: next((i for i, x in enumerate(lines) if x.startswith("[")), len(lines))]

    assert "[Curve]" not in shot(first)
    ten, thirty = curve(second), curve(third)
    assert ten[0].startswith(tuple(f"{n} of 112 samples" for n in range(1, 30)))
    assert ten[1] == "t (s),pressure (bar)"
    rows_ten = int(ten[0].split()[0])
    rows_thirty = int(thirty[0].split()[0])
    # A target, plus the moments that are always kept (at most eleven here:
    # the ends, three phases' edges, peak, drip and the drop's two samples).
    assert rows_ten <= 10 + 11
    assert rows_ten < rows_thirty <= 30
    assert len(ten) == 2 + rows_ten and len(thirty) == 2 + rows_thirty


async def test_a_tier_moved_between_turns_reaches_the_next_turn_s_context_and_glossary(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """Read at the start of every turn: no restart, and nothing cached across turns."""
    chat_provider.chat_script = [ChatTurn(text="ok")]
    await send(runner, tasks, thread)
    repo = ShotInfoTiersRepository(archive.db)
    await repo.set_tier(ShotInfoTierWrite(item_key="score_confidence", tier="base"))
    await repo.set_tier(ShotInfoTierWrite(item_key="rating", tier="excluded"))
    await send(runner, tasks, thread, "and now?")

    first, second = (call.system for call in chat_provider.chat_calls)
    # The opening context's shots, rendered in base.
    assert "\nScore confidence: high" not in first
    assert "\nScore confidence: high" in second
    assert re.search(r"\nRating: \d/5", first)
    assert not re.search(r"\nRating: \d/5", second)
    # And the glossary follows: the moved item under its new tier, the
    # excluded one no longer explained.
    assert "- Score confidence [" not in first, "extended entries are not in the prompt"
    assert "- Score confidence [base]: " in second
    assert "- Rating [base]: " in first
    assert "- Rating [" not in second


async def test_the_set_and_general_prompts_carry_the_shot_field_glossary(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    from gaggiclanker.db.repos.chat import ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, thread)
    await send(runner, tasks, created.thread.id, "which beans did I like?")

    base = render_glossary(default_tiers(), "base")
    extended_items = [i for i in CATALOGUE if default_tiers()[i.key] == "extended"]
    assert extended_items
    for request in chat_provider.chat_calls:
        assert base in request.system
        assert request.system.index("PROPOSE, NEVER ACT") < request.system.index("SHOT FIELDS")
        # The extended half is not in the prompt: it rides with an extended read.
        assert "SHOT FIELDS, EXTENDED" not in request.system
        for item in extended_items:
            assert f"- {item.label} [" not in request.system, item.key
            assert item.meaning not in request.system, item.key
        for name in ("get_shot_extended", "get_shot_full", "compare_shots"):
            assert (
                name in request.system.split("SHOT FIELDS")[1].split("extended fields are not")[1]
            )


async def test_an_unscoped_thread_gets_no_scope_block(
    runner: ChatRunner, tasks: TaskRegistry, archive: Fixture, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.chat import ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    plain = created.thread
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, plain.id, "what is a 1:2 ratio?")

    assert "THIS CONVERSATION IS ABOUT" not in chat_provider.chat_calls[0].system


# -- the tool surface handed to the provider -------------------------------


async def test_a_general_thread_is_given_the_archive_s_tools(
    runner: ChatRunner, tasks: TaskRegistry, archive: Fixture, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.chat import ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, created.thread.id, "what is a 1:2 ratio?")

    names = {schema["function"]["name"] for schema in chat_provider.chat_calls[0].tools}
    assert names == GENERAL_TOOLS


async def test_the_provider_is_given_the_chat_tool_set(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, thread)

    names = {schema["function"]["name"] for schema in chat_provider.chat_calls[0].tools}
    # The Set conversation's surface, because that is what this thread is: the
    # archive-wide tools are not merely refused, they are never described.
    assert names == SET_TOOLS
    assert not any(name.startswith("push_") for name in names)


async def test_a_set_being_designed_is_given_the_design_tools_and_a_dispatcher_to_match(
    runner: ChatRunner,
    tasks: TaskRegistry,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """The schemas sent, and the calls allowed, are the design scope — from the Set's flag."""
    designed = await SetsRepository(archive.db).create_design(
        SetWrite(name="Designed", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        DesignBrief(),
    )
    opened = await ChatRepository(archive.db).open_thread(designed.id)
    assert opened.thread is not None
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[
                ChatToolCall(
                    id="c1",
                    name="propose_set_version",
                    arguments={"reason": "Finer.", "grind_setting": "18"},
                )
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="Let us talk about what you want first."),
    ]

    await send(runner, tasks, opened.thread.id, "Help me design this Set")

    names = {schema["function"]["name"] for schema in chat_provider.chat_calls[0].tools}
    assert names == DESIGN_TOOLS
    refused = chat_provider.chat_calls[1].messages[-1].tool_results[0]
    assert refused.ok is False
    assert "propose_initial_recipe" in refused.content


async def test_a_design_is_answered_by_the_design_prompt_until_its_recipe_is_accepted(
    runner: ChatRunner,
    tasks: TaskRegistry,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """Read from the Set on every turn: the turn after Accept is an ordinary Set chat."""
    from gaggiclanker.db.repos.llm import LlmCallsRepository
    from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
    from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
    from gaggiclanker.db.repos.sets import SetVersionPatch

    calls = LlmCallsRepository(archive.db)
    runner.llm.calls_repo = calls
    designed = await SetsRepository(archive.db).create_design(
        SetWrite(name="Designed", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        DesignBrief(goal="More body."),
    )
    opened = await ChatRepository(archive.db).open_thread(designed.id)
    assert opened.thread is not None
    thread_id = opened.thread.id
    chat_provider.chat_script = [ChatTurn(text="What basket?"), ChatTurn(text="Grade it.")]

    first = await send(runner, tasks, thread_id, "Help me design this Set")

    draft = await ProfileDraftsRepository(archive.db).create(
        ProfileDraftWrite(
            base_version_id=archive.profile_version_id,
            draft_version_id=archive.profile_version_id,
        )
    )
    proposals = SetProposalsRepository(archive.db)
    card = await proposals.create(
        designed.id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            thread_id=thread_id,
            reason="The recipe we agreed.",
            patch=SetVersionPatch(profile_version_id=archive.profile_version_id, dose_g=18),
        ),
    )
    assert card.proposal is not None, card.refused
    assert (await proposals.accept(designed.id, card.proposal.id)).refused is None

    second = await send(runner, tasks, thread_id, "It came out fast.")

    design_turn, set_turn = chat_provider.chat_calls
    assert "THIS CONVERSATION IS DESIGNING A NEW SET" in design_turn.system
    assert "GRADE FIRST" not in design_turn.system
    # A design reads no shots, so it is not told what their fields mean.
    assert "SHOT FIELDS" not in design_turn.system
    assert {schema["function"]["name"] for schema in design_turn.tools} == DESIGN_TOOLS
    assert "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET" in set_turn.system
    assert "GRADE FIRST" in set_turn.system
    assert "SHOT FIELDS" in set_turn.system
    # The card it came from is told as the recipe it set, not as an empty change.
    assert "its initial recipe was accepted as v1" in set_turn.system
    assert {schema["function"]["name"] for schema in set_turn.tools} == SET_TOOLS
    rows = {row.call_id: row.prompt_name for row in await calls.recent(limit=10)}
    assert (rows[f"chat-{first}"], rows[f"chat-{second}"]) == ("chat-design", "chat-set")


async def test_a_hand_made_set_that_names_no_profile_is_an_ordinary_set_chat(
    runner: ChatRunner,
    tasks: TaskRegistry,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """Its version 1 looks like an empty design, and nothing about it changes."""
    from gaggiclanker.db.repos.sets import SetVersionWrite

    any_profile = await SetsRepository(archive.db).create(
        SetWrite(name="Any profile", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        SetVersionWrite(),
    )
    opened = await ChatRepository(archive.db).open_thread(any_profile.id)
    assert opened.thread is not None
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, opened.thread.id)

    request = chat_provider.chat_calls[0]
    assert request.system.rstrip().endswith(
        "Those facts are the record, not the whole archive: use the tools for anything else, "
        "and for anything you are about to quote a number from."
    )
    assert "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET" in request.system
    assert "DESIGNING" not in request.system
    assert {schema["function"]["name"] for schema in request.tools} == SET_TOOLS


async def test_the_usage_row_records_which_prompt_answered_the_turn(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """A turn has to be traceable to the instructions that produced it.

    The two kinds of conversation are answered by two different prompts, and
    the ledger row is where that is written down — a reading of what the
    agent said six weeks ago is explainable only if the row names the prompt.
    """
    from gaggiclanker.db.repos.chat import ChatThreadWrite
    from gaggiclanker.db.repos.llm import LlmCallsRepository

    repo = LlmCallsRepository(archive.db)
    runner.llm.calls_repo = repo
    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    chat_provider.chat_script = [ChatTurn(text="ok"), ChatTurn(text="ok")]

    scoped_run = await send(runner, tasks, thread)
    general_run = await send(runner, tasks, created.thread.id, "what is a 1:2 ratio?")

    rows = {row.call_id: row.prompt_name for row in await repo.recent(limit=10)}
    assert rows[f"chat-{scoped_run}"] == "chat-set"
    assert rows[f"chat-{general_run}"] == "chat-general"


async def test_an_empty_message_is_refused_before_a_run_is_opened(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    from gaggiclanker.infra.errors import BadRequest

    with pytest.raises(BadRequest):
        await runner.send(thread, "   ", tasks=tasks)

    assert await ChatRepository(runner.db).messages(thread) == []


# -- the Set scope reaches the provider ------------------------------------


async def test_the_scope_is_on_the_request_so_the_cli_can_carry_it(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """The other providers get the scope through the tool context; the CLI cannot.

    `claude_code` runs the tool loop itself against our MCP server, so the only
    way a scoped thread's `get_set` with no argument answers there is if the
    scope is on the request and ends up in the generated --mcp-config.
    """
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, thread)

    assert chat_provider.chat_calls[0].set_id == archive.set_id
    assert chat_provider.chat_calls[0].set_version_id == archive.version_id


async def test_an_unscoped_thread_sends_no_scope(
    runner: ChatRunner, tasks: TaskRegistry, archive: Fixture, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.chat import ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    plain = created.thread
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, plain.id, "what is a 1:2 ratio?")

    assert chat_provider.chat_calls[0].set_id is None


async def test_a_run_tells_the_conversation_what_the_person_did_with_its_own_insights(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """The runner hands the opening context the thread, so "you proposed" is its own.

    An insight this thread wrote and the person answered appears, with its state,
    in the system prompt the provider receives; a second thread about the same
    version, which wrote nothing, gets no such block.
    """
    insights = InsightsRepository(archive.db)
    added = await insights.insert(
        InsightWrite(
            text="This bag is sweetest one number finer.",
            source="chat",
            set_id=archive.set_id,
            set_version_id=archive.version_id,
            thread_id=thread,
        )
    )
    await insights.set_confirmed(added, True)
    dismissed = await insights.insert(
        InsightWrite(
            text="Longer pre-infusion helps every bag.",
            source="chat",
            set_id=archive.set_id,
            set_version_id=archive.version_id,
            thread_id=thread,
        )
    )
    await insights.dismiss(dismissed)
    other = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(title="", set_id=archive.set_id, set_version_id=archive.version_id)
    )
    assert other.thread is not None
    chat_provider.chat_script = [ChatTurn(text="Noted.")]

    await send(runner, tasks, thread)
    await send(runner, tasks, other.thread.id)

    own, elsewhere = (call.system for call in chat_provider.chat_calls)
    assert "INSIGHTS YOU PROPOSED IN THIS CONVERSATION" in own
    assert (
        "- This bag is sweetest one number finer. "
        "(added by the person — it is in the confirmed list above)"
    ) in own
    assert (
        "- Longer pre-infusion helps every bag. (dismissed by the person — do not offer it again)"
    ) in own
    assert "INSIGHTS YOU PROPOSED IN THIS CONVERSATION" not in elsewhere
    assert "Longer pre-infusion helps every bag." not in elsewhere
