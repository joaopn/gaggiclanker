import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { REVIEW_EXPLAINED, ReviewCard } from "@/components/shots/ReviewCard";
import { EVENT_INVALIDATIONS } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";
import {
  createTestQueryClient,
  renderWithQueryClient,
  setupUser,
} from "@/test/renderWithQueryClient";
import { review } from "@/test/reviewFixtures";
import { vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { runReview, getVocabulary } = vi.hoisted(() => ({
  runReview: vi.fn(),
  getVocabulary: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  runReview,
  getVocabulary,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  runReview.mockResolvedValue(review({ status: "running", finished_at: null }));
});

describe("ReviewCard", () => {
  it("before any review: the button and one line saying what it does", async () => {
    const user = setupUser();
    renderWithQueryClient(<ReviewCard shotId={129} reviews={[]} />);

    const empty = screen.getByTestId("review-empty");
    expect(empty).toHaveTextContent(REVIEW_EXPLAINED);
    expect(REVIEW_EXPLAINED).toContain("without your judgement");
    expect(REVIEW_EXPLAINED).toContain("changes nothing else.");
    const button = within(empty).getByRole("button", { name: "Review" });

    await user.click(button);

    await waitFor(() => expect(runReview).toHaveBeenCalledWith(129, { model: undefined }));
    expect(screen.queryByTestId("review-reading")).toBeNull();
  });

  it("while running: says so and holds the button", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[review({ id: 2, status: "running", finished_at: null, summary: null })]}
      />,
    );

    expect(screen.getByTestId("review-running")).toHaveTextContent("Reading this shot since");
    expect(screen.getByTestId("run-review")).toBeDisabled();
    expect(screen.getByTestId("run-review")).toHaveTextContent("Reviewing");
    expect(screen.queryByTestId("review-empty")).toBeNull();
  });

  it("after: the summary first, then the citations and where it came from", async () => {
    const user = setupUser();
    renderWithQueryClient(<ReviewCard shotId={129} reviews={[review()]} />);

    const reading = screen.getByTestId("review-reading");
    // The summary leads.
    expect(reading.firstElementChild).toBe(screen.getByTestId("review-summary"));
    expect(screen.getByTestId("review-summary")).toHaveTextContent(
      "Slow start, thin middle; the cup filled early.",
    );
    // A reading predicts no taste and describes nothing at length.
    expect(screen.queryByTestId("review-taste")).toBeNull();
    expect(screen.queryByTestId("review-description")).toBeNull();
    // The citations link to the Knowledge page, as before.
    expect(
      within(screen.getByTestId("review-rules")).getByRole("link", { name: "hierarchy" }),
    ).toHaveAttribute("href", "/knowledge?rule=hierarchy");
    expect(
      within(screen.getByTestId("review-excerpts")).getByRole("link", {
        name: "ESPRESSO_TASTING_GUIDE#sour-vs-bitter",
      }),
    ).toHaveAttribute(
      "href",
      "/knowledge?tab=docs&doc=ESPRESSO_TASTING_GUIDE&chunk=ESPRESSO_TASTING_GUIDE%23sour-vs-bitter",
    );
    expect(screen.getByTestId("review-provenance")).toHaveTextContent("by claude-careful");
    expect(screen.getByTestId("review-provenance")).toHaveTextContent("not a measurement");

    await user.click(screen.getByRole("button", { name: "Review again" }));
    await waitFor(() => expect(runReview).toHaveBeenCalledWith(129, { model: undefined }));
  });

  it("renders the newest finished review, not an older one", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[
          review({ id: 3, summary: "The newest reading." }),
          review({ id: 2, summary: "An older reading." }),
        ]}
      />,
    );
    expect(screen.getByTestId("review-summary")).toHaveTextContent("The newest reading.");
    expect(screen.queryByText("An older reading.")).toBeNull();
  });

  it("failed: shows the stored error and the button, and the last reading below it", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[
          review({
            id: 4,
            status: "failed",
            error: "auth: invalid api key",
            summary: null,
          }),
          review({ id: 3, summary: "The last good reading." }),
        ]}
      />,
    );

    expect(screen.getByTestId("review-failed")).toHaveTextContent("That review did not complete");
    expect(screen.getByTestId("review-error")).toHaveTextContent("auth: invalid api key");
    expect(screen.getByRole("button", { name: "Review again" })).toBeEnabled();
    expect(screen.getByTestId("review-summary")).toHaveTextContent("The last good reading.");
  });

  it("an interrupted review says the process stopped", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[review({ status: "interrupted", error: "stopped", summary: null })]}
      />,
    );
    expect(screen.getByTestId("review-failed")).toHaveTextContent("Interrupted");
    expect(screen.queryByTestId("review-reading")).toBeNull();
  });

  it("is refreshed by the reading events, which re-read the shots and the Sets", () => {
    for (const event of ["review.started", "review.finished", "review.failed"]) {
      const keys = EVENT_INVALIDATIONS[event];
      expect(keys, event).toEqual([queryKeys.shots.all, queryKeys.sets.all]);
    }
  });

  describe("while a review runs, the card re-reads its shot", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    function detailInvalidations(spy: { mock: { calls: unknown[][] } }): number {
      const detail = JSON.stringify(queryKeys.shots.detail("129"));
      return spy.mock.calls.filter(
        ([filters]) => JSON.stringify((filters as { queryKey?: unknown })?.queryKey) === detail,
      ).length;
    }

    it("every five seconds, since the event stream is lossy", () => {
      vi.useFakeTimers();
      const queryClient = createTestQueryClient();
      const spy = vi.spyOn(queryClient, "invalidateQueries");
      renderWithQueryClient(
        <ReviewCard
          shotId={129}
          reviews={[review({ status: "running", finished_at: null, summary: null })]}
        />,
        { queryClient },
      );

      act(() => {
        vi.advanceTimersByTime(4900);
      });
      expect(detailInvalidations(spy)).toBe(0);
      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(detailInvalidations(spy)).toBe(1);
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(detailInvalidations(spy)).toBe(2);
    });

    it("not at all when nothing is running", () => {
      vi.useFakeTimers();
      const queryClient = createTestQueryClient();
      const spy = vi.spyOn(queryClient, "invalidateQueries");
      renderWithQueryClient(<ReviewCard shotId={129} reviews={[review()]} />, {
        queryClient,
      });

      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      expect(detailInvalidations(spy)).toBe(0);
    });
  });
});
