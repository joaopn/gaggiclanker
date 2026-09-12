import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  acceptStartingPoint,
  createStartingPoint,
  getSimilarSets,
  getStartingPoint,
} from "@/api/client";
import type {
  SimilarSetsData,
  StartingPointAccepted,
  StartingPointRequest,
  StartingPointRun,
} from "@/api/types";
import { invalidateBeans, invalidateDrafts, invalidateSets } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The starting-point wizard: the free evidence, the paid suggestion,
 * and taking one of the three.
 *
 * Two things here are not the obvious thing, and both are the analysis hook's
 * reasoning (`useAnalysis.ts`).
 *
 * **Creating a run resolves when the work is *queued*, not when it is done.**
 * The server answers 202 with a `running` row, because a provider call takes a
 * minute and a request holding one open is a request `docker stop` kills. So
 * `useCreateStartingPoint` hands back a running row and the caller follows it.
 *
 * **A failed run resolves rather than rejecting.** The row exists, it says
 * `failed` and it carries the error code; turning that into a thrown error
 * would leave the caller an exception and no id, and the row is the one thing
 * that explains what happened.
 */

/** How often the wizard re-reads a running row. */
const POLL_MS = 1500;

export function useSimilarSets(
  beanId: number | undefined,
  params: { grinderId?: number | null; machineId?: number | null } = {},
): UseQueryResult<SimilarSetsData, Error> {
  return useQuery({
    queryKey: queryKeys.beans.similarSets(String(beanId), params.grinderId, params.machineId),
    queryFn: () => getSimilarSets(beanId as number, params),
    enabled: beanId !== undefined && Number.isFinite(beanId),
  });
}

/**
 * One run, polled while it is running.
 *
 * Polled rather than streamed. The LLM bus does carry `starting_point.*` and
 * the header's activity indicator already shows the call — but the wizard is a
 * dialog that is open for thirty seconds, and a poll that stops the moment the
 * row is terminal is a great deal less machinery than a second SSE subscription
 * for one dialog. `refetchInterval` returning `false` is what stops it.
 */
export function useStartingPoint(
  runId: number | undefined,
): UseQueryResult<StartingPointRun, Error> {
  return useQuery({
    queryKey: queryKeys.startingPoints.detail(String(runId)),
    queryFn: () => getStartingPoint(runId as number),
    enabled: runId !== undefined && Number.isFinite(runId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? POLL_MS : false),
  });
}

export function useCreateStartingPoint(): UseMutationResult<
  StartingPointRun,
  Error,
  StartingPointRequest
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: StartingPointRequest) => createStartingPoint(body),
    onSuccess: (row) => {
      // Seed the cache with the row we already have, so the poll below starts
      // from "running" rather than from a spinner and a second round-trip.
      queryClient.setQueryData(queryKeys.startingPoints.detail(String(row.id)), row);
    },
    onError: (error) => toast.error(`Could not ask for a starting point: ${error.message}`),
  });
}

export function useAcceptStartingPoint(): UseMutationResult<
  StartingPointAccepted,
  Error,
  { runId: number; option: "conservative" | "recommended" | "adventurous" }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, option }) => acceptStartingPoint(runId, option),
    onSuccess: (accepted) => {
      toast.success(`Started "${accepted.set.name}"`);
      queryClient.setQueryData(
        queryKeys.startingPoints.detail(String(accepted.run.id)),
        accepted.run,
      );
    },
    onError: (error) => toast.error(`Could not start the Set: ${error.message}`),
    onSettled: () => {
      void invalidateSets(queryClient);
      // The bean's card shows how many Sets use it, and an option that carried
      // a profile has just put a draft in the queue.
      void invalidateBeans(queryClient);
      void invalidateDrafts(queryClient);
    },
  });
}
