import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  getCleanupPlan,
  getCleanupRuns,
  getDeviceStatus,
  getDeviceWrites,
  getPendingNotes,
  pushPendingNotes,
  runCleanup,
  writeBackNotes,
} from "@/api/client";
import type {
  CleanupPlan,
  CleanupRunAccepted,
  CleanupRunsData,
  DeviceStatusData,
  DeviceWritesData,
  NotesPushAccepted,
  PendingNotesData,
  WritebackResult,
} from "@/api/types";
import { invalidateDeviceWrites, invalidateShots } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Whether the machine is there, and what it is.
 *
 * The only thing that answers that question now. There used to be a 2 Hz
 * telemetry stream beside it whose connection events refreshed this key the
 * instant a socket came up or went down; without it the poll is the whole
 * mechanism, so it runs every fifteen seconds rather than every thirty. That
 * is the delay between plugging the machine in and the pull button going live,
 * and it costs one small request a minute against the box's own database.
 */
export function useDeviceStatus(): UseQueryResult<DeviceStatusData, Error> {
  return useQuery({
    queryKey: queryKeys.device.status(),
    queryFn: getDeviceStatus,
    refetchInterval: 15_000,
    staleTime: 10_000,
    retry: 0,
  });
}

/**
 * Every write this box has asked the machine to make, and whether the switch
 * that allows them is on.
 *
 * Both in one query because they are read together: a list of refusals means
 * one thing when writes are off and something quite different when they are on,
 * and two requests would render the wrong sentence for a moment every time.
 */
export function useDeviceWrites(limit = 100): UseQueryResult<DeviceWritesData, Error> {
  return useQuery({
    queryKey: queryKeys.device.writes(),
    queryFn: () => getDeviceWrites(limit),
    retry: 0,
  });
}

/**
 * What a cleanup would delete from the machine.
 *
 * Fetched rather than computed on this side, and that is deliberate: the
 * eligibility rule is the same function the write gate applies, so the preview
 * a person approves is the set the machine will actually be asked to lose. A
 * copy of the rule in TypeScript would be a second opinion, and the dangerous
 * kind — one that says a shot is safe to delete when the server disagrees.
 */
export function useCleanupPlan(enabled = true): UseQueryResult<CleanupPlan, Error> {
  return useQuery({
    queryKey: queryKeys.device.cleanupPlan(),
    queryFn: getCleanupPlan,
    enabled,
    retry: 0,
  });
}

export function useCleanupRuns(limit = 20): UseQueryResult<CleanupRunsData, Error> {
  return useQuery({
    queryKey: queryKeys.device.cleanupRuns(),
    queryFn: () => getCleanupRuns(limit),
    retry: 0,
  });
}

/**
 * Start a cleanup.
 *
 * Resolving means **queued**, not finished — the route answers 202 and the work
 * is a background task. The ledger and the plan are invalidated on success so
 * the card re-reads; `cleanup.progress` on the sync stream does the same when
 * the run ends, which is what makes a run started in another tab show up here.
 */
export function useRunCleanup(): UseMutationResult<CleanupRunAccepted, Error, number[]> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (shotIds: number[]) => runCleanup(shotIds),
    // Settled rather than success: a 409 means the plan moved under the
    // preview, and the fresh plan is exactly what the person needs to see next.
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.device.all });
    },
  });
}

/** The judgements the machine's own notes cards do not have yet. */
export function usePendingNotes(): UseQueryResult<PendingNotesData, Error> {
  return useQuery({
    queryKey: queryKeys.device.pendingNotes(),
    queryFn: getPendingNotes,
    retry: 0,
  });
}

export function usePushPendingNotes(): UseMutationResult<NotesPushAccepted, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => pushPendingNotes(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.device.all });
    },
  });
}

/**
 * Send one shot's judgement to the machine.
 *
 * Unlike the two above this resolves with the *outcome*: it is a single frame,
 * and a refusal ("this verdict came from the machine") comes back as a result
 * with a reason rather than as an error. The caller renders the sentence.
 *
 * Both the shot and the device audit are invalidated: the write changes the
 * judgement's sync state on one page and adds an audit row on the other.
 */
export function useWriteBackNotes(shotId: string): UseMutationResult<WritebackResult, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => writeBackNotes(shotId),
    onSuccess: async (result) => {
      if (!result.written) return;
      await invalidateShots(queryClient, shotId);
      await invalidateDeviceWrites(queryClient);
    },
  });
}
