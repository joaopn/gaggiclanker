import { Send, Square } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChatTranscript } from "@/components/chat/ChatTranscript";
import { ThreadList } from "@/components/chat/ThreadList";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  useCancelChatRun,
  useChatRun,
  useChatThread,
  useChatThreads,
  useChatTools,
  useCreateChatThread,
  useDeleteChatThread,
  useSendChatMessage,
} from "@/hooks/useChat";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useSets } from "@/hooks/useSets";

/**
 * The chat, as a page.
 *
 * Three query parameters, and each is a link somebody else's page makes:
 * `?thread=` selects a conversation, `?set=` scopes a *new* one, and `?ask=`
 * prefills the composer. That is what the "Discuss in chat" buttons on a shot
 * and a Set produce — a thread already pointed at the right archive with the
 * question typed, because the alternative is the person retyping "how is Set 3
 * going" into a box that has no idea what Set 3 is.
 *
 * The live answer comes off the run's own SSE stream rather than from the
 * thread query: tokens arrive several a second, and writing each into the cache
 * would re-render every consumer of the thread per token. The stored copy
 * replaces it when the run completes.
 */
export function ChatPage() {
  const [params, setParams] = useSearchParams();
  const threadParam = params.get("thread");
  const setParam = params.get("set");
  const askParam = params.get("ask");

  const threads = useChatThreads();
  const tools = useChatTools();
  const sets = useSets();
  useQueryErrorToast(threads.error, "conversations");

  const [selected, setSelected] = useState<number | null>(threadParam ? Number(threadParam) : null);
  const [draft, setDraft] = useState(askParam ?? "");
  const [runId, setRunId] = useState<number | null>(null);
  const [scope, setScope] = useState<number | null>(setParam ? Number(setParam) : null);

  const thread = useChatThread(selected);
  const createThread = useCreateChatThread();
  const deleteThread = useDeleteChatThread();
  const send = useSendChatMessage();
  const cancel = useCancelChatRun();
  const live = useChatRun(runId, selected);

  const permissions = useMemo(() => {
    const map: Record<string, string> = {};
    for (const tool of tools.data?.tools ?? []) map[tool.name] = tool.permission;
    return map;
  }, [tools.data]);

  // A thread that is still running when the page loads — another tab, or a
  // reload mid-answer. Following it is what makes a refresh harmless.
  useEffect(() => {
    const running = (thread.data?.runs ?? []).find((run) => run.status === "running");
    if (running && runId === null) setRunId(running.id);
  }, [thread.data, runId]);

  // Once the run is over the stored message is authoritative; dropping the id
  // is what takes the live bubble off the screen.
  useEffect(() => {
    if (live.status === "completed" || live.status === "error" || live.status === "cancelled") {
      setRunId(null);
    }
  }, [live.status]);

  // Keep the newest turn in view as tokens arrive. Both values are read inside
  // the effect as well as listed as dependencies: they are what changed, and an
  // effect that only names them in the array reads as a mistake to a linter and
  // to the next person.
  const scrollAnchor = useRef<HTMLDivElement | null>(null);
  const liveText = live.text;
  const messageCount = thread.data?.messages?.length ?? 0;
  useEffect(() => {
    if (liveText === "" && messageCount === 0) return;
    scrollAnchor.current?.scrollIntoView({ block: "end" });
  }, [liveText, messageCount]);

  const select = (id: number) => {
    setSelected(id);
    setRunId(null);
    const next = new URLSearchParams(params);
    next.set("thread", String(id));
    next.delete("set");
    next.delete("ask");
    setParams(next, { replace: true });
  };

  const startThread = async () => {
    const created = await createThread.mutateAsync({ setId: scope });
    select(created.id);
  };

  const submit = async () => {
    const message = draft.trim();
    if (!message) return;
    let threadId = selected;
    if (threadId === null) {
      // Asking without having pressed "New conversation" is the common case,
      // so the first question creates the thread rather than refusing.
      const created = await createThread.mutateAsync({ setId: scope });
      threadId = created.id;
      setSelected(created.id);
    }
    setDraft("");
    const result = await send.mutateAsync({ threadId, message });
    setRunId(result.run.id);
  };

  const busy = runId !== null;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Chat"
        subtitle="Ask about the archive. It reads shots, Sets and the knowledge base itself."
      />

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <SectionCard title="Conversations">
          {threads.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <ThreadList
              threads={threads.data ?? []}
              selectedId={selected}
              onSelect={select}
              onNew={() => void startThread()}
              onDelete={(id) => {
                void deleteThread.mutateAsync(id).then(() => {
                  if (id === selected) setSelected(null);
                });
              }}
              busy={createThread.isPending}
            />
          )}

          <div className="mt-3 border-border border-t pt-3">
            <label
              className="mb-1 block font-medium text-muted-foreground text-xs"
              htmlFor="chat-scope"
            >
              Scope a new conversation
            </label>
            {/* A plain select: the list is short, and the shadcn one is a
                portal whose options a component test cannot see. */}
            <select
              id="chat-scope"
              className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              value={scope === null ? "" : String(scope)}
              onChange={(event) =>
                setScope(event.target.value === "" ? null : Number(event.target.value))
              }
            >
              <option value="">No Set — general questions</option>
              {(sets.data?.items ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-muted-foreground text-xs">
              A scoped conversation starts with that Set's recipe, its recent shots and the insights
              that apply to it.
            </p>
          </div>
        </SectionCard>

        <SectionCard
          title={thread.data?.thread.title || "New conversation"}
          description={
            thread.data?.thread.set_name
              ? `Scoped to ${thread.data.thread.set_name}`
              : "Not scoped to a Set"
          }
        >
          <div className="max-h-[60vh] min-h-40 overflow-y-auto pr-1">
            {selected === null ? (
              <p className="text-muted-foreground text-sm">
                Ask something below. "How is this Set going?", "did the grind change in v3 fix the
                channeling?", "why does flow deviation matter more than pressure overshoot?"
              </p>
            ) : thread.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <ChatTranscript
                messages={thread.data?.messages ?? []}
                runs={thread.data?.runs ?? []}
                permissions={permissions}
                live={live}
              />
            )}
            <div ref={scrollAnchor} />
          </div>

          <form
            className="mt-3 flex items-end gap-2 border-border border-t pt-3"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <Textarea
              aria-label="Message"
              placeholder="Ask about a shot, a Set, or espresso in general…"
              value={draft}
              rows={2}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter sends, shift+Enter is a newline: this is a chat box,
                // and a Send button nobody can reach from the keyboard is not.
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
            />
            {busy ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (runId !== null) void cancel.mutateAsync(runId);
                }}
              >
                <Square className="size-4" aria-hidden="true" />
                Stop
              </Button>
            ) : (
              <Button type="submit" disabled={!draft.trim() || send.isPending}>
                <Send className="size-4" aria-hidden="true" />
                Send
              </Button>
            )}
          </form>

          <UsageFooter runs={thread.data?.runs ?? []} />
        </SectionCard>
      </div>
    </div>
  );
}

/** What this conversation has cost, as far as the providers disclosed it. */
function UsageFooter({ runs }: { runs: Array<{ usage?: Record<string, unknown> | null }> }) {
  const totals = runs.reduce(
    (accumulator, run) => {
      const usage = run.usage ?? {};
      return {
        input: accumulator.input + Number(usage.prompt_tokens ?? 0),
        output: accumulator.output + Number(usage.completion_tokens ?? 0),
      };
    },
    { input: 0, output: 0 },
  );
  if (totals.input === 0 && totals.output === 0) return null;
  return (
    <p className="mt-2 text-muted-foreground text-xs" data-testid="chat-usage">
      {totals.input.toLocaleString()} in · {totals.output.toLocaleString()} out across {runs.length}{" "}
      turn{runs.length === 1 ? "" : "s"}
    </p>
  );
}
