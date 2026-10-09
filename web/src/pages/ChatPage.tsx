import { Download, Send, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { chatTranscriptUrl, downloadFile } from "@/api/client";
import { ChatTranscript } from "@/components/chat/ChatTranscript";
import {
  buildFolders,
  ConversationPicker,
  defaultFolderKey,
  folderKey,
} from "@/components/chat/ConversationPicker";
import { ChatThreadContext, TellAgentContext } from "@/components/chat/tellAgent";
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
import { loadChatPosition, saveChatPosition } from "@/lib/chatPosition";
import { contextFooter, latestUsage } from "@/lib/chatUsage";
import { attempt } from "@/lib/mutations";

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
 * With none of them — the sidebar's link, the `g c` chord — the page goes back
 * to where the person was in this browser: the conversation that was open and
 * the badge they picked (`lib/chatPosition.ts`). Without that, clicking away
 * and back always landed on the newest Set with nothing open.
 *
 * `?set=` alone creates nothing: it says where a first question would land, and
 * a mis-click leaves no empty conversation behind. `?set=&version=` is the
 * other intent — "take me to the room where this change is being argued" — and
 * that room is a thing that exists, so it is opened through the server's own
 * open-or-continue route rather than made afresh.
 *
 * Which Set a conversation is about is the first thing picked: a badge per Set
 * above the chat, the open badge's conversations under it, one per line, and
 * New at the top of that list. The page opens on the newest Set's badge unless
 * a conversation or a `?set=` link says otherwise. `scope` is what a *first
 * question* would be filed under when no conversation is selected — the open
 * badge's Set — and the composer's card says so, because a question that
 * quietly became a general one is a question answered without the bag in
 * front of it.
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

  // Read once, at mount, and only when no link says where to go.
  const [remembered] = useState(() =>
    threadParam || setParam || versionParam || askParam ? null : loadChatPosition(),
  );
  const [selected, setSelected] = useState<number | null>(
    threadParam ? Number(threadParam) : (remembered?.thread ?? null),
  );
  const [draft, setDraft] = useState(askParam ?? "");
  const [runId, setRunId] = useState<number | null>(null);
  // The badge the person opened, over the page's own choice (`defaultFolderKey`).
  // Seeded by a `?set=` link, which is somebody else's page picking for them.
  const [pickedFolder, setPickedFolder] = useState<string | null>(
    setParam ? folderKey(Number(setParam)) : (remembered?.folder ?? null),
  );

  const thread = useChatThread(selected);
  const createThread = useCreateChatThread();
  const openThread = useOpenChatThread();
  const deleteThread = useDeleteChatThread();
  const send = useSendChatMessage();
  const cancel = useCancelChatRun();
  const live = useChatRun(runId, selected);

  // A remembered conversation may have been deleted since (another tab, the
  // Set page). Fetching it is the check, since the page fetches it anyway: a
  // failure forgets it and opens as if nothing was remembered, and only once
  // it has loaded does the URL name it, so a reload never asks for a missing
  // one by link.
  const [checking, setChecking] = useState<number | null>(remembered?.thread ?? null);
  useEffect(() => {
    if (checking === null) return;
    if (selected !== checking) {
      setChecking(null);
    } else if (thread.isSuccess) {
      setChecking(null);
      const next = new URLSearchParams(params);
      next.set("thread", String(checking));
      setParams(next, { replace: true });
    } else if (thread.isError) {
      setChecking(null);
      setSelected(null);
    }
  }, [checking, selected, thread.isSuccess, thread.isError, params, setParams]);
  useEffect(() => {
    saveChatPosition({ thread: selected, folder: pickedFolder });
  }, [selected, pickedFolder]);

  const setRows = sets.data?.items;
  const folders = useMemo(
    () => buildFolders(threads.data ?? [], setRows ?? []),
    [threads.data, setRows],
  );
  // Archived Sets' badges are behind a toggle, off by default. Opening a
  // conversation about an archived Set (a link, a reload) turns it on once for
  // that conversation, so its badge is on screen; turning it off again is the
  // person's call. Reset during render, keyed by the conversation (see the web
  // rules on state that follows a prop).
  const [showArchived, setShowArchived] = useState(false);
  const [archivedShownFor, setArchivedShownFor] = useState<number | null>(null);
  const selectedFolder =
    selected === null
      ? null
      : (folders.find((folder) => folder.threads.some((row) => row.id === selected)) ?? null);
  if (selectedFolder?.archived && archivedShownFor !== selected) {
    setArchivedShownFor(selected);
    setShowArchived(true);
  }
  const shownFolders = showArchived ? folders : folders.filter((folder) => !folder.archived);
  const openFolderKey =
    pickedFolder !== null && shownFolders.some((folder) => folder.key === pickedFolder)
      ? pickedFolder
      : defaultFolderKey({
          folders: shownFolders,
          sets: setRows ?? [],
          selectedId: selected,
          linkedSetId: setParam ? Number(setParam) : null,
        });
  // A first question lands in the open badge's Set. An archived Set has no
  // New, so a first question there is a general one, and the card below says so.
  const openFolder = shownFolders.find((folder) => folder.key === openFolderKey) ?? null;
  const scope = openFolder?.canCreate ? openFolder.setId : null;

  const [downloading, setDownloading] = useState(false);
  async function saveTranscript(id: number) {
    setDownloading(true);
    try {
      await downloadFile(chatTranscriptUrl(id), `chat-${id}.json`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The log could not be downloaded");
    } finally {
      setDownloading(false);
    }
  }

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
    // The badge follows the conversation: one picked from a list keeps it
    // open, and one opened from elsewhere opens its own.
    const holding = folders.find((folder) => folder.threads.some((row) => row.id === id));
    if (holding) setPickedFolder(holding.key);
    setSelected(id);
    setRunId(null);
    const next = new URLSearchParams(params);
    next.set("thread", String(id));
    next.delete("set");
    next.delete("ask");
    setParams(next, { replace: true });
  };

  const startThread = async (setId: number | null) => {
    // The badge decides the scope, and it decides it now rather than at the
    // first question: pressing New under a Set is somebody saying what they are
    // about to ask about.
    setPickedFolder(folderKey(setId));
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

  // What a button in the transcript asked to say to the agent — an Accept on a
  // card it proposed. Sent as the person's next message, so the agent answers
  // it like any other turn and the transcript shows what it was answering.
  // Held while a run is still going: the card is live in the answer being
  // written, and a second run in the same conversation would be answered
  // without the first one's end in its history. Held in a ref and taken out
  // of it before sending, so it goes out exactly once: a piece of state cleared
  // in the effect can still be read as set by the effect's next run (a render
  // with the new run id and the old message was seen), and only the run guard
  // would then stand between it and a second send. The counter is only what
  // wakes the effect. It waits for its own conversation to be on screen with
  // nothing running: the run id is only ever the selected conversation's, so a
  // move to another one would otherwise release it into a run still going.
  const heldForAgent = useRef<{ threadId: number; message: string } | null>(null);
  const [toldCount, setToldCount] = useState(0);
  const tellAgent = useCallback(
    (message: string) => {
      if (selected === null) return;
      heldForAgent.current = { threadId: selected, message };
      setToldCount((count) => count + 1);
    },
    [selected],
  );
  const sendMessage = send.mutateAsync;
  useEffect(() => {
    if (toldCount === 0 || runId !== null) return;
    const told = heldForAgent.current;
    if (told === null || told.threadId !== selected) return;
    heldForAgent.current = null;
    void attempt(() => sendMessage(told)).then((result) => {
      if (result) setRunId(result.run.id);
    });
  }, [toldCount, runId, sendMessage, selected]);

  const busy = runId !== null;

  return (
    <div className="space-y-4">
      <PageHeader title="Chat" />

      {/* One column: the Sets as badges, the open one's conversations under
          them, then the conversation itself at the page's full width. */}
      <div className="min-w-0 space-y-4">
        {threads.isLoading || sets.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <ConversationPicker
            folders={folders}
            openKey={openFolderKey}
            selectedId={selected}
            onOpen={setPickedFolder}
            onSelect={select}
            onNew={(setId) => void startThread(setId)}
            onDelete={(id) => {
              void deleteThread.mutateAsync(id).then(() => {
                if (id === selected) setSelected(null);
              });
            }}
            showArchived={showArchived}
            onToggleArchived={() => setShowArchived((shown) => !shown)}
            busy={createThread.isPending}
          />
        )}

        <SectionCard
          className="min-w-0"
          // The log is of what is stored, so only a stored conversation has one.
          // A button, not a link: the token is a bearer header, so a bare href
          // would be a 401 with sign-in on. One file, JSON, always.
          actions={
            selected === null ? undefined : (
              <Button
                variant="outline"
                size="sm"
                disabled={downloading}
                onClick={() => void saveTranscript(selected)}
              >
                <Download className="size-3.5" aria-hidden="true" />
                Download log
              </Button>
            )
          }
          title={
            selected === null ? "New conversation" : thread.data?.thread.title || "New conversation"
          }
          description={
            selected !== null
              ? thread.data?.thread.set_name
                ? `About ${[thread.data.thread.set_name, thread.data.thread.set_version_label].filter(Boolean).join(" ")}` +
                  (thread.data.thread.dead_end ? " — a dead end the Set went back past" : "")
                : "General"
              : // Nothing selected: the first question creates the conversation,
                // and this is the only place that says where it will land.
                scopeSet
                ? `A new conversation about ${scopeSet.name} ${scopeSet.current_version_label}`
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
              <ChatThreadContext.Provider value={selected}>
                <TellAgentContext.Provider value={tellAgent}>
                  <ChatTranscript
                    messages={thread.data?.messages ?? []}
                    runs={thread.data?.runs ?? []}
                    permissions={permissions}
                    live={live}
                  />
                </TellAgentContext.Provider>
              </ChatThreadContext.Provider>
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

/**
 * How big the conversation is now, and how much of it the cache served.
 *
 * Read from the newest run that reported a size, not summed over the thread:
 * a sum of billed input counts every re-sent copy of the conversation and says
 * nothing about its size. The wording is in `lib/chatUsage`.
 */
function UsageFooter({ runs }: { runs: Array<{ usage?: Record<string, unknown> | null }> }) {
  const text = contextFooter(latestUsage(runs));
  if (text === null) return null;
  return (
    <p className="mt-2 text-muted-foreground text-xs" data-testid="chat-usage">
      {text}
    </p>
  );
}
