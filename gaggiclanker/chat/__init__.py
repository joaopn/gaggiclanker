"""The chat: threads, the tool loop, and the streamed transcript."""

from __future__ import annotations

from gaggiclanker.chat.context import opening_context, thread_title_from
from gaggiclanker.chat.runner import (
    CHAT_EVENT,
    GENERAL_CHAT_PROMPT,
    SET_CHAT_PROMPT,
    ChatRunner,
    prompt_for,
    run_task_name,
)

__all__ = [
    "CHAT_EVENT",
    "GENERAL_CHAT_PROMPT",
    "SET_CHAT_PROMPT",
    "ChatRunner",
    "opening_context",
    "prompt_for",
    "run_task_name",
    "thread_title_from",
]
