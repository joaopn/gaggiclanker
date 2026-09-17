import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  createBean,
  createGrinder,
  deleteBean,
  getBeans,
  getGrinders,
  getMachine,
  getVocabulary,
  patchMachine,
  setBeanArchived,
  updateBean,
  updateGrinder,
} from "@/api/client";
import type {
  BeanRow,
  BeanWrite,
  GrinderRow,
  GrinderWrite,
  MachineData,
  MachinePatch,
  MachineRow,
  Vocabulary,
} from "@/api/types";
import { invalidateBeans, invalidateHardware } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The catalogue: the words, the bags and the hardware.
 *
 * None of it comes from the machine and none of it polls. A bean is created by
 * a person and changes when that person changes it, so these are plain queries
 * invalidated by their own mutations.
 */

/**
 * Every closed vocabulary, fetched once for the session.
 *
 * `staleTime: Infinity` because these change with a redeploy and nothing else:
 * a refetch could only ever return the same bytes, and the forms that depend on
 * it would flash a disabled select while it ran.
 */
export function useVocabulary(): UseQueryResult<Vocabulary, Error> {
  return useQuery({
    queryKey: queryKeys.vocab.current(),
    queryFn: getVocabulary,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useBeans(includeArchived = false): UseQueryResult<{ items: BeanRow[] }, Error> {
  return useQuery({
    queryKey: queryKeys.beans.list(includeArchived),
    queryFn: () => getBeans(includeArchived),
  });
}

export function useSaveBean(): UseMutationResult<BeanRow, Error, { id?: number; body: BeanWrite }> {
  const queryClient = useQueryClient();
  return useMutation({
    // One hook for create and edit, because the form is the same form: the
    // presence of an id is the only difference, and two hooks would mean two
    // places to keep the invalidation right.
    mutationFn: ({ id, body }) => (id === undefined ? createBean(body) : updateBean(id, body)),
    onSuccess: (bean) => {
      toast.success(`Saved ${bean.name}`);
    },
    onError: (error) => toast.error(`Could not save the bean: ${error.message}`),
    onSettled: () => {
      void invalidateBeans(queryClient);
      // A Set card shows its bean's name and roast date, so a rename is a
      // stale Set card until this runs.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sets.all });
    },
  });
}

export function useArchiveBean(): UseMutationResult<
  BeanRow,
  Error,
  { id: number; archived: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, archived }) => setBeanArchived(id, archived),
    onSuccess: (bean) =>
      toast.success(bean.archived ? `${bean.name} archived` : `${bean.name} is back`),
    onError: (error) => toast.error(error.message),
    onSettled: () => invalidateBeans(queryClient),
  });
}

/**
 * Delete a bean nobody used. Takes the name too, so the toast can say which one
 * went after the row is already gone from the cache.
 */
export function useDeleteBean(): UseMutationResult<
  { deleted: boolean },
  Error,
  { id: number; name: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id }) => deleteBean(id),
    onSuccess: (_result, { name }) => toast.success(`${name} deleted`),
    onError: (error) => toast.error(`Could not delete the bean: ${error.message}`),
    onSettled: () => invalidateBeans(queryClient),
  });
}

export function useGrinders(): UseQueryResult<{ items: GrinderRow[] }, Error> {
  return useQuery({ queryKey: queryKeys.hardware.grinders(), queryFn: getGrinders });
}

export function useSaveGrinder(): UseMutationResult<
  GrinderRow,
  Error,
  { id?: number; body: GrinderWrite }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }) =>
      id === undefined ? createGrinder(body) : updateGrinder(id, body),
    onSuccess: (grinder) => toast.success(`Saved ${grinder.name}`),
    onError: (error) => toast.error(`Could not save the grinder: ${error.message}`),
    onSettled: () => {
      void invalidateHardware(queryClient);
      void queryClient.invalidateQueries({ queryKey: queryKeys.sets.all });
    },
  });
}

/** The machine. One row, so no id and no list. */
export function useMachine(): UseQueryResult<MachineData, Error> {
  return useQuery({ queryKey: queryKeys.hardware.machine(), queryFn: getMachine });
}

export function useSaveMachine(): UseMutationResult<MachineRow, Error, MachinePatch> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) => patchMachine(body),
    onSuccess: () => toast.success("Machine saved"),
    onError: (error) => toast.error(`Could not save the machine: ${error.message}`),
    onSettled: () => invalidateHardware(queryClient),
  });
}
