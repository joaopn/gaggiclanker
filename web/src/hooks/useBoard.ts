import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  deleteBoardRow,
  getProfileBoard,
  putOnBoard,
  resumeBoard,
  setBoardHomeScreen,
} from "@/api/client";
import type { BoardRow, BoardView } from "@/api/types";
import { invalidateBoardWrites, invalidateSets } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The profile board and the five things a person can do to it.
 *
 * None of the mutations sends anything to the machine: the board is edited here and
 * the next pull writes it. What they share is what they change. Every one settles with
 * `invalidateBoardWrites`: the board and its preview, the drafts (which say whether
 * they are on it), the profile mirror, the sync status (the last run's summary and the
 * pause live there) and the write audit. Putting a draft on the board *for a Set* also
 * invalidates the Sets, since that pending record is what the pull turns into the Set's
 * next version.
 */

/**
 * `live` reads the machine now (the switch's preview). The default is the archive's last
 * mirror: free, so the page can ask whenever it renders.
 */
export function useProfileBoard(
  options: { live?: boolean; enabled?: boolean } = {},
): UseQueryResult<BoardView, Error> {
  const live = options.live ?? false;
  return useQuery({
    queryKey: queryKeys.board.view(live),
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
  { draftId: number; setId?: number; major?: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) => putOnBoard(body),
    onSuccess: (row) =>
      toast.success(`${row.label} is on the board`, {
        description: "The next pull puts it on the machine.",
      }),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, variables) => {
      void invalidateBoardWrites(queryClient);
      if (variables?.setId !== undefined) void invalidateSets(queryClient);
    },
  });
}

export function useSetHomeScreen(): UseMutationResult<
  BoardRow,
  Error,
  { rowId: number; on: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rowId, on }) => setBoardHomeScreen(rowId, on),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateBoardWrites(queryClient),
  });
}

export function useDeleteBoardRow(): UseMutationResult<BoardRow, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rowId: number) => deleteBoardRow(rowId),
    onSuccess: (row) => toast.success(`${row.label} is deleted from the board`),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateBoardWrites(queryClient),
  });
}

export function useResumeBoard(): UseMutationResult<{ resumed: boolean }, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => resumeBoard(),
    onSuccess: () =>
      toast.success("Pulls may write again", {
        description: "The next pull puts the app's profiles back on the machine.",
      }),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateBoardWrites(queryClient),
  });
}
