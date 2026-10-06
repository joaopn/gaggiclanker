import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReadingBlock, ReviewClaim, ShotReview } from "@/api/types";
import { ReviewBadge } from "@/components/shots/ReviewBadge";
import { REVIEW_EXPLAINED, ReviewCard } from "@/components/shots/ReviewCard";
import { type ClaimSpanControls, useClaimSpan } from "@/lib/claimSpan";
import { queryKeys } from "@/lib/queryKeys";
import { claim, evidence, readingBlock } from "@/test/readingFixtures";
import {
  createTestQueryClient,
  renderWithQueryClient,
  setupUser,
} from "@/test/renderWithQueryClient";
import { review } from "@/test/reviewFixtures";
import { vocabulary } from "@/test/setsFixtures";
import { leverSignedFields } from "@/test/shotFieldsFixture";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { runReview, answerReviewClaim, confirmAllReviewClaims, getVocabulary } = vi.hoisted(() => ({
  runReview: vi.fn(),
  answerReviewClaim: vi.fn(),
  confirmAllReviewClaims: vi.fn(),
  getVocabulary: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  runReview,
  answerReviewClaim,
  confirmAllReviewClaims,
  getVocabulary,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  runReview.mockResolvedValue(review({ status: "running", finished_at: null }));
  answerReviewClaim.mockResolvedValue(review());
  confirmAllReviewClaims.mockResolvedValue(review());
});

const NO_SPAN: ClaimSpanControls = {
  look: () => undefined,
  pin: () => undefined,
  pinnedClaimId: null,
};

/** The card with the block the server would serve beside the reviews. */
function Card({
  reviews,
  reading,
  span = NO_SPAN,
  ...rest
}: {
  reviews: ShotReview[];
  reading?: ReadingBlock;
  span?: ClaimSpanControls;
  decision?: string | null;
  hasPrediction?: boolean;
}) {
  const latest = reviews[0];
  const inForce = reviews.find((r) => r.status === "ok");
  const block =
    reading ??
    readingBlock({
      state: latest === undefined ? "unread" : latest.status === "ok" ? "read" : "failed",
      review_id: latest?.id,
      in_force_id: inForce?.id,
    });
  return <ReviewCard shotId={129} reviews={reviews} reading={block} span={span} {...rest} />;
}

const observation = (overrides: Partial<ReviewClaim> = {}) => claim({ review_id: 1, ...overrides });

describe("ReviewCard", () => {
  it("before any reading: the button and one line saying what it does", async () => {
    const user = setupUser();
    renderWithQueryClient(<Card reviews={[]} />);

    const empty = screen.getByTestId("review-empty");
    expect(empty).toHaveTextContent(REVIEW_EXPLAINED);
    expect(REVIEW_EXPLAINED).toContain("without your judgement");
    expect(REVIEW_EXPLAINED).toContain("changes nothing else.");
    expect(REVIEW_EXPLAINED).toMatch(/confirm or reject each claim/);
    expect(REVIEW_EXPLAINED).not.toMatch(/taste prediction|blind/i);

    await user.dblClick(within(empty).getByRole("button", { name: "Read this shot" }));

    // A double click is one request.
    await waitFor(() => expect(runReview).toHaveBeenCalledTimes(1));
    expect(runReview).toHaveBeenCalledWith(129, { model: undefined });
    expect(screen.queryByTestId("review-reading")).toBeNull();
  });

  it("says a discarded shot is not read, with no button", () => {
    renderWithQueryClient(<Card reviews={[]} reading={readingBlock({ state: "not_readable" })} />);
    expect(screen.getByTestId("review-unreadable")).toHaveTextContent("not read");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("while running: says so and holds the button", () => {
    renderWithQueryClient(
      <Card
        reviews={[review({ id: 2, status: "running", finished_at: null, summary: null })]}
        reading={readingBlock({ state: "running", review_id: 2 })}
      />,
    );

    expect(screen.getByTestId("review-running")).toHaveTextContent("Reading this shot since");
    expect(screen.queryByTestId("review-empty")).toBeNull();
  });

  it("while a re-read runs, the earlier reading stays below it and its Read again is held", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <Card
        reviews={[
          review({ id: 3, status: "running", finished_at: null, summary: null }),
          review({
            id: 2,
            summary: "The reading in force.",
            claims: [observation({ review_id: 2 })],
          }),
        ]}
        reading={readingBlock({ state: "running", review_id: 3, in_force_id: 2, unanswered: 1 })}
      />,
    );

    expect(screen.getByTestId("review-running")).toHaveTextContent("earlier reading stays below");
    expect(screen.getByTestId("review-summary")).toHaveTextContent("The reading in force.");
    const button = screen.getByTestId("run-review");
    expect(button).toHaveAttribute("aria-disabled", "true");
    await user.click(button);
    expect(runReview).not.toHaveBeenCalled();
  });

  it("answers a claim through the reading in force, never the newest attempt", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <Card
        reviews={[
          review({ id: 9, status: "running", finished_at: null, summary: null }),
          review({ id: 4, claims: [observation({ id: 40, review_id: 4 })] }),
        ]}
        reading={readingBlock({ state: "running", review_id: 9, in_force_id: 4, unanswered: 1 })}
      />,
    );

    await user.click(screen.getByTestId("claim-confirm"));

    await waitFor(() =>
      expect(answerReviewClaim).toHaveBeenCalledWith(4, 40, {
        status: "confirmed",
        reason: undefined,
      }),
    );
  });

  describe("a finished reading", () => {
    const freeText = claim({
      id: 21,
      review_id: 1,
      position: 1,
      kind: "free_text",
      expectation_id: 7,
      held: false,
      fault: "unstable",
      phase: "decline",
      window_text: "the decline",
      text: "Pressure and flow do not fall together.",
    });
    const prediction = claim({
      id: 22,
      review_id: 1,
      position: 2,
      kind: "prediction",
      stance: "partly",
      fault: null,
      phase: null,
      start_s: null,
      end_s: null,
      window_text: "the whole shot",
      text: "The shot was sweeter than predicted.",
      evidence: [],
    });
    const found = observation({ id: 20, position: 0 });
    const full = () =>
      review({
        id: 1,
        claims: [found, freeText, prediction],
      });

    it("shows the summary, the expectations, the prediction, the claims, then the provenance and Read again, in that order", () => {
      renderWithQueryClient(
        <Card
          reviews={[full()]}
          decision="keep"
          hasPrediction
          reading={readingBlock({
            state: "read",
            verdict: "entries",
            review_id: 1,
            in_force_id: 1,
            unanswered: 3,
          })}
        />,
      );

      const order = [
        screen.getByTestId("review-summary"),
        screen.getByTestId("reading-expectations"),
        screen.getByTestId("reading-prediction"),
        screen.getByTestId("reading-claims"),
        screen.getByTestId("claims-confirm-all"),
        screen.getByTestId("review-rules"),
        screen.getByTestId("review-excerpts"),
        screen.getByTestId("review-provenance"),
        screen.getByTestId("read-again-ask"),
        screen.getByTestId("run-review"),
      ];
      for (let i = 1; i < order.length; i += 1) {
        expect(
          order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING,
          `section ${i}`,
        ).toBeTruthy();
      }
      expect(screen.getByTestId("review-summary")).toHaveTextContent(
        "Slow start, thin middle; the cup filled early.",
      );
      expect(screen.getByTestId("review-provenance")).toHaveTextContent("by claude-careful");
      expect(screen.getByTestId("review-provenance")).toHaveTextContent("not a measurement");
    });

    it("links the cited rules and excerpts to the Knowledge page", () => {
      renderWithQueryClient(<Card reviews={[full()]} />);
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
    });

    it("shows a claim's phase, fault, sentence and evidence with its value and limit", () => {
      renderWithQueryClient(
        <Card
          reviews={[
            review({
              claims: [
                observation({
                  evidence: [
                    evidence({
                      sentence: "cup weight at the end of the ramp, as a share of the target",
                      value: 117.24,
                      unit: "%",
                      limit_text: "at most 15 % of target",
                    }),
                    evidence({
                      sentence: "mean of flow over the decline",
                      value: null,
                      absent: "no scale",
                    }),
                  ],
                }),
              ],
            }),
          ]}
        />,
      );
      const card = within(screen.getByTestId("claim"));
      expect(card.getByTestId("claim-window")).toHaveTextContent("decline");
      expect(card.getByTestId("claim-fault")).toHaveTextContent("unstable");
      expect(card.getByTestId("claim-text")).toHaveTextContent(
        "Pressure wanders through the decline.",
      );
      const [first, second] = card.getAllByTestId("claim-evidence");
      expect(first).toHaveTextContent(
        "cup weight at the end of the ramp, as a share of the target: 117.24 %, at most 15 % of target",
      );
      expect(second).toHaveTextContent("not measured (no scale)");
    });

    it("does not say a limit twice when the sentence already carries it", () => {
      renderWithQueryClient(
        <Card
          reviews={[
            review({
              claims: [
                observation({
                  evidence: [
                    evidence({
                      sentence: "mean of pressure over the decline, at most 6 bar",
                      limit_text: "at most 6 bar",
                    }),
                  ],
                }),
              ],
            }),
          ]}
        />,
      );
      expect(
        screen.getByTestId("claim-evidence").textContent?.match(/at most 6 bar/g),
      ).toHaveLength(1);
    });

    it("marks a claim the numbers do not bear out, and only that one", () => {
      renderWithQueryClient(
        <Card
          reviews={[
            review({
              claims: [
                observation({ id: 1, supported: false }),
                observation({ id: 2, position: 1, supported: true }),
              ],
            }),
          ]}
        />,
      );
      const [bad, good] = screen.getAllByTestId("claim");
      expect(within(bad).getByTestId("claim-unsupported")).toHaveTextContent(
        "the numbers don't bear this out",
      );
      expect(within(good).queryByTestId("claim-unsupported")).toBeNull();
    });

    it("shows a free-text result held or failed, with its expectation and its tier colour", () => {
      renderWithQueryClient(
        <ReviewCard
          shotId={129}
          reviews={[full()]}
          reading={readingBlock({ state: "read", review_id: 1, in_force_id: 1 })}
          span={NO_SPAN}
          checks={[
            {
              expectation_id: 7,
              tier: "critical",
              sentence: "pressure and flow fall together through the decline",
            } as never,
          ]}
        />,
      );
      const item = within(screen.getByTestId("reading-expectations")).getByTestId("claim");
      expect(within(item).getByTestId("claim-result")).toHaveTextContent("failed");
      expect(item).toHaveClass("border-status-bad/40");
      expect(within(item).getByTestId("claim-expectation")).toHaveTextContent(
        "pressure and flow fall together through the decline",
      );
    });

    it("holds the prediction back until the shot has a decision, and reveals it on Show", async () => {
      const user = setupUser();
      const { rerender } = renderWithQueryClient(
        <Card reviews={[full()]} decision={null} hasPrediction />,
      );
      expect(screen.getByTestId("prediction-hidden")).toBeInTheDocument();
      expect(screen.queryByText("The shot was sweeter than predicted.")).toBeNull();
      expect(screen.queryByTestId("claim-stance")).toBeNull();

      await user.click(screen.getByTestId("prediction-show"));
      expect(screen.queryByTestId("prediction-hidden")).toBeNull();
      expect(screen.getByTestId("claim-stance")).toHaveTextContent("partly as predicted");
      // Focus lands on the section the button was replaced by, not on the page.
      expect(screen.getByTestId("prediction-heading")).toHaveFocus();

      rerender(<Card reviews={[full()]} decision="keep" hasPrediction />);
      expect(screen.getByText("The shot was sweeter than predicted.")).toBeInTheDocument();
    });

    it("shows the stance from the start once the shot has a decision, taking no focus", () => {
      renderWithQueryClient(<Card reviews={[full()]} decision="keep" hasPrediction />);
      expect(screen.queryByTestId("prediction-show")).toBeNull();
      expect(screen.getByTestId("claim-stance")).toBeInTheDocument();
      expect(screen.getByTestId("prediction-heading")).not.toHaveFocus();
    });

    it("never confirms a stance nobody was shown: Confirm all leaves it out and does not count it", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[full()]} decision={null} hasPrediction />);

      // Three claims wait, one of them the hidden stance.
      expect(screen.getByTestId("claims-confirm-all")).toHaveTextContent("Confirm all (2)");
      await user.click(screen.getByTestId("claims-confirm-all"));

      await waitFor(() => expect(confirmAllReviewClaims).toHaveBeenCalledTimes(1));
      expect(confirmAllReviewClaims).toHaveBeenCalledWith(1, ["prediction"]);
    });

    it("includes the stance in Confirm all, and in its count, once it is revealed", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[full()]} decision={null} hasPrediction />);

      await user.click(screen.getByTestId("prediction-show"));
      expect(screen.getByTestId("claims-confirm-all")).toHaveTextContent("Confirm all (3)");
      await user.click(screen.getByTestId("claims-confirm-all"));

      await waitFor(() => expect(confirmAllReviewClaims).toHaveBeenCalledTimes(1));
      expect(confirmAllReviewClaims).toHaveBeenCalledWith(1, undefined);
    });

    it("includes the stance in Confirm all when the shot has a decision", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[full()]} decision="keep" hasPrediction />);
      expect(screen.getByTestId("claims-confirm-all")).toHaveTextContent("Confirm all (3)");
      await user.click(screen.getByTestId("claims-confirm-all"));
      await waitFor(() => expect(confirmAllReviewClaims).toHaveBeenCalledWith(1, undefined));
    });

    it("a reveal belongs to one reading: a new reading in force holds its stance back again", async () => {
      const user = setupUser();
      const { rerender } = renderWithQueryClient(
        <Card reviews={[full()]} decision={null} hasPrediction />,
      );
      await user.click(screen.getByTestId("prediction-show"));
      const next = review({ id: 2, claims: [{ ...prediction, id: 90, review_id: 2 }] });
      rerender(<Card reviews={[next, full()]} decision={null} hasPrediction />);
      expect(screen.getByTestId("prediction-hidden")).toBeInTheDocument();
    });

    it("confirms one claim with one call", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[full()]} decision="keep" />);

      const item = screen
        .getAllByTestId("claim")
        .find((el) => el.getAttribute("data-claim-id") === "20");
      if (!item) throw new Error("no claim");
      await user.dblClick(within(item).getByTestId("claim-confirm"));

      await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(1));
      expect(answerReviewClaim).toHaveBeenCalledWith(1, 20, {
        status: "confirmed",
        reason: undefined,
      });
      expect(confirmAllReviewClaims).not.toHaveBeenCalled();
    });

    it("rejects with an optional one-line reason, Enter submitting", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[review({ claims: [found] })]} />);

      await user.click(screen.getByTestId("claim-reject"));
      await user.type(
        screen.getByPlaceholderText("Why (optional)"),
        "the pressure is steady{Enter}",
      );

      await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(1));
      expect(answerReviewClaim).toHaveBeenCalledWith(1, 20, {
        status: "rejected",
        reason: "the pressure is steady",
      });
    });

    it("rejects with no reason too", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[review({ claims: [found] })]} />);

      await user.click(screen.getByTestId("claim-reject"));
      await user.click(screen.getByTestId("reason-submit"));

      await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(1));
      expect(answerReviewClaim).toHaveBeenCalledWith(1, 20, { status: "rejected", reason: "" });
    });

    it("lets an answer be changed, each way", async () => {
      const user = setupUser();
      renderWithQueryClient(
        <Card
          reviews={[
            review({
              claims: [
                observation({ id: 30, position: 0, status: "confirmed" }),
                observation({ id: 31, position: 1, status: "rejected", reason: "wrong phase" }),
              ],
            }),
          ]}
        />,
      );
      const [confirmed, rejected] = screen.getAllByTestId("claim");
      expect(within(confirmed).queryByTestId("claim-confirm")).toBeNull();
      expect(within(rejected).queryByTestId("claim-reject")).toBeNull();
      expect(within(rejected).getByTestId("claim-reason")).toHaveTextContent("wrong phase");

      await user.click(within(rejected).getByRole("button", { name: "Change to confirm" }));
      await waitFor(() =>
        expect(answerReviewClaim).toHaveBeenCalledWith(1, 31, {
          status: "confirmed",
          reason: undefined,
        }),
      );
      await user.click(within(confirmed).getByRole("button", { name: "Change to reject" }));
      await user.click(screen.getByTestId("reason-submit"));
      await waitFor(() =>
        expect(answerReviewClaim).toHaveBeenCalledWith(1, 30, { status: "rejected", reason: "" }),
      );
    });

    it("confirms every waiting claim with one call, and offers it only while some wait", async () => {
      const user = setupUser();
      const { rerender } = renderWithQueryClient(<Card reviews={[full()]} decision="keep" />);

      expect(screen.getByTestId("claims-confirm-all")).toHaveTextContent("Confirm all (3)");
      await user.dblClick(screen.getByTestId("claims-confirm-all"));
      await waitFor(() => expect(confirmAllReviewClaims).toHaveBeenCalledTimes(1));
      expect(confirmAllReviewClaims).toHaveBeenCalledWith(1, undefined);
      expect(answerReviewClaim).not.toHaveBeenCalled();

      rerender(
        <Card
          reviews={[
            review({
              claims: [
                observation({ status: "confirmed" }),
                observation({ id: 2, status: "rejected" }),
              ],
            }),
          ]}
        />,
      );
      expect(screen.queryByTestId("claims-confirm-all")).toBeNull();
    });

    it("Read again with nothing confirmed starts a reading at once", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[review({ claims: [found] })]} />);

      await user.click(screen.getByRole("button", { name: "Read again" }));

      await waitFor(() => expect(runReview).toHaveBeenCalledTimes(1));
      expect(screen.getByTestId("read-again-ask")).toHaveAttribute("hidden");
    });

    it("Read again asks once, inline, when claims are confirmed, and says how many it sets aside", async () => {
      const user = setupUser();
      renderWithQueryClient(
        <Card
          reviews={[
            review({
              claims: [
                observation({ id: 1, status: "confirmed" }),
                observation({ id: 2, position: 1, status: "confirmed" }),
                observation({ id: 3, position: 2, status: "rejected" }),
              ],
            }),
          ]}
        />,
      );
      const ask = screen.getByTestId("read-again-ask");
      expect(ask).toHaveAttribute("hidden");

      await user.click(screen.getByTestId("run-review"));
      expect(runReview).not.toHaveBeenCalled();
      expect(ask).not.toHaveAttribute("hidden");
      expect(ask).toHaveTextContent("This sets aside 2 confirmed claims");
      expect(screen.getByTestId("run-review")).toHaveAttribute("aria-controls", ask.id);

      await user.click(screen.getByTestId("read-again-cancel"));
      expect(ask).toHaveAttribute("hidden");
      expect(runReview).not.toHaveBeenCalled();

      await user.click(screen.getByTestId("run-review"));
      await user.dblClick(screen.getByTestId("read-again-confirm"));
      await waitFor(() => expect(runReview).toHaveBeenCalledTimes(1));
      expect(runReview).toHaveBeenCalledWith(129, { model: undefined });
    });

    it("says one confirmed claim in the singular", async () => {
      const user = setupUser();
      renderWithQueryClient(
        <Card reviews={[review({ claims: [observation({ status: "confirmed" })] })]} />,
      );
      await user.click(screen.getByTestId("run-review"));
      expect(screen.getByTestId("read-again-ask")).toHaveTextContent(
        "sets aside 1 confirmed claim.",
      );
    });

    it("renders the reading in force, not an older one", () => {
      renderWithQueryClient(
        <Card
          reviews={[
            review({ id: 3, summary: "The newest reading." }),
            review({ id: 2, summary: "An older reading." }),
          ]}
        />,
      );
      expect(screen.getByTestId("review-summary")).toHaveTextContent("The newest reading.");
      expect(screen.queryByText("An older reading.")).toBeNull();
    });
  });

  it("failed: shows the stored error and the button, and the last reading below it", () => {
    renderWithQueryClient(
      <Card
        reviews={[
          review({ id: 4, status: "failed", error: "auth: invalid api key", summary: null }),
          review({ id: 3, summary: "The last good reading." }),
        ]}
        reading={readingBlock({ state: "failed", review_id: 4, in_force_id: 3, reason: "auth" })}
      />,
    );

    expect(screen.getByTestId("review-failed")).toHaveTextContent("That reading did not complete");
    expect(screen.getByTestId("review-error")).toHaveTextContent("auth: invalid api key");
    expect(screen.getByRole("button", { name: "Read again" })).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByTestId("review-summary")).toHaveTextContent("The last good reading.");
  });

  it("an interrupted reading says the process stopped", () => {
    renderWithQueryClient(
      <Card reviews={[review({ status: "interrupted", error: "stopped", summary: null })]} />,
    );
    expect(screen.getByTestId("review-failed")).toHaveTextContent("Interrupted");
    expect(screen.queryByTestId("review-reading")).toBeNull();
  });

  describe("hovering a claim", () => {
    function Harness() {
      const { shown, controls } = useClaimSpan(1);
      return (
        <>
          <p data-testid="shown">{shown ? `${shown.start}-${shown.end}` : "none"}</p>
          <Card
            reviews={[
              review({
                claims: [
                  observation({ id: 1, start_s: 12, end_s: 24 }),
                  observation({ id: 2, position: 1, start_s: 3, end_s: 6 }),
                  observation({ id: 3, position: 2, start_s: null, end_s: null }),
                ],
              }),
            ]}
            span={controls}
          />
        </>
      );
    }

    it("hands its span to the chart on hover and focus, one at a time", async () => {
      const user = setupUser();
      renderWithQueryClient(<Harness />);
      const [first, second] = screen.getAllByTestId("claim-span");

      await user.hover(first);
      expect(screen.getByTestId("shown")).toHaveTextContent("12-24");
      await user.unhover(first);
      expect(screen.getByTestId("shown")).toHaveTextContent("none");

      act(() => second.focus());
      expect(screen.getByTestId("shown")).toHaveTextContent("3-6");
      act(() => second.blur());
      expect(screen.getByTestId("shown")).toHaveTextContent("none");
    });

    it("pins on a press, lets go on the second, and keeps one span at a time", async () => {
      const user = setupUser();
      renderWithQueryClient(<Harness />);
      const [first, second] = screen.getAllByTestId("claim-span");

      await user.click(first);
      await user.unhover(first);
      expect(first).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByTestId("shown")).toHaveTextContent("12-24");

      await user.click(second);
      await user.unhover(second);
      expect(first).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByTestId("shown")).toHaveTextContent("3-6");

      await user.click(second);
      await user.unhover(second);
      expect(screen.getByTestId("shown")).toHaveTextContent("none");
    });

    it("gives a claim with no span no button for it", () => {
      renderWithQueryClient(<Harness />);
      expect(screen.getAllByTestId("claim-span")).toHaveLength(2);
      expect(screen.getAllByTestId("claim")).toHaveLength(3);
    });
  });

  describe("while a reading runs, the card re-reads its shot", () => {
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
        <Card reviews={[review({ status: "running", finished_at: null, summary: null })]} />,
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
      renderWithQueryClient(<Card reviews={[review()]} />, { queryClient });

      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      expect(detailInvalidations(spy)).toBe(0);
    });
  });
});

