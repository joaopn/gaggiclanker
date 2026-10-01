import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  createProfileDraft,
  discardProfileDraft,
  getProfileDraft,
  getProfileDrafts,
  previewProfileDraft,
  refineProfileDraft,
} from "@/api/client";
import type {
  DraftCreateBody,
  DraftPreview,
  ProfileDraft,
  ProfileDraftDetail,
  ProfileDraftListData,
} from "@/api/types";
import { invalidateBoard, invalidateDrafts } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The draft queue and what can be done to a draft before it is put on the board: draft it,
 * refine it, discard it, validate a document. Putting it on the board (which also approves
 * it) is `usePutOnBoard`; nothing here sends anything to the machine.
 */

export function useProfileDrafts(
  params: { status?: string; open?: boolean } = {},
  options: { enabled?: boolean } = {},
): UseQueryResult<ProfileDraftListData, Error> {
  return useQuery({
    queryKey: queryKeys.drafts.list(params),
    queryFn: () => getProfileDrafts(params),
    enabled: options.enabled ?? true,
  });
}

export function useProfileDraft(id: number | undefined): UseQueryResult<ProfileDraftDetail, Error> {
  return useQuery({
    queryKey: queryKeys.drafts.detail(String(id)),
    queryFn: () => getProfileDraft(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

export function useCreateDraft(): UseMutationResult<ProfileDraft, Error, DraftCreateBody> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: DraftCreateBody) => createProfileDraft(body),
    onSuccess: (draft) => {
      toast.success(draft.change_summary ? `Drafted: ${draft.change_summary}` : "Profile drafted");
    },
    // The server's refusals are the useful instruction here — "11 phases, and
    // the policy allows 10. Remove phases yourself" is exactly what to show.
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateDrafts(queryClient);
      // Where a put of a draft would land is read from the board, so a draft made, refined or
      // discarded changes what the board says about it.
      void invalidateBoard(queryClient);
    },
  });
}

export function useRefineDraft(): UseMutationResult<
  ProfileDraft,
  Error,
  { id: number; notes: string; model?: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, notes, model }) => refineProfileDraft(id, { notes, model }),
    onSuccess: () => toast.success("Drafted again"),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateDrafts(queryClient);
      // Where a put of a draft would land is read from the board, so a draft made, refined or
      // discarded changes what the board says about it.
      void invalidateBoard(queryClient);
    },
  });
}

export function useDiscardDraft(): UseMutationResult<ProfileDraft, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => discardProfileDraft(id),
    onSuccess: () => toast.success("Draft discarded"),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateDrafts(queryClient);
      // Where a put of a draft would land is read from the board, so a draft made, refined or
      // discarded changes what the board says about it.
      void invalidateBoard(queryClient);
    },
  });
}

/**
 * Live validation for the JSON editor.
 *
 * A mutation rather than a query even though it reads nothing: it is fired by
 * typing, and a query keyed on the document would fill the cache with an entry
 * per keystroke.
 */
export function usePreviewDraft(): UseMutationResult<
  DraftPreview,
  Error,
  { baseVersionId: number; profile: Record<string, unknown> }
> {
  return useMutation({
    mutationFn: ({ baseVersionId, profile }) => previewProfileDraft(baseVersionId, profile),
  });
}
