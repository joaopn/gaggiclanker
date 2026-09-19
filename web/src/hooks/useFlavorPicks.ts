import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { getFlavorPicks, putFlavorPicks } from "@/api/client";
import type { FlavorPicks } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The notes the shot panel offers, for taste and for aroma.
 *
 * Read by every open shot row and by the Taste wheel page. Nothing but that
 * page changes it, so it is a plain query invalidated by its own mutation.
 */
export function useFlavorPicks(): UseQueryResult<FlavorPicks, Error> {
  return useQuery({ queryKey: queryKeys.flavorPicks.all, queryFn: getFlavorPicks });
}

/**
 * Replace both lists, optimistically.
 *
 * The wheel is clicked quickly — a note, the next one, the next — and waiting
 * for a round trip before a segment lights up would make it feel broken. The
 * cache takes the new lists at once, a failure puts the old ones back with a
 * toast, and the server's answer (de-duplicated, in wheel order) is re-read
 * when the write settles. The shape is `useUpdateSettings`'.
 */
export function useSaveFlavorPicks(): UseMutationResult<
  FlavorPicks,
  Error,
  FlavorPicks,
  { previous: FlavorPicks | undefined }
> {
  const queryClient = useQueryClient();
  const key = queryKeys.flavorPicks.all;
  return useMutation({
    mutationKey: key,
    mutationFn: putFlavorPicks,
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<FlavorPicks>(key);
      queryClient.setQueryData(key, next);
      return { previous };
    },
    onError: (error, _next, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous);
      toast.error(`Could not save the notes: ${error.message}`);
    },
    // Only once the last of several quick writes settles: re-reading after the
    // first would put the lists as they were before the second on screen until
    // the second's answer arrived.
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey: key }) <= 1) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });
}