describe("the verdict line, the card's first", () => {
  const entries = { badge: leverSignedFields.badge, warnings: leverSignedFields.warnings };
  const cases: Array<
    [string, { badge: string | null; warnings: typeof entries.warnings }, ReadingBlock]
  > = [
    [
      "entries",
      entries,
      readingBlock({
        state: "read",
        verdict: "entries",
        review_id: 1,
        in_force_id: 1,
        unanswered: 2,
      }),
    ],
    [
      "entries, answered",
      entries,
      readingBlock({ state: "read", verdict: "entries", review_id: 1, in_force_id: 1 }),
    ],
    [
      "as intended",
      { badge: "As intended", warnings: [] },
      readingBlock({ state: "read", verdict: "as_intended", review_id: 1, in_force_id: 1 }),
    ],
    [
      "no signature",
      { badge: "No signature", warnings: [] },
      readingBlock({ state: "read", verdict: "no_signature", review_id: 1, in_force_id: 1 }),
    ],
    [
      "running",
      { badge: "Reading…", warnings: [] },
      readingBlock({ state: "running", review_id: 2, in_force_id: 1 }),
    ],
    [
      "failed",
      { badge: "Failed to run", warnings: [] },
      readingBlock({ state: "failed", review_id: 2, in_force_id: 1, reason: "timed out" }),
    ],
  ];

  it.each(cases)("says what the badge says, in its tone and fill: %s", (_name, served, reading) => {
    const reviews = [review({ id: 1, claims: [observation()] })];
    const { container } = renderWithQueryClient(
      <>
        <ReviewBadge badge={served.badge} warnings={served.warnings} reading={reading} />
        <ReviewCard
          shotId={129}
          reviews={reviews}
          reading={reading}
          span={NO_SPAN}
          badge={served.badge}
          warnings={served.warnings}
        />
      </>,
    );
    const badge = container.querySelector('[data-testid="review-badge"]');
    const line = screen.getByTestId("reading-verdict-badge");
    expect(line).toHaveTextContent((badge?.textContent ?? "").trim());
    expect(line.getAttribute("data-tone")).toBe(badge?.getAttribute("data-tone"));
    expect(line.getAttribute("data-filled")).toBe(badge?.getAttribute("data-filled"));
  });

  it("is the first thing in a reading, and the model's summary comes after it, labelled", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[review({ id: 1, summary: "A clean lever shot." })]}
        reading={readingBlock({ state: "read", verdict: "no_signature", in_force_id: 1 })}
        span={NO_SPAN}
        badge="No signature"
        warnings={[]}
      />,
    );
    const reading = screen.getByTestId("review-reading");
    expect(reading.firstElementChild).toBe(screen.getByTestId("reading-verdict"));
    expect(screen.getByTestId("reading-verdict-why")).toHaveTextContent(
      "read without a confirmed signature, so nothing was checked against the profile's intent",
    );
    const summary = screen.getByTestId("review-summary-block");
    expect(summary).toHaveTextContent("The model's summary: A clean lever shot.");
    expect(
      screen.getByTestId("reading-verdict").compareDocumentPosition(summary) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("lists the failures of an entries verdict, and says how many claims wait", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[review({ id: 1, claims: [observation()] })]}
        reading={readingBlock({ state: "read", verdict: "entries", in_force_id: 1, unanswered: 1 })}
        span={NO_SPAN}
        badge={entries.badge}
        warnings={entries.warnings}
      />,
    );
    expect(screen.getByTestId("reading-verdict-entries").querySelectorAll("li")).toHaveLength(
      entries.warnings.length,
    );
    expect(screen.getByTestId("reading-verdict-waiting")).toHaveTextContent("1 claim waits");
  });

  it("counts only what is on screen: a stance behind Show waits apart, and is not counted", async () => {
    const user = setupUser();
    const stance = claim({
      id: 22,
      review_id: 1,
      position: 2,
      kind: "prediction",
      stance: "partly",
      start_s: null,
      end_s: null,
      text: "The shot was sweeter than predicted.",
      evidence: [],
    });
    const reading = readingBlock({
      state: "read",
      verdict: "entries",
      in_force_id: 1,
      unanswered: 2,
    });
    const props = {
      shotId: 129,
      reading,
      span: NO_SPAN,
      badge: entries.badge,
      warnings: entries.warnings,
      hasPrediction: true,
    };
    renderWithQueryClient(
      <ReviewCard {...props} reviews={[review({ id: 1, claims: [observation(), stance] })]} />,
    );
    expect(screen.getByTestId("reading-verdict-waiting")).toHaveTextContent(
      "1 claim waits for your answer, and the prediction stance waits behind Show",
    );
    await user.click(screen.getByTestId("prediction-show"));
    expect(screen.getByTestId("reading-verdict-waiting")).toHaveTextContent(
      /^2 claims wait for your answer$/,
    );
  });

  it("says only the stance waits when nothing else does", () => {
    const stance = claim({
      id: 22,
      review_id: 1,
      kind: "prediction",
      stance: "partly",
      start_s: null,
      end_s: null,
      evidence: [],
    });
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[review({ id: 1, claims: [observation({ status: "confirmed" }), stance] })]}
        reading={readingBlock({ state: "read", verdict: "entries", in_force_id: 1, unanswered: 1 })}
        span={NO_SPAN}
        badge={entries.badge}
        warnings={entries.warnings}
        hasPrediction
      />,
    );
    expect(screen.getByTestId("reading-verdict-waiting")).toHaveTextContent(
      /^the prediction stance waits behind Show$/,
    );
  });

  it("never says No signature for a verdict of failures whose badge text has not arrived", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[review({ id: 1, claims: [observation()] })]}
        reading={readingBlock({ state: "read", verdict: "entries", in_force_id: 1, unanswered: 1 })}
        span={NO_SPAN}
        badge={undefined}
        warnings={undefined}
      />,
    );
    expect(screen.getByTestId("reading-verdict-badge")).not.toHaveTextContent("No signature");
    expect(screen.getByTestId("reading-verdict-why")).toHaveTextContent("still loading");
  });

  it("says As intended with its reason", () => {
    renderWithQueryClient(
      <ReviewCard
        shotId={129}
        reviews={[review({ id: 1 })]}
        reading={readingBlock({ state: "read", verdict: "as_intended", in_force_id: 1 })}
        span={NO_SPAN}
        badge="As intended"
        warnings={[]}
      />,
    );
    expect(screen.getByTestId("reading-verdict-badge")).toHaveTextContent("As intended");
    expect(screen.getByTestId("reading-verdict-why")).toHaveTextContent("held");
  });
});

