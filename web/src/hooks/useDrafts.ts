import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  approveProfileDraft,
  createProfileDraft,
  discardProfileDraft,
  getProfileDraft,
  getProfileDrafts,
  getProfileVersion,
  previewProfileDraft,
  pushProfileDraft,
  refineProfileDraft,
  rollbackProfileDraft,
} from "@/api/client";
import {
  clampChangesOf,
  type DraftCreateBody,
  type DraftPreview,
  type DraftPushResult,
  type ProfileDraft,
  type ProfileDraftDetail,
  type ProfileDraftListData,
} from "@/api/types";
import { invalidateDeviceWrites, invalidateDrafts, invalidateProfiles } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The draft queue and the four buttons on it.
 *
 * One thing here is unlike every other mutation in this app and it is the
 * reason this file has a doc comment: **a successful push is not a successful
 * push.** `POST /profile-drafts/{id}/push` answers 200 whether the machine
 * stored what we sent or something else, because the profile is on the display
 * either way and only the draft's `status` says which happened. So `onSuccess`
 * branches on the row — the same rule `useRunAnalysis` follows for an analysis
 * that came back `failed`.
 */

export function useProfileDrafts(
  params: { status?: string; open?: boolean } = {},
): UseQueryResult<ProfileDraftListData, Error> {
  return useQuery({
    queryKey: queryKeys.drafts.list(params),
    queryFn: () => getProfileDrafts(params),
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
    },
  });
}

/**
 * Stage a stored version with nothing edited.
 *
 * The plain path to the machine, and the common one: a profile that is already
 * right and only needs to get there should not have to go through a JSON
 * editor first. Two calls rather than one because the versions *list* carries
 * summaries — a document per row would be megabytes — so the document is read
 * here and posted straight back through the same manual draft path the editor
 * uses, which is what keeps the schema, the safety policy and the audit in the
 * way.
 *
 * **The summary says "no edits", not "unchanged".** Those are not the same
 * claim: the safety policy is narrower than the firmware, so a version
 * mirrored off a machine at 118 °C is stored at 100 and the draft genuinely
 * differs from the document that was posted. Nobody edited it, and the card's
 * clamp list is where what moved is stated — a summary promising an unchanged
 * profile beside a clamp list saying otherwise is the one sentence in this
 * flow that must not be wrong. The toast says so too, because the clamp
 * happens on a page the person may scroll straight past.
 */
export function useStageVersionAsIs(): UseMutationResult<
  ProfileDraft,
  Error,
  { versionId: number; label: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ versionId, label }) => {
      const version = await getProfileVersion(versionId);
      return createProfileDraft({
        base_version_id: versionId,
        profile: version.profile as Record<string, unknown>,
        change_summary: `Staged from ${label}, no edits`,
      });
    },
    onSuccess: (draft) => {
      const clamped = clampChangesOf(draft).length;
      toast.success(
        clamped > 0
          ? `Staged — the safety policy moved ${clamped} value${clamped === 1 ? "" : "s"}`
          : "Staged for the machine",
      );
    },
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateDrafts(queryClient);
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
    },
  });
}

export function useApproveDraft(): UseMutationResult<
  ProfileDraft,
  Error,
  { id: number; acknowledgeStopChanges?: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, acknowledgeStopChanges }) =>
      approveProfileDraft(id, acknowledgeStopChanges ?? false),
    onSuccess: () => toast.success("Approved — ready to push"),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateDrafts(queryClient);
    },
  });
}

export function usePushDraft(): UseMutationResult<
  DraftPushResult,
  Error,
  { id: number; setId?: number; allowStaleBase?: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, setId, allowStaleBase }) => pushProfileDraft(id, { setId, allowStaleBase }),
    onSuccess: (result) => {
      if (result.draft.status === "pushed") {
        toast.success(`On the machine as ${result.draft.pushed_device_profile_id}`);
      } else {
        // 200, and the worst outcome this feature has: the machine took the
        // profile and stored something else. Say so, and point at the button
        // that removes it.
        toast.error(
          result.draft.error ??
            "The machine stored something other than what was sent — roll it back",
        );
      }
    },
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateDrafts(queryClient);
      void invalidateProfiles(queryClient);
      void invalidateDeviceWrites(queryClient);
    },
  });
}

export function useRollbackDraft(): UseMutationResult<ProfileDraft, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => rollbackProfileDraft(id),
    onSuccess: () => toast.success("Deleted from the machine"),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateDrafts(queryClient);
      void invalidateProfiles(queryClient);
      void invalidateDeviceWrites(queryClient);
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
