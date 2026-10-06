import type { QueryClient } from "@tanstack/react-query";
import { act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useEventInvalidation } from "@/hooks/useEventInvalidation";
import { useAnswerClaim, useConfirmAllClaims, useRunReview } from "@/hooks/useReview";
import { queryKeys } from "@/lib/queryKeys";
import type { SseMessage } from "@/lib/sse";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";
import { review } from "@/test/reviewFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { runReview, answerReviewClaim, confirmAllReviewClaims, handlers, subscribeToEventSource } =
  vi.hoisted(() => {
    const handlers: { current: { onMessage?: (m: SseMessage) => void } } = { current: {} };
    return {
      runReview: vi.fn(),
      answerReviewClaim: vi.fn(),
      confirmAllReviewClaims: vi.fn(),
      handlers,
      subscribeToEventSource: vi.fn((_url: string, given: Record<string, unknown>) => {
        handlers.current = given;
        return () => {};
      }),
    };
  });
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  runReview,
  answerReviewClaim,
  confirmAllReviewClaims,
}));
vi.mock("@/lib/sse", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sse")>()),
  subscribeToEventSource,
}));

beforeEach(() => {
  vi.clearAllMocks();
  runReview.mockResolvedValue(review({ status: "running", finished_at: null }));
  answerReviewClaim.mockResolvedValue(review());
  confirmAllReviewClaims.mockResolvedValue(review());
});

function spyOn(queryClient: QueryClient): unknown[][] {
  const keys: unknown[][] = [];
  const original = queryClient.invalidateQueries.bind(queryClient);
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    keys.push([...((filters?.queryKey ?? []) as readonly unknown[])]);
    return original(filters);
  });
  return keys;
}

/**
 * A reading is shown by the shots list (the badge and its state), each shot's detail (claims)
 * and fields (the checks a reading's free-text results join), and the Set pages that list the
 * shot with its badge. Every write and every event is pinned, because a missing key is a badge
 * that still says Reading… after the answer, and nothing else in a render test shows it.
 */
const WRITES = [
  ["starting a reading", () => useRunReview(), { shotId: 129 }],
  ["confirming a claim", () => useAnswerClaim(), { reviewId: 1, claimId: 2, status: "confirmed" }],
  [
    "rejecting a claim with a reason",
    () => useAnswerClaim(),
    { reviewId: 1, claimId: 2, status: "rejected", reason: "no" },
  ],
  ["confirming every claim", () => useConfirmAllClaims(), { reviewId: 1 }],
] as const;

describe("every reading write invalidates what it touches", () => {
  it.each(WRITES)("%s", async (_name, hook, variables) => {
    const { result, queryClient } = renderHookWithQueryClient(hook as () => any);
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(variables);

    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys).toContainEqual(queryKeys.shots.all);
    expect(keys).toContainEqual(queryKeys.sets.all);
  });

  it("also when an answer is refused, so the card shows the answer that won", async () => {
    answerReviewClaim.mockRejectedValue(new Error("a newer reading is in force"));
    const { result, queryClient } = renderHookWithQueryClient(() => useAnswerClaim());
    const keys = spyOn(queryClient);

    await expect(
      result.current.mutateAsync({ reviewId: 1, claimId: 2, status: "confirmed" }),
    ).rejects.toThrow();

    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys).toContainEqual(queryKeys.shots.all);
  });

  it("sends an answer to its own route and Confirm all as one call", async () => {
    const one = renderHookWithQueryClient(() => useAnswerClaim());
    await one.result.current.mutateAsync({
      reviewId: 4,
      claimId: 9,
      status: "rejected",
      reason: "too tight",
    });
    expect(answerReviewClaim).toHaveBeenCalledWith(4, 9, {
      status: "rejected",
      reason: "too tight",
    });
    expect(confirmAllReviewClaims).not.toHaveBeenCalled();

    const all = renderHookWithQueryClient(() => useConfirmAllClaims());
    await all.result.current.mutateAsync({ reviewId: 4 });
    expect(confirmAllReviewClaims).toHaveBeenCalledTimes(1);
    expect(confirmAllReviewClaims).toHaveBeenCalledWith(4, undefined);

    await all.result.current.mutateAsync({ reviewId: 4, exceptKinds: ["prediction"] });
    expect(confirmAllReviewClaims).toHaveBeenLastCalledWith(4, ["prediction"]);
  });
});

describe("every reading event invalidates what it touches", () => {
  it.each(["review.started", "review.finished", "review.failed", "review.answered"])(
    "%s",
    (event) => {
      const { queryClient } = renderHookWithQueryClient(() => useEventInvalidation("/api/events"));
      const keys = spyOn(queryClient);

      act(() => handlers.current.onMessage?.({ event, data: { shot_id: 129, review_id: 1 } }));

      expect(keys).toContainEqual(queryKeys.shots.all);
      expect(keys).toContainEqual(queryKeys.sets.all);
    },
  );
});
