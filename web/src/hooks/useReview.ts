import { type UseMutationResult, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { answerReviewClaim, runReview } from "@/api/client";
import type { ShotReview } from "@/api/types";
import { invalidateReadings } from "@/lib/invalidate";

/**
 * Asking for a shot's reading: the one way one starts.
 *
 * Two things here are not the shape a mutation usually has.
 *
 * **The mutation resolves when the work is queued, not when it is done.** The
 * server answers 202 with a `running` row; the outcome arrives as
 * `review.finished` / `review.failed` on the event stream, which
 * `EVENT_INVALIDATIONS` turns into a re-read of the shots, so the badge and the card
 * follow the row rather than this mutation.
 *
 * **A failed reading resolves, it does not reject.** The server answers 2xx with
 * a `failed` row carrying the error, because a 502 would leave the caller an
 * error and no row — and the row is the one thing that explains what
 * happened. So `onSuccess` branches on `status`.
 *
 * Starting changes what the shots list, the shot's detail and fields, and the Set pages say
 * (the badge turns to Reviewing…), so it invalidates all of them, as an answer does.
 */
export function useRunReview(): UseMutationResult<
  ShotReview,
  Error,
  { shotId: number; model?: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, model }) => runReview(shotId, { model }),
    onSuccess: (review) => {
      if (review.status === "running") {
        toast.info("Reading — this takes a minute or so");
      } else if (review.status !== "ok") {
        // The row exists and carries the error; the card renders it. The toast
        // exists so somebody who has scrolled away still learns it went wrong.
        toast.error(review.error ?? "The reading did not complete");
      }
    },
    onError: (error) => toast.error(`Could not start the reading: ${error.message}`),
    onSettled: () => void invalidateReadings(queryClient),
  });
}

/**
 * Rejecting one claim, or restoring it. `reviewId` is the review in force, never the newest
 * attempt: while a review is made again, the newest id is not the one whose claims can be
 * answered. A refusal (409: another tab answered, or a newer review finished) toasts and still
 * re-reads, so the card shows what is true.
 */
export function useAnswerClaim(): UseMutationResult<
  ShotReview,
  Error,
  { reviewId: number; claimId: number; status: "confirmed" | "rejected" }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ reviewId, claimId, status }) => answerReviewClaim(reviewId, claimId, { status }),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateReadings(queryClient),
  });
}
