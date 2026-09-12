import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  activateSet,
  addSetVersion,
  archiveSet,
  createSet,
  deleteJudgement,
  getSet,
  getSets,
  getSetTrends,
  putJudgement,
  putShotSetVersion,
} from "@/api/client";
import type {
  JudgementWrite,
  SetCreate,
  SetDetailData,
  SetListData,
  SetRow,
  SetTrends,
  SetVersionPatch,
  SetVersionRow,
  ShotDetailRow,
  ShotJudgement,
} from "@/api/types";
import { invalidateSets, invalidateShots } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Sets, their versions, the trend chart, and the two things a person writes
 * about a shot: the verdict and which Set it belongs to.
 *
 * Every mutation here invalidates both `sets` and `shots`, and that is not
 * laziness. Saving a judgement changes the shot row (`has_judgement`), the Set
 * page's own copy of that row, and the trend chart's averages; assigning a shot
 * changes its badge in the list and moves a point between two versions on the
 * chart. Naming three keys at each call site is how one of them gets forgotten,
 * and the symptom — "the page did not update" — surfaces long after the commit.
 */

export function useSets(includeArchived = false): UseQueryResult<SetListData, Error> {
  return useQuery({
    queryKey: queryKeys.sets.list(includeArchived),
    queryFn: () => getSets(includeArchived),
  });
}

export function useSet(id: number | undefined): UseQueryResult<SetDetailData, Error> {
  return useQuery({
    queryKey: queryKeys.sets.detail(String(id)),
    queryFn: () => getSet(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

export function useSetTrends(id: number | undefined): UseQueryResult<SetTrends, Error> {
  return useQuery({
    queryKey: queryKeys.sets.trends(String(id)),
    queryFn: () => getSetTrends(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

export function useCreateSet(): UseMutationResult<SetRow, Error, SetCreate> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createSet,
    onSuccess: (row) => toast.success(`Started "${row.name}"`),
    onError: (error) => toast.error(`Could not start the Set: ${error.message}`),
    onSettled: () => {
      void invalidateSets(queryClient);
      // A new Set is active by default, so the machine's other Set just lost
      // the flag its card renders.
      void invalidateShots(queryClient);
    },
  });
}

export function useAddSetVersion(): UseMutationResult<
  SetVersionRow,
  Error,
  { setId: number; patch: SetVersionPatch }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, patch }) => addSetVersion(setId, patch),
    onSuccess: (version) => toast.success(`Version ${version.version_no} recorded`),
    onError: (error) => toast.error(`Could not add the version: ${error.message}`),
    onSettled: () => invalidateSets(queryClient),
  });
}

export function useActivateSet(): UseMutationResult<SetRow, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: activateSet,
    onSuccess: (row) => toast.success(`"${row.name}" is what the machine is set up for`),
    onError: (error) => toast.error(error.message),
    onSettled: () => invalidateSets(queryClient),
  });
}

export function useArchiveSet(): UseMutationResult<SetRow, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: archiveSet,
    onSuccess: (row) => toast.success(`"${row.name}" archived`),
    onError: (error) => toast.error(error.message),
    onSettled: () => invalidateSets(queryClient),
  });
}

export function useSaveJudgement(): UseMutationResult<
  ShotJudgement,
  Error,
  { shotId: number; body: JudgementWrite }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, body }) => putJudgement(shotId, body),
    onSuccess: () => toast.success("Judgement saved"),
    onError: (error) => toast.error(`Could not save: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateShots(queryClient, String(variables.shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

export function useDeleteJudgement(): UseMutationResult<{ deleted: boolean }, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteJudgement,
    onSuccess: () => toast.success("Judgement withdrawn"),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, shotId) => {
      void invalidateShots(queryClient, String(shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

export function useAssignShot(): UseMutationResult<
  ShotDetailRow,
  Error,
  { shotId: number; setVersionId: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, setVersionId }) => putShotSetVersion(shotId, setVersionId),
    onSuccess: (row) =>
      toast.success(
        row.set_badge
          ? `Filed under ${row.set_badge.set_name} v${row.set_badge.version_no}`
          : "Detached from its Set",
      ),
    onError: (error) => toast.error(`Could not assign: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateShots(queryClient, String(variables.shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
    },
  });
}
