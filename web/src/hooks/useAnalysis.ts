import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  acceptSuggestion,
  analyseSet,
  getSetSuggestions,
  largeBatchCount,
  rejectSuggestion,
  runAnalysis,
} from "@/api/client";
import type {
  AcceptedSuggestion,
  Analysis,
  BatchResult,
  Suggestion,
  SuggestionListData,
} from "@/api/types";
import { invalidateAnalyses, invalidateSets, invalidateShots } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Running an analysis, and doing something about what it says.
 *
 * Two things here are not the shape a mutation usually has.
 *
 * **`runAnalysis` returns almost immediately, and the work is not done.** The
 * server queues it and answers 202 with a `running` row; the outcome arrives as
 * `analysis.finished` / `analysis.failed` on the LLM stream, which
 * `EVENT_INVALIDATIONS` turns into a re-read of the row. So the mutation
 * resolving means "queued", not "analysed", and the toast says so.
 *
 * **A failed analysis resolves, it does not reject.** The server answers 2xx
 * with a `failed` row carrying the error code, because a 502 would leave the
 * caller an error and no id — and the row it could not see is the one thing
 * that explains what happened. So `onSuccess` branches on `status` rather than
 * assuming success.
 */

export function useSetSuggestions(
  setId: number | undefined,
): UseQueryResult<SuggestionListData, Error> {
  return useQuery({
    queryKey: queryKeys.analyses.forSet(String(setId)),
    queryFn: () => getSetSuggestions(setId as number),
    enabled: setId !== undefined && Number.isFinite(setId),
  });
}

export function useRunAnalysis(): UseMutationResult<
  Analysis,
  Error,
  { shotId: number; model?: string; force?: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, model, force }) => runAnalysis(shotId, { model, force }),
    onSuccess: (analysis) => {
      if (analysis.status === "running") {
        toast.info("Analysing — this takes a minute or two");
      } else if (analysis.status === "ok") {
        const count = analysis.suggestions?.length ?? 0;
        toast.success(
          count === 0
            ? "Already analysed — no changes suggested"
            : `Already analysed: ${count} suggestion${count === 1 ? "" : "s"}`,
        );
      } else {
        // The row exists and carries the error; the panel renders it. The toast
        // exists so somebody who has scrolled away still learns it went wrong.
        toast.error(analysis.error ?? "The analysis did not complete");
      }
    },
    onError: (error) => toast.error(`Could not analyse: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateAnalyses(queryClient);
      void invalidateShots(queryClient, String(variables.shotId));
      void invalidateShots(queryClient);
    },
  });
}

export function useAnalyseSet(): UseMutationResult<
  BatchResult,
  Error,
  { setId: number; onlyUnanalysed?: boolean; model?: string; acknowledgeLargeBatch?: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, onlyUnanalysed, model, acknowledgeLargeBatch }) =>
      analyseSet(setId, { onlyUnanalysed, model, acknowledgeLargeBatch }),
    onSuccess: (result) => {
      // The batch is queued, not done: the counts that mean anything at this
      // point are what it will attempt and what it left to somebody else.
      if (result.requested === 0) {
        toast.info(
          result.skipped > 0
            ? `Already being analysed (${result.skipped} shot${result.skipped === 1 ? "" : "s"})`
            : "Every shot in this Set has already been analysed",
        );
      } else {
        toast.info(
          `Analysing ${result.requested} shot${result.requested === 1 ? "" : "s"}` +
            (result.skipped > 0 ? `, ${result.skipped} already running` : ""),
        );
      }
    },
    onError: (error) => {
      // Not a failure: a question the page asks from the mutation's error,
      // with the size and a button to go ahead.
      if (largeBatchCount(error) !== null) return;
      toast.error(`Could not analyse the Set: ${error.message}`);
    },
    onSettled: () => {
      void invalidateAnalyses(queryClient);
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

export function useAcceptSuggestion(): UseMutationResult<AcceptedSuggestion, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: acceptSuggestion,
    onSuccess: (result) =>
      toast.success(`Recorded version ${result.version.version_no} from that suggestion`),
    // The server's refusals are the useful text here — "this advice was about
    // version 1 and the Set is now on version 2" tells the user exactly what to
    // do, and a generic "could not accept" would throw it away.
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateAnalyses(queryClient);
      void invalidateSets(queryClient);
      void invalidateShots(queryClient);
    },
  });
}

export function useRejectSuggestion(): UseMutationResult<Suggestion, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: rejectSuggestion,
    onSuccess: () => toast.success("Turned down"),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateAnalyses(queryClient),
  });
}
