import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { toast } from "sonner";
import {
  getLlmCalls,
  getLlmModels,
  getLlmStatus,
  getLlmUsage,
  resetLlmRateLimit,
  validateLlm,
} from "@/api/client";
import type {
  LlmCall,
  LlmCallEvent,
  LlmCallsData,
  LlmCredentialCheck,
  LlmModelsData,
  LlmRateLimit,
  LlmSnapshotEvent,
  LlmStatusData,
  LlmUsageTotals,
} from "@/api/types";
import { useSse } from "@/hooks/useSse";
import { queryKeys } from "@/lib/queryKeys";

export function useLlmStatus(): UseQueryResult<LlmStatusData, Error> {
  return useQuery({ queryKey: queryKeys.llm.status(), queryFn: getLlmStatus });
}

/**
 * "Does this provider work at all." Deliberately a mutation and not a query:
 * it costs a round trip to somebody else's server, so it happens when the
 * button is pressed and never on render.
 */
export function useValidateLlm(): UseMutationResult<LlmCredentialCheck, Error, string | undefined> {
  return useMutation({
    mutationFn: (provider?: string) => validateLlm(provider),
    onSuccess: (check) => {
      if (check.ok) toast.success(check.detail || "Credentials look good");
      else toast.error(check.detail || "The provider refused those credentials");
    },
    onError: (error) => toast.error(error.message),
  });
}

/** Fetched on demand, because listing models is a request to the provider. */
export function useLlmModels(
  provider?: string,
  enabled = false,
): UseQueryResult<LlmModelsData, Error> {
  return useQuery({
    queryKey: queryKeys.llm.models(provider),
    queryFn: () => getLlmModels(provider),
    enabled,
    staleTime: 5 * 60 * 1000,
  });
}

export function useResetRateLimit(): UseMutationResult<LlmRateLimit, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => resetLlmRateLimit(),
    onSuccess: () => {
      toast.success("The LLM is running again");
      void queryClient.invalidateQueries({ queryKey: queryKeys.llm.status() });
    },
    onError: (error) => toast.error(error.message),
  });
}

export function useLlmUsage(since?: string): UseQueryResult<LlmUsageTotals, Error> {
  return useQuery({ queryKey: queryKeys.llm.usage(since), queryFn: () => getLlmUsage(since) });
}

/**
 * The live call list, seeded by a fetch and then kept current by the stream.
 *
 * The fetch is what makes a tab that opened between two events correct; the
 * stream is what makes it stay correct without polling an endpoint twice a
 * second for a page that is usually idle. Records are keyed by id and replaced
 * in place, because an update carries the whole record - the same rule the
 * device stream follows.
 */
export function useLlmCalls(enabled = true): {
  calls: LlmCall[];
  running: number;
  connected: boolean;
} {
  const [calls, setCalls] = useState<LlmCall[]>([]);
  const [running, setRunning] = useState(0);

  const seed = useQuery({
    queryKey: queryKeys.llm.calls(),
    queryFn: getLlmCalls,
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });

  const apply = useCallback((snapshot: LlmCallsData) => {
    setCalls(snapshot.calls);
    setRunning(snapshot.running);
  }, []);

  const onEvent = useCallback(
    (message: { event: string; data: LlmCallEvent | LlmSnapshotEvent }) => {
      if (message.event === "llm.snapshot") {
        apply(message.data as LlmSnapshotEvent);
        return;
      }
      if (message.event !== "llm.call") return;
      const { call, running: count } = message.data as LlmCallEvent;
      setRunning(count);
      setCalls((previous) => {
        const without = previous.filter((entry) => entry.id !== call.id);
        return [call, ...without].slice(0, 50);
      });
    },
    [apply],
  );

  const { connected } = useSse<LlmCallEvent | LlmSnapshotEvent>(
    enabled ? "/api/llm/calls/stream" : null,
    onEvent,
  );

  // The seeded list is used only until the stream's own snapshot lands, which
  // is normally within a frame of it.
  const seeded = calls.length > 0 || running > 0 ? { calls, running } : seed.data;
  return {
    calls: seeded?.calls ?? [],
    running: seeded?.running ?? 0,
    connected,
  };
}
