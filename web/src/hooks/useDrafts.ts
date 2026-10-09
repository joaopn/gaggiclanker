import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  checkDraftName,
  createProfileDraft,
  discardProfileDraft,
  getDraftStanding,
  getProfileDraft,
  getProfileDrafts,
  previewProfileDraft,
  refineProfileDraft,
} from "@/api/client";
import type {
  DraftCreateBody,
  DraftPreview,
  DraftStanding,
  NameCheck,
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
    onSuccess: () => toast.success("Proposal declined"),
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

/**
 * Where one proposed profile version stands: waiting, approved, on the machine, not on the
 * machine, declined or replaced, with the server's words for it. The one read the proposal card
 * draws from, in the chat and on the Profiles page. It sits under the `drafts` prefix, so every
 * draft write, board write and sync event that refreshes the drafts refreshes it too.
 */
export function useDraftStanding(
  draftId: number | undefined,
): UseQueryResult<DraftStanding, Error> {
  return useQuery({
    queryKey: queryKeys.drafts.standing(draftId as number),
    queryFn: () => getDraftStanding(draftId as number),
    enabled: draftId !== undefined && Number.isFinite(draftId),
  });
}

/**
 * Would approving this proposal under the typed name be refused? A query on the debounced name,
 * never stored: the answer is the server's own placement rule (the one the put asks), and it is
 * kept apart from the drafts' keys, so a refresh of them is not a request. Nothing is asked for an empty name (the card says so itself) or while `enabled` is false.
 */
export function useNameCheck(
  draftId: number,
  label: string,
  enabled: boolean,
): UseQueryResult<NameCheck, Error> {
  return useQuery({
    queryKey: queryKeys.nameChecks.one(draftId, label),
    queryFn: () => checkDraftName(draftId, label),
    enabled: enabled && label.trim() !== "",
    // One answer per typed value, asked once: it is not refreshed with the drafts (the list gaining
    // that name meanwhile is refused by the put in the same words), and a stale answer for an old
    // keystroke is never shown, since the key carries the name.
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    gcTime: 0,
  });
}
