import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiClientError, getPinPreview, pinVersion, unpinPin } from "@/api/client";
import type { PinAnswer, PinPreview } from "@/api/types";
import { invalidateBoardWrites, invalidateRecipes } from "@/lib/invalidate";
import { noAnswerSentence } from "@/lib/mutations";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Pinning a Recipe version to the machine, and taking a pin off it.
 *
 * Neither writes to the machine: the pin is a profile-list row and a record, and the next sync
 * does the rest. What they share is what they touch: the Recipe (its pins, its versions' pinned
 * marker), the profile board (a row appears or is tombstoned), and everything the board's
 * writes reach (drafts, the profile mirror and its versions, the sync status, the write audit).
 * No shot is refiled by either, so the shots queries are left alone.
 */

/**
 * The pin's own questions, asked and not acted on. Never cached: a name the preview called free
 * a minute ago may be taken now, and the dialog is the moment to ask.
 */
export function usePinPreview(
  recipeId: number,
  versionId: number,
): UseQueryResult<PinPreview, Error> {
  return useQuery({
    queryKey: queryKeys.pins.preview(String(recipeId), String(versionId)),
    queryFn: () => getPinPreview(recipeId, versionId),
    staleTime: 0,
    gcTime: 0,
    retry: 0,
    refetchOnWindowFocus: false,
  });
}

/**
 * Pin a version. No toast: the dialog that asked shows what came back, in one place. A refusal
 * the server worded is shown as its own sentence; a request that got no answer is shown as
 * `noAnswerSentence`, since the pin may exist anyway. Either way the Recipe and the board are
 * refreshed when the request settles, so the card behind the dialog shows what is true.
 */
export function usePinVersion(): UseMutationResult<
  PinAnswer,
  Error,
  { recipeId: number; versionId: number; label?: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, versionId, label }) => pinVersion(recipeId, versionId, label),
    onSuccess: ({ pin }) => toast.success(`Pinned ${pin.version_label} as "${pin.name}"`),
    onSettled: () => {
      void invalidateRecipes(queryClient);
      void invalidateBoardWrites(queryClient);
    },
  });
}

/** Take a pin off the machine at the next sync. */
export function useUnpin(): UseMutationResult<PinAnswer, Error, { pinId: number }> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ pinId }) => unpinPin(pinId),
    onSuccess: ({ pin }) => toast.success(`Unpinned ${pin.version_label}: "${pin.name}"`),
    onError: (error) =>
      toast.error(error instanceof ApiClientError ? error.message : noAnswerSentence("the page")),
    onSettled: () => {
      void invalidateRecipes(queryClient);
      void invalidateBoardWrites(queryClient);
    },
  });
}
