import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  cancelChatRun,
  chatRunStreamUrl,
  createChatThread,
  deleteChatThread,
  getChatThread,
  getChatThreads,
  getChatTools,
  openChatThread,
  renameChatThread,
  sendChatMessage,
} from "@/api/client";
import type {
  ChatRun,
  ChatSendResult,
  ChatThread,
  ChatThreadDetail,
  ChatToolList,
} from "@/api/types";
import { useSse } from "@/hooks/useSse";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The chat, from the browser's side.
 *
 * Three things here are not the shape a TanStack hook usually has, and each is
 * a consequence of the server's design rather than a preference.
 *
 * **Sending resolves as soon as the turn is queued.** `POST .../messages`
 * answers 202 with a `running` run, exactly as running an analysis does, so the
 * mutation resolving means "queued" and the answer arrives on the stream.
 *
 * **The live answer is component state, not cache.** Tokens arrive a few a
 * second; writing each one into the query cache would re-render every consumer
 * of the thread. The partial answer lives in `useChatRun` and the thread query
 * is re-read once, when the run completes — at which point the stored message
 * replaces the partial one.
 *
 * **The stream is resumable.** Every event carries a sequence number and the
 * server replays from the database on connect, so `after` is the last one this
 * tab saw. A reconnect after a locked phone catches up rather than showing half
 * an answer forever.
 */

export type ChatEventKind =
  | "delta"
  | "tool_call"
  | "tool_result"
  | "message"
  | "completed"
  | "error"
  | "cancelled";

export type ChatStreamEvent = {
  kind: ChatEventKind;
  run_id: number;
  thread_id: number;
  seq: number;
  text?: string;
  id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  content?: string;
  ok?: boolean;
  status?: string;
  duration_ms?: number;
  message_id?: number;
  code?: string;
  message?: string;
  usage?: { prompt_tokens?: number | null; completion_tokens?: number | null } | null;
};

/** One tool call and whatever came back, paired for the trace. */
export type TraceEntry = {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  ok?: boolean;
  content?: string;
  durationMs?: number;
};

export type LiveRun = {
  runId: number | null;
  /** The answer so far. Empty once the run completes and the row takes over. */
  text: string;
  trace: TraceEntry[];
  status: "idle" | "running" | "completed" | "error" | "cancelled";
  error: string | null;
  connected: boolean;
};

export function useChatThreads(): UseQueryResult<ChatThread[], Error> {
  return useQuery({ queryKey: queryKeys.chat.threads(), queryFn: getChatThreads });
}

export function useChatThread(id: number | null): UseQueryResult<ChatThreadDetail, Error> {
  return useQuery({
    queryKey: queryKeys.chat.thread(String(id)),
    queryFn: () => getChatThread(id as number),
    enabled: id !== null,
  });
}

/**
 * The tool list for a kind of conversation, fetched once per kind per session.
 *
 * It changes with a redeploy and nothing else. The panel needs it twice over:
 * to say whether a call was a read or a *proposal*, and to say what the agent
 * can do here at all — and "here" differs, which is why the kind is part of
 * the key rather than something the page filters afterwards.
 */
export function useChatTools(kind: "general" | "set" | null): UseQueryResult<ChatToolList, Error> {
  return useQuery({
    // `null` is "the page does not know yet" — a conversation is selected and
    // its row has not arrived. Asking for a kind then would mean guessing, and
    // the guess is visible: the general list would flash beside a Set's chat.
    queryKey: queryKeys.chat.tools(kind ?? "unknown"),
    queryFn: () => getChatTools(kind ?? "general"),
    enabled: kind !== null,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useCreateChatThread(): UseMutationResult<
  ChatThread,
  Error,
  { title?: string; setId?: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ title, setId }) =>
      createChatThread({ title: title ?? "", set_id: setId ?? null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.chat.all }),
    onError: (error) => toast.error(error.message),
  });
}

/**
 * Open or continue the conversation about one version of a Set.
 *
 * Unlike `useCreateChatThread` this is idempotent: the room already exists most
 * of the time, and Discuss means "take me to it" rather than "make another".
 */