describe("answering goes through the reading in force during a re-read", () => {
  const inForce = review({
    id: 4,
    claims: [
      observation({ id: 40, review_id: 4 }),
      observation({ id: 41, review_id: 4, position: 1 }),
    ],
  });
  const states: Array<[string, ShotReview, ReadingBlock]> = [
    [
      "a running re-read",
      review({ id: 9, status: "running", finished_at: null, summary: null }),
      readingBlock({ state: "running", review_id: 9, in_force_id: 4, unanswered: 2 }),
    ],
    [
      "a failed re-read",
      review({ id: 9, status: "failed", error: "boom", summary: null }),
      readingBlock({ state: "failed", review_id: 9, in_force_id: 4, unanswered: 2 }),
    ],
  ];

  it.each(states)("Confirm all, %s", async (_name, newest, reading) => {
    const user = setupUser();
    renderWithQueryClient(<Card reviews={[newest, inForce]} reading={reading} />);
    await user.click(screen.getByTestId("claims-confirm-all"));
    await waitFor(() => expect(confirmAllReviewClaims).toHaveBeenCalledTimes(1));
    // Never the newest attempt's id (9), which answers 409.
    expect(confirmAllReviewClaims).toHaveBeenCalledWith(4, undefined);
  });

  it.each(states)("a single Confirm and a Reject, %s", async (_name, newest, reading) => {
    const user = setupUser();
    renderWithQueryClient(<Card reviews={[newest, inForce]} reading={reading} />);
    const [first, second] = screen.getAllByTestId("claim");
    await user.click(within(first).getByTestId("claim-confirm"));
    await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(1));
    expect(answerReviewClaim.mock.calls[0].slice(0, 2)).toEqual([4, 40]);
    await user.click(within(second).getByTestId("claim-reject"));
    await user.click(screen.getByTestId("reason-submit"));
    await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(2));
    expect(answerReviewClaim.mock.calls[1].slice(0, 2)).toEqual([4, 41]);
  });
});

describe("the limit is said once", () => {
  it("prefers the unit's limit_text over the language's unit-less words", () => {
    renderWithQueryClient(
      <Card
        reviews={[
          review({
            claims: [
              observation({
                evidence: [
                  evidence({
                    sentence: "highest value of pressure in the decline, at most 6",
                    value: 8.2,
                    unit: "bar",
                    limit_text: "at most 6 bar",
                    held: false,
                  }),
                ],
              }),
            ],
          }),
        ]}
      />,
    );
    const text = screen.getByTestId("claim-evidence").textContent ?? "";
    expect(text).toContain("highest value of pressure in the decline: 8.2 bar, at most 6 bar");
    expect(text.match(/at most 6/g)).toHaveLength(1);
  });
});
