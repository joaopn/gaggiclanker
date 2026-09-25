import { Send, Square } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChatTranscript } from "@/components/chat/ChatTranscript";
import { ThreadFolders } from "@/components/chat/ThreadFolders";
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
  useOpenChatThread,
  useSendChatMessage,
} from "@/hooks/useChat";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useSets } from "@/hooks/useSets";

/**
 * The chat, as a page.
 *
 * Four query parameters, and each is a link somebody else's page makes:
 * `?thread=` selects a conversation, `?set=` scopes a *new* one, `?set=&version=`
 * opens or continues the conversation about that version, and `?ask=` prefills
 * the composer. That is what the Discuss buttons produce — a conversation
 * already pointed at the right experiment with the question typed, because the
 * alternative is the person retyping "how is Set 3 going" into a box that has
 * no idea what Set 3 is.
 *
 * `?set=` alone creates nothing: it says where a first question would land, and
 * a mis-click leaves no empty conversation behind. `?set=&version=` is the
 * other intent — "take me to the room where this change is being argued" — and
 * that room is a thing that exists, so it is opened through the server's own
 * open-or-continue route rather than made afresh.
 *
 * Which Set a conversation is about is the shape of the list rather than a
 * control beside it: a folder per Set, and New inside the folder. `scope` is
 * what a *first question* would be filed under when no conversation is
 * selected — set by the last New pressed or by a `?set=` link — and the
 * composer's card says so, because a question that quietly became a general
 * one is a question answered without the bag in front of it.
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
  const versionParam = params.get("version");
  const askParam = params.get("ask");

  const threads = useChatThreads();
  const sets = useSets();
  useQueryErrorToast(threads.error, "conversations");

  const [selected, setSelected] = useState<number | null>(threadParam ? Number(threadParam) : null);
  const [draft, setDraft] = useState(askParam ?? "");
  const [runId, setRunId] = useState<number | null>(null);
  const [scope, setScope] = useState<number | null>(setParam ? Number(setParam) : null);

  const thread = useChatThread(selected);
  const createThread = useCreateChatThread();
  const openThread = useOpenChatThread();
  const deleteThread = useDeleteChatThread();
  const send = useSendChatMessage();
  const cancel = useCancelChatRun();
  const live = useChatRun(runId, selected);

  // Which surface this conversation has: the selected thread's, or — with
  // nothing selected — the one a first question would be filed under. Asked of
  // the server per kind; the page never filters a list of its own.
  //
  // NULL while a selected conversation is still loading. Falling back to
  // "general" there would be a guess, and the guess is on screen: the archive's
  // tool list would flash beside a Set's chat and then be replaced.
  const kind: "general" | "set" | null =
    selected === null
      ? scope === null
        ? "general"
        : "set"
      : thread.data === undefined
        ? null
        : thread.data.thread.set_id === null
          ? "general"
          : "set";
  // Whether that Set is still being designed, which is a third tool list: the
  // design tools, and none that change a recipe it does not have yet. Read off
  // the Sets list the folders already need rather than off the thread, so an
  // accepted first recipe — which invalidates that list — switches the tools
  // on the next render. NULL until the list has arrived, for the same reason
  // as the kind: the ordinary Set list flashing beside a design would be a guess.
  const conversationSet = selected === null ? scope : (thread.data?.thread.set_id ?? null);
  const designing: boolean | null =
    kind !== "set"
      ? false
      : sets.isPending
        ? null
        : Boolean((sets.data?.items ?? []).find((row) => row.id === conversationSet)?.designing);
  const tools = useChatTools(designing === null ? null : kind, designing ?? false);

  const permissions = useMemo(() => {
    const map: Record<string, string> = {};
    for (const tool of tools.data?.tools ?? []) map[tool.name] = tool.permission;
    return map;
  }, [tools.data]);

  // `?set=&version=` is Discuss: open or continue that version's conversation,
  // once. The guard is a ref written inside the effect rather than state: two
  // passes of an effect that has already fired would be two rooms about one
  // change, and the answer is not a render's worth of state.
  const openedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!setParam || !versionParam) return;
    const key = `${setParam}:${versionParam}`;
    if (openedFor.current === key) return;
    openedFor.current = key;
    void openThread
      .mutateAsync({ setId: Number(setParam), setVersionId: Number(versionParam) })
      .then((row) => {
        setSelected(row.id);
        setRunId(null);
        const next = new URLSearchParams(params);
        next.set("thread", String(row.id));
        next.delete("set");
        next.delete("version");
        next.delete("ask");
        setParams(next, { replace: true });
      });
    // The draft is state by now, so dropping `ask` from the URL keeps the
    // typed question on screen.
  }, [setParam, versionParam, openThread, params, setParams]);

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

  const startThread = async (setId: number | null) => {
    // The folder decides the scope, and it decides it now rather than at the
    // first question: pressing New under a Set is somebody saying what they are
    // about to ask about.
    setScope(setId);
    const created = await createThread.mutateAsync({ setId });
    select(created.id);
  };

  //: The Set a first question would be filed under, for the composer to say so
  //: — with the version it would land on, which is the Set's current one.
  const scopeSet = (sets.data?.items ?? []).find((row) => row.id === scope) ?? null;

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

      {/* `min-w-0` on both tracks: the left one is a fixed 260px and the right
          one is `1fr`, and without it a long Set name or conversation title
          makes the grid item wider than its track instead of truncating. */}
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <SectionCard
          title="Conversations"
          description="One folder per Set. New starts a conversation about that Set."
          className="min-w-0"
          contentClassName="min-w-0"
        >
          {threads.isLoading || sets.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <ThreadFolders
              threads={threads.data ?? []}
              sets={sets.data?.items ?? []}
              selectedId={selected}
              scopedSetId={scope}
              onSelect={select}
              onNew={(setId) => void startThread(setId)}
              onDelete={(id) => {
                void deleteThread.mutateAsync(id).then(() => {
                  if (id === selected) setSelected(null);
                });
              }}
              busy={createThread.isPending}
            />
          )}
        </SectionCard>

        <SectionCard
          className="min-w-0"
          title={
            selected === null ? "New conversation" : thread.data?.thread.title || "New conversation"
          }
          description={
            selected !== null
              ? thread.data?.thread.set_name
                ? `About ${thread.data.thread.set_name} v${thread.data.thread.set_version_no}` +
                  (thread.data.thread.dead_end ? " — a dead end a later roll back went past" : "")
                : "General"
              : // Nothing selected: the first question creates the conversation,
                // and this is the only place that says where it will land.
                scopeSet
                ? `A new conversation about ${scopeSet.name} v${scopeSet.current_version_no}`
                : "General"
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

          <ToolsHere
            tools={(tools.data?.tools ?? []).map((tool) => tool.name)}
            kind={designing === null ? null : kind}
            designing={designing ?? false}
          />
          <UsageFooter runs={thread.data?.runs ?? []} />
        </SectionCard>
      </div>
    </div>
  );
}

/**
 * What the agent can do in this conversation, in its own words.
 *
 * The list comes from the server, per kind, and is not filtered here: a Set's
 * conversation cannot query the archive and a general one cannot change a Set,
 * and the page promising otherwise would be the page lying.
 */
function ToolsHere({
  tools,
  kind,
  designing,
}: {
  tools: string[];
  kind: "general" | "set" | null;
  designing: boolean;
}) {
  // Nothing at all until the kind is known: a list that says the wrong thing
  // for a moment is worse than a line that arrives a moment later.
  if (kind === null || tools.length === 0) return null;
  return (
    <p className="mt-2 text-muted-foreground text-xs" data-testid="chat-tools">
      {kind === "set"
        ? designing
          ? "It is designing this Set's first recipe with you and can propose it as one card. Tools here: "
          : "It can see this Set only. Tools here: "
        : "It can read the whole archive and change no Set. Tools here: "}
      {tools.join(", ")}
    </p>
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
