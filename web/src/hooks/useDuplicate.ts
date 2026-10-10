import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { duplicateVersion, getDuplicatePreview } from "@/api/client";
import type { DuplicateAnswer, DuplicatePreview, DuplicateRequest } from "@/api/types";
import { invalidateBoardWrites, invalidateKnowledge, invalidateSets } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Duplicating a Set from one of its versions.
 *
 * The write makes a profile on the profile list, a Set with its first version, and insights. So
 * it touches the Sets (the list and the new Set's page, one prefix), the board and everything the
 * board's writes reach (drafts, the profile mirror and so every profile-versions query, the sync
 * status, the write audit), and the insights, which the new Set's page lists. No shot is filed or
 * moved by it, so the shots queries are left alone. Nothing is sent to the machine: the next sync
 * does that.
 */

/**
 * What the duplicate starts from. Never cached: the Set's insights may have changed since the
 * last opening, and the dialog is the moment to ask.
 */
export function useDuplicatePreview(
  setId: number,
  versionId: number,
): UseQueryResult<DuplicatePreview, Error> {
  return useQuery({
    queryKey: queryKeys.duplicates.preview(String(setId), String(versionId)),
    queryFn: () => getDuplicatePreview(setId, versionId),
    staleTime: 0,
    gcTime: 0,
    retry: 0,
    refetchOnWindowFocus: false,
  });
}

/**
 * Duplicate a version. No toast on a refusal: the dialog that asked shows the server's sentence in
 * place. The Sets, the board and the insights are refreshed when the request settles, answered or
 * not, since a request that got no answer may have written anyway.
 */
export function useDuplicateSet(): UseMutationResult<
  DuplicateAnswer,
  Error,
  { setId: number; versionId: number; body: DuplicateRequest }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, versionId, body }) => duplicateVersion(setId, versionId, body),
    onSuccess: ({ set }) => toast.success(`Started "${set.name}"`),
    onSettled: () => {
      void invalidateSets(queryClient);
      void invalidateBoardWrites(queryClient);
      void invalidateKnowledge(queryClient);
    },
  });
}
