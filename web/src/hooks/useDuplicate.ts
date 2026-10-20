import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { duplicateProfile, duplicateVersion, getDuplicatePreview } from "@/api/client";
import type {
  BoardRow,
  DuplicateAnswer,
  DuplicatePreview,
  DuplicateRequest,
  ProfileDuplicateRequest,
} from "@/api/types";
import { invalidateBoardWrites, invalidateKnowledge, invalidateRecipes } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Duplicating a Recipe from one of its versions.
 *
 * The write makes a profile on the profile list, a Recipe with its first version, and insights. So
 * it touches the Recipes (the list and the new Recipe's page, one prefix), the board and everything the
 * board's writes reach (drafts, the profile mirror and so every profile-versions query, the sync
 * status, the write audit), and the insights, which the new Recipe's page lists. No shot is filed or
 * moved by it, so the shots queries are left alone. Nothing is sent to the machine: the next sync
 * does that.
 */

/**
 * What the duplicate starts from. Never cached: the Recipe's insights may have changed since the
 * last opening, and the dialog is the moment to ask.
 */
export function useDuplicatePreview(
  recipeId: number,
  versionId: number,
): UseQueryResult<DuplicatePreview, Error> {
  return useQuery({
    queryKey: queryKeys.duplicates.preview(String(recipeId), String(versionId)),
    queryFn: () => getDuplicatePreview(recipeId, versionId),
    staleTime: 0,
    gcTime: 0,
    retry: 0,
    refetchOnWindowFocus: false,
  });
}

/**
 * Duplicate a version. No toast on a refusal: the dialog that asked shows the server's sentence in
 * place. The Recipes, the board and the insights are refreshed when the request settles, answered or
 * not, since a request that got no answer may have written anyway.
 */
export function useDuplicateRecipe(): UseMutationResult<
  DuplicateAnswer,
  Error,
  { recipeId: number; versionId: number; body: DuplicateRequest }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, versionId, body }) => duplicateVersion(recipeId, versionId, body),
    onSuccess: ({ recipe }) => toast.success(`Started "${recipe.name}"`),
    onSettled: () => {
      void invalidateRecipes(queryClient);
      void invalidateBoardWrites(queryClient);
      void invalidateKnowledge(queryClient);
    },
  });
}

/**
 * Duplicate Profile. The write puts one new profile on the list, so it touches the board and
 * everything its writes reach (the profile mirror, so every profile-versions query) and the
 * profiles a new Recipe may start on. No toast on a refusal: the dialog shows the server's sentence
 * in place.
 *
 * The refresh is awaited before the call resolves, so whoever asked (New Recipe) can select the new
 * profile at once: the choices and the profile versions already list it.
 */
export function useDuplicateProfile(): UseMutationResult<
  BoardRow,
  Error,
  { versionId: number; body: ProfileDuplicateRequest }
> {
  const queryClient = useQueryClient();
  const refresh = () =>
    Promise.all([
      invalidateBoardWrites(queryClient),
      queryClient.invalidateQueries({ queryKey: queryKeys.recipes.profileChoices() }),
    ]).then(() => undefined);
  return useMutation({
    mutationFn: ({ versionId, body }) => duplicateProfile(versionId, body),
    onSuccess: async (row) => {
      await refresh();
      toast.success(`Added ${row.label}`);
    },
    // A request that got no answer may have written anyway.
    onError: () => refresh(),
  });
}
