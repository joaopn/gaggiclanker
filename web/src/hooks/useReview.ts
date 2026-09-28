import { type UseMutationResult, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { runReview } from "@/api/client";
import type { ShotReview } from "@/api/types";
import { invalidateShots } from "@/lib/invalidate";

/**
 * Asking for a shot's review: the one way one starts.
 *
 * Two things here are not the shape a mutation usually has.
 *
 * **The mutation resolves when the work is queued, not when it is done.** The
 * server answers 202 with a `running` row; the outcome arrives as
 * `review.finished` / `review.failed` on the event stream, which
 * `EVENT_INVALIDATIONS` turns into a re-read of the shot, so the card follows
 * the row rather than this mutation.
 *
 * **A failed review resolves, it does not reject.** The server answers 2xx with
 * a `failed` row carrying the error, because a 502 would leave the caller an
 * error and no row — and the row is the one thing that explains what
 * happened. So `onSuccess` branches on `status`.
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
        toast.info("Reviewing — this takes a minute or so");
      } else if (review.status !== "ok") {
        // The row exists and carries the error; the card renders it. The toast
        // exists so somebody who has scrolled away still learns it went wrong.
        toast.error(review.error ?? "The review did not complete");
      }
    },
    onError: (error) => toast.error(`Could not start the review: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      // The shot detail carries its reviews; nothing else shows one.
      void invalidateShots(queryClient, String(variables.shotId));
    },
  });
}
