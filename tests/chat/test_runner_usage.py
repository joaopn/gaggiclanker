"""What a run and its messages store as usage, per provider shape.

The Claude Code case replays a real capture through the provider, so the stored
figures are the ones the footer will show for that stream.
"""

from __future__ import annotations

from gaggiclanker.chat.runner import ChatRunner
from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.chat_types import ChatToolCall, ChatTurn
from gaggiclanker.llm.types import Usage, request_usage
from tests.chat.test_runner import send
from tests.llm.conftest import FakeProvider
from tests.llm.test_chat_usage_stream import replay


async def test_a_claude_code_run_stores_the_context_the_cache_split_and_the_window(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    turn = await replay("run1_four_requests")
    turn.text = "Done."
    chat_provider.chat_script = [turn]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.usage is not None
    usage = run.usage
    assert usage["context_tokens"] == 21741
    assert usage["context_window"] == 200000
    assert (usage["cache_read"], usage["cache_write"], usage["fresh"]) == (77985, 7930, 35)
    assert usage["requests"] == 4
    assert usage["prompt_tokens"] == 85950 and usage["completion_tokens"] == 431
    assert usage["cost_usd"] == 0.025848499999999996
    assert usage["per_request"][0] == {
        "context": 21146,
        "cache_read": 13803,
        "cache_write": 7334,
        "fresh": 9,
        "out": 183,
    }
    assert len(usage["per_request"]) == 4


async def test_each_message_holds_only_the_requests_that_produced_it(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    turn = await replay("run1_four_requests")
    turn.text = "Done."
    chat_provider.chat_script = [turn]

    run_id = await send(runner, tasks, thread)

    messages = [m for m in await ChatRepository(runner.db).messages(thread) if m.run_id == run_id]
    parts = [m.usage for m in messages if m.role == "assistant" and m.usage]
    # The tool-call message holds the three requests that issued the calls, the
    # answer the fourth; together they are the run, with nothing counted twice.
    assert [[r["context"] for r in p["per_request"]] for p in parts] == [
        [21146, 21460, 21603],
        [21741],
    ]
    assert sum(p["completion_tokens"] for p in parts) == 431


async def test_api_providers_store_one_request_per_message_and_no_window(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    first = request_usage(fresh=100, cache_write=50, cache_read=1000, out=20)
    second = request_usage(fresh=10, cache_write=0, cache_read=1200, out=40)
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[ChatToolCall(id="c1", name="get_shot", arguments={"shot_id": 1})],
            usage=first,
            stop_reason="tool_use",
        ),
        ChatTurn(text="Fine.", usage=second),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.usage is not None
    assert run.usage["context_tokens"] == 1210  # the last request, not 2380
    assert run.usage["prompt_tokens"] == 1150 + 1210
    assert run.usage["requests"] == 2
    assert "context_window" not in run.usage and "cost_usd" not in run.usage
    messages = [m for m in await ChatRepository(runner.db).messages(thread) if m.usage]
    assert [(m.usage or {}).get("requests") for m in messages] == [1, 1]


async def test_an_openai_style_run_stores_cached_tokens_as_a_read_and_no_write(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.llm.types import RequestUsage

    usage = Usage(
        prompt_tokens=900,
        completion_tokens=30,
        cache_read_tokens=600,
        fresh_tokens=300,
        requests=(RequestUsage(context=900, cache_read=600, fresh=300, out=30),),
    )
    chat_provider.chat_script = [ChatTurn(text="ok", usage=usage)]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.usage is not None
    assert (run.usage["context_tokens"], run.usage["cache_read"], run.usage["fresh"]) == (
        900,
        600,
        300,
    )
    assert "cache_write" not in run.usage


async def test_a_provider_that_reports_nothing_stores_no_usage(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [ChatTurn(text="ok")]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.usage is None


async def test_the_ledger_row_carries_the_cache_split_and_the_context(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.llm import LlmCallsRepository

    runner.llm.calls_repo = LlmCallsRepository(runner.db)
    turn = await replay("run1_four_requests")
    turn.text = "Done."
    chat_provider.chat_script = [turn]

    run_id = await send(runner, tasks, thread)

    row = await runner.db.fetch_one(
        "SELECT input_tokens, cache_read_tokens, cache_write_tokens, context_tokens "
        "FROM llm_calls WHERE call_id = ?",
        (f"chat-{run_id}",),
    )
    assert row is not None
    assert tuple(row) == (85950, 77985, 7930, 21741)
