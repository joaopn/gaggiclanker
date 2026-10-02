import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiClientError,
  getBoardConflict,
  getBoardVersions,
  getProfileBoard,
  putOnBoard,
  resolveBoardConflict,
  resumeBoard,
  setBoardActiveVersion,
  setBoardOnMachine,
  setBoardStarred,
} from "@/api/client";
import type { BoardRow, BoardView, ConflictView, ProfileVersionsView } from "@/api/types";
import { invalidateBoardWrites, invalidateSets } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The profile list and what a person can do to it.
 *
 * None of the mutations sends anything to the machine: the list is edited here and the next
 * sync writes it. What they share is what they change. Every one settles with
 * `invalidateBoardWrites`: the board (which includes each profile's versions and conflict),
 * the drafts, the profile mirror, the sync status (the last run's summary and the pause live
 * there) and the write audit. Anything that changes what a Set brews (making a proposal
 * active for a Set, another version active, a conflict resolved) also invalidates the Sets.
 */

/** What the next sync does with a profile, said truthfully for one that is switched off. */
function nextSyncWords(row: BoardRow): string {
  return row.on_machine
    ? "The next sync puts it on the machine."
    : "It is switched off, so it stays off the machine until you switch it on.";
}

/**
 * `live` reads the machine now (the switch's preview). The default is the archive's last
 * mirror: free, so the page can ask whenever it renders.
 */
export function useProfileBoard(
  options: { live?: boolean; enabled?: boolean } = {},
): UseQueryResult<BoardView, Error> {
  const live = options.live ?? false;
  return useQuery({
    queryKey: live ? queryKeys.boardLive.view() : queryKeys.board.view(),
    queryFn: () => getProfileBoard(live),
    enabled: options.enabled ?? true,
    // A live read is a list and a load per profile on a small machine: ask once per
    // opening, never because a window regained focus.
    ...(live ? { staleTime: 0, gcTime: 0, refetchOnWindowFocus: false, retry: 0 } : {}),
  });
}

export function usePutOnBoard(): UseMutationResult<
  BoardRow,
  Error,
  { draftId: number; setId?: number; major?: boolean; acknowledgeStopChanges?: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) => putOnBoard(body),
    onSuccess: (row) =>
      toast.success(`${row.label} has a new active version`, {
        description: nextSyncWords(row),
      }),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, variables) => {
      void invalidateBoardWrites(queryClient);
      if (variables?.setId !== undefined) void invalidateSets(queryClient);
    },
  });
}

export function useSetOnMachine(): UseMutationResult<
  BoardRow,
  Error,
  { rowId: number; on: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rowId, on }) => setBoardOnMachine(rowId, on),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateBoardWrites(queryClient),
  });
}

export function useSetStarred(): UseMutationResult<
  BoardRow,
  Error,
  { rowId: number; starred: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rowId, starred }) => setBoardStarred(rowId, starred),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateBoardWrites(queryClient),
  });
}

/**
 * Make one of a profile's versions its active one. Nothing is sent to the machine; the next
 * sync puts that version there. The Sets are refreshed because what a Set brews is whatever
 * its profile's active version is once the sync has run.
 */
export function useSetActiveVersion(): UseMutationResult<
  BoardRow,
  Error,
  { rowId: number; versionId: number }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rowId, versionId }) => setBoardActiveVersion(rowId, versionId),
    onSuccess: (row) =>
      toast.success(`${row.label} has a new active version`, {
        description: nextSyncWords(row),
      }),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateBoardWrites(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

/** A profile's versions and proposals; read only while its dropdown is open. */
export function useBoardVersions(
  rowId: number,
  enabled: boolean,
): UseQueryResult<ProfileVersionsView, Error> {
  return useQuery({
    queryKey: queryKeys.board.versions(rowId),
    queryFn: () => getBoardVersions(rowId),
    enabled,
  });
}

/** Both sides of a profile's conflict; `null` when it has none. */
export function useBoardConflict(
  rowId: number,
  enabled: boolean,
): UseQueryResult<ConflictView | null, Error> {
  return useQuery({
    queryKey: queryKeys.board.conflict(rowId),
    queryFn: () => getBoardConflict(rowId),
    enabled,
  });
}

/**
 * Choose a side of a conflict. A refusal because the machine's file changed since it was
 * shown re-reads the conflict (the settle below) and the panel says so; `variables` names the
 * profile it was for.
 */
export function useResolveConflict(): UseMutationResult<
  BoardRow,
  Error,
  { rowId: number; keep: "app" | "machine"; contentHash: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rowId, keep, contentHash }) => resolveBoardConflict(rowId, keep, contentHash),
    onSuccess: (row, variables) =>
      toast.success(
        variables.keep === "machine"
          ? `${row.label} keeps the machine's version`
          : `${row.label} keeps the app's version`,
        {
          description:
            variables.keep === "machine"
              ? "The machine's version is now the active one. Nothing is sent to the machine."
              : nextSyncWords(row),
        },
      ),
    onError: (error) => {
      if (!isStaleConflict(error)) toast.error(error.message);
    },
    onSettled: () => {
      void invalidateBoardWrites(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

/** The refusal for a conflict whose machine file changed after the person looked at it. */
export function isStaleConflict(error: unknown): boolean {
  if (!(error instanceof ApiClientError)) return false;
  const details = error.details as { reason?: unknown } | null | undefined;
  return details?.reason === "stale_conflict";
}

export function useResumeBoard(): UseMutationResult<{ resumed: boolean }, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => resumeBoard(),
    onSuccess: () =>
      toast.success("Syncs may write again", {
        description:
          "The next sync puts the profiles that are on back and removes the ones that are off.",
      }),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateBoardWrites(queryClient),
  });
}