export function useOpenChatThread(): UseMutationResult<
  ChatThread,
  Error,
  { setId: number; setVersionId?: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, setVersionId }) => openChatThread(setId, setVersionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.chat.all }),
    onError: (error) => toast.error(error.message),
  });
}

export function useRenameChatThread(): UseMutationResult<
  ChatThread,
  Error,
  { id: number; title: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }) => renameChatThread(id, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.chat.all }),
    onError: (error) => toast.error(error.message),
  });
}

export function useDeleteChatThread(): UseMutationResult<{ deleted: boolean }, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => deleteChatThread(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.chat.all }),
    onError: (error) => toast.error(error.message),
  });
}

export function useSendChatMessage(): UseMutationResult<
  ChatSendResult,
  Error,
  { threadId: number; message: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, message }) => sendChatMessage(threadId, message),
    onSuccess: (_result, { threadId }) => {
      // The question is stored; the answer is not. Re-reading the thread now is
      // what puts the user's own message on screen without waiting for the run.
      void queryClient.invalidateQueries({ queryKey: queryKeys.chat.thread(String(threadId)) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.chat.threads() });
    },
    onError: (error) => toast.error(error.message),
  });
}

export function useCancelChatRun(): UseMutationResult<ChatRun, Error, number> {
  return useMutation({
    mutationFn: (runId) => cancelChatRun(runId),
    onError: (error) => toast.error(error.message),
  });
}

/**
 * Follow one run: the streamed answer, the tool trace, and when it is over.
 *
 * `runId` of `null` means nothing is in flight and no connection is made.
 */
export function useChatRun(runId: number | null, threadId: number | null): LiveRun {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [status, setStatus] = useState<LiveRun["status"]>("idle");
  const [error, setError] = useState<string | null>(null);
  // The highest sequence number seen, in a ref rather than state: it changes on
  // every token and is only ever read when the URL is built, so putting it in
  // state would re-render the tree per token for no visible difference.
  const lastSeq = useRef(0);

  useEffect(() => {
    lastSeq.current = 0;
    setText("");
    setTrace([]);
    setError(null);
    setStatus(runId === null ? "idle" : "running");
  }, [runId]);

  const onEvent = useCallback(
    (message: { data: ChatStreamEvent }) => {
      const event = message.data;
      if (!event || typeof event.seq !== "number") return;
      if (event.seq <= lastSeq.current) return;
      lastSeq.current = event.seq;

      switch (event.kind) {
        case "delta":
          setText((current) => current + (event.text ?? ""));
          break;
        case "tool_call":
          setTrace((current) => [
            ...current,
            {
              id: event.id ?? String(event.seq),
              name: event.name ?? "tool",
              arguments: event.arguments ?? {},
            },
          ]);
          break;
        case "tool_result":
          setTrace((current) =>
            current.map((entry) =>
              entry.id === event.id
                ? {
                    ...entry,
                    ok: event.ok ?? true,
                    content: event.content ?? "",
                    durationMs: event.duration_ms ?? 0,
                  }
                : entry,
            ),
          );
          break;
        case "message":
          // The stored message is about to arrive through the thread query, so
          // the partial copy is dropped rather than rendered twice.
          setText("");
          break;
        case "completed":
        case "cancelled":
        case "error":
          setStatus(event.kind === "completed" ? "completed" : event.kind);
          if (event.kind === "error") setError(event.message ?? "The run failed.");
          if (threadId !== null) {
            void queryClient.invalidateQueries({
              queryKey: queryKeys.chat.thread(String(threadId)),
            });
          }
          // A run may have created a Set version, a draft or an insight, and
          // each of those is somebody else's page.
          void queryClient.invalidateQueries({ queryKey: queryKeys.sets.all });
          void queryClient.invalidateQueries({ queryKey: queryKeys.knowledge.all });
          void queryClient.invalidateQueries({ queryKey: queryKeys.drafts.all });
          break;
      }
    },
    [queryClient, threadId],
  );

  const { connected } = useSse<ChatStreamEvent>(
    runId === null ? null : chatRunStreamUrl(runId, 0),
    onEvent,
  );

  return { runId, text, trace, status, error, connected };
}
