import { Loader2 } from "lucide-react";
import type { ChatMessage, ChatRun } from "@/api/types";
import { AnswerText } from "@/components/chat/citations";
import { ToolTrace } from "@/components/chat/ToolTrace";
import type { TraceEntry } from "@/hooks/useChat";
import { cn } from "@/lib/utils";

/**
 * The conversation as it is read: questions, answers, and what was called.
 *
 * A stored `tool` message and the assistant turn that asked for it are folded
 * into one trace under that answer, because that is how a person reads it —
 * "it looked these things up, then said this". The transcript keeps them apart
 * (the next turn has to be shown exactly what the model was shown), so the
 * folding happens here rather than in the database.
 *
 * The live answer is rendered as one more bubble at the end. It is deliberately
 * *not* merged into the message list: it has no id yet, and a list that mixed
 * stored rows with a synthetic one would flicker when the stored copy arrives.
 */

export type ChatTranscriptProps = {
  messages: ChatMessage[];
  runs: ChatRun[];
  permissions: Record<string, string>;
  live?: { text: string; trace: TraceEntry[]; status: string } | null;
};

type Turn = {
  key: string;
  role: "user" | "assistant";
  content: string;
  trace: TraceEntry[];
  usage?: Record<string, unknown> | null;
};

/** Fold the stored rows into the turns a reader sees. */
export function toTurns(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  let pending: TraceEntry[] = [];

  for (const message of messages) {
    if (message.role === "user") {
      turns.push({ key: `m${message.id}`, role: "user", content: message.content, trace: [] });
      continue;
    }
    if (message.role === "assistant") {
      const calls = (message.tool_calls ?? []) as Array<Record<string, unknown>>;
      if (calls.length > 0) {
        // An assistant turn that only called tools is not a bubble: it becomes
        // the trace attached to whatever it eventually answered.
        pending = pending.concat(
          calls.map((call) => ({
            id: String(call.id ?? ""),
            name: String(call.name ?? "tool"),
            arguments: (call.arguments ?? {}) as Record<string, unknown>,
          })),
        );
        if (!message.content) continue;
      }
      turns.push({
        key: `m${message.id}`,
        role: "assistant",
        content: message.content,
        trace: pending,
        usage: message.usage ?? null,
      });
      pending = [];
      continue;
    }
    if (message.role === "tool") {
      const results = (message.tool_results ?? []) as Array<Record<string, unknown>>;
      pending = pending.map((entry) => {
        const match = results.find((result) => String(result.id ?? "") === entry.id);
        return match
          ? {
              ...entry,
              ok: Boolean(match.ok),
              content: String(match.content ?? ""),
            }
          : entry;
      });
    }
  }

  // A run that failed or was cancelled mid-loop leaves calls with no answer
  // after them. Showing them is the difference between "it did nothing" and
  // "it looked, then the provider fell over".
  if (pending.length > 0) {
    turns.push({ key: "orphan-trace", role: "assistant", content: "", trace: pending });
  }
  return turns;
}

export function ChatTranscript({ messages, runs, permissions, live }: ChatTranscriptProps) {
  const turns = toTurns(messages);
  const lastRun = runs.length > 0 ? runs[runs.length - 1] : null;

  return (
    <div className="space-y-4" data-testid="chat-transcript">
      {turns.map((turn) => (
        <div key={turn.key} className={cn(turn.role === "user" ? "flex justify-end" : "")}>
          <div
            className={cn(
              "max-w-full space-y-2",
              turn.role === "user"
                ? "rounded-lg rounded-br-sm bg-primary/10 px-3 py-2 text-sm"
                : "",
            )}
          >
            {turn.trace.length > 0 ? (
              <ToolTrace entries={turn.trace} permissions={permissions} />
            ) : null}
            {turn.content ? (
              turn.role === "assistant" ? (
                <AnswerText text={turn.content} />
              ) : (
                <p className="whitespace-pre-wrap">{turn.content}</p>
              )
            ) : null}
          </div>
        </div>
      ))}

      {live && live.status === "running" ? (
        <div className="space-y-2" data-testid="live-answer">
          {live.trace.length > 0 ? (
            <ToolTrace entries={live.trace} permissions={permissions} live />
          ) : null}
          {live.text ? (
            <AnswerText text={live.text} />
          ) : (
            <p className="flex items-center gap-2 text-muted-foreground text-sm">
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              Thinking…
            </p>
          )}
        </div>
      ) : null}

      {lastRun && lastRun.status === "failed" ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">
          {lastRun.error ?? "That turn failed."}
        </p>
      ) : null}
      {lastRun && lastRun.status === "cancelled" ? (
        <p className="text-muted-foreground text-sm">Cancelled.</p>
      ) : null}
    </div>
  );
}
