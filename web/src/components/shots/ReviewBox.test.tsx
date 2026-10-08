import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReviewBlock, ReviewClaim, ShotReview } from "@/api/types";
import { ReviewBadge } from "@/components/shots/ReviewBadge";
import { REVIEW_EXPLAINED, ReviewBox } from "@/components/shots/ReviewBox";
import { type ClaimSpanControls, useClaimSpan } from "@/lib/claimSpan";
import { queryKeys } from "@/lib/queryKeys";
import { claim, evidence, reviewBlock } from "@/test/claimFixtures";
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

const { runReview, answerReviewClaim, getVocabulary } = vi.hoisted(() => ({
  runReview: vi.fn(),
  answerReviewClaim: vi.fn(),
  getVocabulary: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  runReview,
  answerReviewClaim,
  getVocabulary,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  runReview.mockResolvedValue(review({ status: "running", finished_at: null }));
  answerReviewClaim.mockResolvedValue(review());
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
}: {
  reviews: ShotReview[];
  reading?: ReviewBlock;
  span?: ClaimSpanControls;
}) {
  const latest = reviews[0];
  const inForce = reviews.find((r) => r.status === "ok");
  const block =
    reading ??
    reviewBlock({
      state: latest === undefined ? "unreviewed" : latest.status === "ok" ? "reviewed" : "failed",
      review_id: latest?.id,
      in_force_id: inForce?.id,
    });
  return <ReviewBox shotId={129} reviews={reviews} review={block} span={span} />;
}

const observation = (overrides: Partial<ReviewClaim> = {}) => claim({ review_id: 1, ...overrides });

describe("ReviewBox", () => {
  it("before any review: the button and one line saying what it does", async () => {
    const user = setupUser();
    renderWithQueryClient(<Card reviews={[]} />);

    const empty = screen.getByTestId("review-empty");
    expect(empty).toHaveTextContent(REVIEW_EXPLAINED);
    expect(REVIEW_EXPLAINED).toContain("without your judgement");
    expect(REVIEW_EXPLAINED).toContain("changes nothing else.");
    expect(REVIEW_EXPLAINED).toMatch(/kept unless you reject it/);
    expect(REVIEW_EXPLAINED).not.toMatch(/taste prediction|blind/i);

    await user.dblClick(within(empty).getByRole("button", { name: "Review this shot" }));

    // A double click is one request.
    await waitFor(() => expect(runReview).toHaveBeenCalledTimes(1));
    expect(runReview).toHaveBeenCalledWith(129, { model: undefined });
    expect(screen.queryByTestId("review-reviewed")).toBeNull();
  });

  it("says a discarded shot is not reviewed, with no button", () => {
    renderWithQueryClient(<Card reviews={[]} reading={reviewBlock({ state: "not_reviewable" })} />);
    expect(screen.getByTestId("review-unreviewable")).toHaveTextContent("not reviewed");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("while running: says so and holds the button", () => {
    renderWithQueryClient(
      <Card
        reviews={[review({ id: 2, status: "running", finished_at: null, summary: null })]}
        reading={reviewBlock({ state: "running", review_id: 2 })}
      />,
    );

    expect(screen.getByTestId("review-running")).toHaveTextContent("Reviewing this shot since");
    expect(screen.queryByTestId("review-empty")).toBeNull();
  });

  it("while a re-review runs, the earlier review stays below it and its Review again is held", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <Card
        reviews={[
          review({ id: 3, status: "running", finished_at: null, summary: null }),
          review({
            id: 2,
            summary: "The review in force.",
            claims: [observation({ review_id: 2 })],
          }),
        ]}
        reading={reviewBlock({ state: "running", review_id: 3, in_force_id: 2 })}
      />,
    );

    expect(screen.getByTestId("review-running")).toHaveTextContent("earlier review stays below");
    expect(screen.getByTestId("review-summary")).toHaveTextContent("The review in force.");
    const button = screen.getByTestId("run-review");
    expect(button).toHaveAttribute("aria-disabled", "true");
    await user.click(button);
    expect(runReview).not.toHaveBeenCalled();
  });

  it("answers a claim through the review in force, never the newest attempt", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <Card
        reviews={[
          review({ id: 9, status: "running", finished_at: null, summary: null }),
          review({ id: 4, claims: [observation({ id: 40, review_id: 4 })] }),
        ]}
        reading={reviewBlock({ state: "running", review_id: 9, in_force_id: 4 })}
      />,
    );

    await user.click(screen.getByTestId("claim-reject"));

    await waitFor(() =>
      expect(answerReviewClaim).toHaveBeenCalledWith(4, 40, { status: "rejected" }),
    );
  });

  describe("a finished review", () => {
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

    it("shows the summary, the expectations, the prediction, the claims, then the provenance and Review again, in that order", () => {
      renderWithQueryClient(
        <Card
          reviews={[full()]}
          reading={reviewBlock({
            state: "reviewed",
            verdict: "entries",
            review_id: 1,
            in_force_id: 1,
          })}
        />,
      );

      const order = [
        screen.getByTestId("review-summary"),
        screen.getByTestId("review-expectations"),
        screen.getByTestId("review-prediction"),
        screen.getByTestId("review-claims"),
        screen.getByTestId("review-rules"),
        screen.getByTestId("review-excerpts"),
        screen.getByTestId("review-provenance"),
        screen.getByTestId("review-again-ask"),
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
        <ReviewBox
          shotId={129}
          reviews={[full()]}
          review={reviewBlock({ state: "reviewed", review_id: 1, in_force_id: 1 })}
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
      const item = within(screen.getByTestId("review-expectations")).getByTestId("claim");
      expect(within(item).getByTestId("claim-result")).toHaveTextContent("failed");
      expect(item).toHaveClass("border-status-bad/40");
      expect(within(item).getByTestId("claim-expectation")).toHaveTextContent(
        "pressure and flow fall together through the decline",
      );
    });

    it("shows the prediction stance from the start: a stance that reaches the chat is never hidden", () => {
      renderWithQueryClient(<Card reviews={[full()]} />);
      expect(screen.queryByTestId("prediction-show")).toBeNull();
      expect(screen.queryByTestId("prediction-hidden")).toBeNull();
      expect(screen.getByTestId("claim-stance")).toHaveTextContent("partly as predicted");
      expect(screen.getByText("The shot was sweeter than predicted.")).toBeInTheDocument();
    });

    it("keeps every claim from the start: no Confirm, no Confirm all, no waiting state", () => {
      renderWithQueryClient(<Card reviews={[full()]} />);
      expect(screen.queryByTestId("claim-confirm")).toBeNull();
      expect(screen.queryByTestId("claims-confirm-all")).toBeNull();
      expect(screen.queryByTestId("review-verdict-waiting")).toBeNull();
      for (const item of screen.getAllByTestId("claim")) {
        expect(item).toHaveAttribute("data-status", "confirmed");
        expect(within(item).queryByTestId("claim-status")).toBeNull();
        expect(within(item).getByTestId("claim-reject")).toHaveTextContent("Reject");
      }
    });

    it("rejects one claim with one call, a double click making one request", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[full()]} />);

      const item = screen
        .getAllByTestId("claim")
        .find((el) => el.getAttribute("data-claim-id") === "20");
      if (!item) throw new Error("no claim");
      await user.dblClick(within(item).getByTestId("claim-reject"));

      await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(1));
      expect(answerReviewClaim).toHaveBeenCalledWith(1, 20, { status: "rejected" });
    });

    it('keeps a rejected claim out of the lists behind "N rejected · show", each with Restore', async () => {
      const user = setupUser();
      renderWithQueryClient(
        <Card
          reviews={[
            review({
              claims: [
                observation({ id: 30, position: 0 }),
                observation({ id: 31, position: 1, status: "rejected" }),
                observation({ id: 32, position: 2, status: "rejected" }),
              ],
            }),
          ]}
        />,
      );
      // The list shows what was kept; the rejected ones are one line, folded.
      const list = within(screen.getByTestId("review-claims"));
      expect(list.getAllByTestId("claim")).toHaveLength(1);
      const toggle = screen.getByTestId("review-rejected-toggle");
      expect(toggle).toHaveTextContent("2 rejected · show");
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      const folded = screen.getByTestId("review-rejected");
      expect(within(folded).getAllByTestId("claim")).toHaveLength(2);

      await user.click(toggle);
      expect(toggle).toHaveTextContent("2 rejected · hide");
      const rejected = within(folded).getAllByTestId("claim");
      expect(within(rejected[0]).getByTestId("claim-status")).toHaveTextContent("rejected");
      expect(within(rejected[0]).queryByTestId("claim-reject")).toBeNull();

      await user.click(within(rejected[0]).getByRole("button", { name: "Restore" }));
      await waitFor(() =>
        expect(answerReviewClaim).toHaveBeenCalledWith(1, 31, { status: "confirmed" }),
      );
      await user.click(
        within(list.getAllByTestId("claim")[0]).getByRole("button", { name: "Reject" }),
      );
      await waitFor(() =>
        expect(answerReviewClaim).toHaveBeenCalledWith(1, 30, { status: "rejected" }),
      );
      expect(answerReviewClaim).toHaveBeenCalledTimes(2);
    });

    it("has no rejected line when nothing is rejected", () => {
      renderWithQueryClient(<Card reviews={[review({ claims: [observation()] })]} />);
      expect(screen.queryByTestId("review-rejected")).toBeNull();
    });

    it("Review again asks once, inline, that it replaces the current review", async () => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[review({ claims: [found] })]} />);
      const ask = screen.getByTestId("review-again-ask");
      expect(ask).toHaveAttribute("hidden");

      await user.click(screen.getByTestId("run-review"));
      expect(runReview).not.toHaveBeenCalled();
      expect(ask).not.toHaveAttribute("hidden");
      expect(ask).toHaveTextContent("This replaces the current review");
      expect(screen.getByTestId("run-review")).toHaveAttribute("aria-controls", ask.id);

      await user.click(screen.getByTestId("review-again-cancel"));
      expect(ask).toHaveAttribute("hidden");
      expect(runReview).not.toHaveBeenCalled();

      await user.click(screen.getByTestId("run-review"));
      await user.dblClick(screen.getByTestId("review-again-confirm"));
      await waitFor(() => expect(runReview).toHaveBeenCalledTimes(1));
      expect(runReview).toHaveBeenCalledWith(129, { model: undefined });
    });

    it("renders the review in force, not an older one", () => {
      renderWithQueryClient(
        <Card
          reviews={[
            review({ id: 3, summary: "The newest review." }),
            review({ id: 2, summary: "An older review." }),
          ]}
        />,
      );
      expect(screen.getByTestId("review-summary")).toHaveTextContent("The newest review.");
      expect(screen.queryByText("An older review.")).toBeNull();
    });
  });

  it("failed: shows the stored error and the button, and the last reading below it", () => {
    renderWithQueryClient(
      <Card
        reviews={[
          review({ id: 4, status: "failed", error: "auth: invalid api key", summary: null }),
          review({ id: 3, summary: "The last good review." }),
        ]}
        reading={reviewBlock({ state: "failed", review_id: 4, in_force_id: 3, reason: "auth" })}
      />,
    );

    expect(screen.getByTestId("review-failed")).toHaveTextContent("That review did not complete");
    expect(screen.getByTestId("review-error")).toHaveTextContent("auth: invalid api key");
    expect(screen.getByRole("button", { name: "Review again" })).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByTestId("review-summary")).toHaveTextContent("The last good review.");
  });

  it("an interrupted review says the process stopped", () => {
    renderWithQueryClient(
      <Card reviews={[review({ status: "interrupted", error: "stopped", summary: null })]} />,
    );
    expect(screen.getByTestId("review-failed")).toHaveTextContent("Interrupted");
    expect(screen.queryByTestId("review-reviewed")).toBeNull();
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

describe("the verdict line, the box's first", () => {
  const faults = leverSignedFields.checks.entries.slice(0, 2);
  const cases: Array<[string, ReviewBlock]> = [
    [
      "entries",
      reviewBlock({
        state: "reviewed",
        verdict: "entries",
        badge: "ramp: early yield +1",
        entries: faults,
        review_id: 1,
        in_force_id: 1,
      }),
    ],
    [
      "as intended",
      reviewBlock({
        state: "reviewed",
        verdict: "as_intended",
        badge: "As intended",
        review_id: 1,
        in_force_id: 1,
      }),
    ],
    [
      "no faults",
      reviewBlock({
        state: "reviewed",
        verdict: "no_faults",
        badge: "No faults",
        review_id: 1,
        in_force_id: 1,
      }),
    ],
    [
      "running",
      reviewBlock({ state: "running", badge: "Reviewing…", review_id: 2, in_force_id: 1 }),
    ],
    [
      "failed",
      reviewBlock({
        state: "failed",
        badge: "Failed to run",
        review_id: 2,
        in_force_id: 1,
        reason: "timed out",
      }),
    ],
  ];

  it.each(cases)("says what the Review badge says, in its tone: %s", (_name, block) => {
    const reviews = [review({ id: 1, claims: [observation()] })];
    const { container } = renderWithQueryClient(
      <>
        <ReviewBadge review={block} />
        <ReviewBox shotId={129} reviews={reviews} review={block} span={NO_SPAN} />
      </>,
    );
    const badge = container.querySelector('[data-testid="review-badge"]');
    const line = screen.getByTestId("review-verdict-badge");
    expect(line).toHaveTextContent((badge?.textContent ?? "").trim());
    expect(line.getAttribute("data-tone")).toBe(badge?.getAttribute("data-tone"));
  });

  it("is the first thing in a review, and the model's summary comes after it, labelled", () => {
    renderWithQueryClient(
      <ReviewBox
        shotId={129}
        reviews={[review({ id: 1, summary: "A clean lever shot." })]}
        review={reviewBlock({
          state: "reviewed",
          verdict: "no_faults",
          badge: "No faults",
          in_force_id: 1,
        })}
        span={NO_SPAN}
      />,
    );
    const reviewed = screen.getByTestId("review-reviewed");
    expect(reviewed.firstElementChild).toBe(screen.getByTestId("review-verdict"));
    expect(screen.getByTestId("review-verdict-why")).toHaveTextContent(
      "the model found no fault; the profile has no signature in force to hold the shot to",
    );
    const summary = screen.getByTestId("review-summary-block");
    expect(summary).toHaveTextContent("The model's summary: A clean lever shot.");
    expect(
      screen.getByTestId("review-verdict").compareDocumentPosition(summary) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("lists the faults of an entries verdict, and never says a claim waits", () => {
    renderWithQueryClient(
      <ReviewBox
        shotId={129}
        reviews={[review({ id: 1, claims: [observation()] })]}
        review={cases[0][1]}
        span={NO_SPAN}
      />,
    );
    expect(screen.getByTestId("review-verdict-entries").querySelectorAll("li")).toHaveLength(
      faults.length,
    );
    expect(screen.queryByTestId("review-verdict-waiting")).toBeNull();
  });

  it("says As intended with its reason", () => {
    renderWithQueryClient(
      <ReviewBox shotId={129} reviews={[review({ id: 1 })]} review={cases[1][1]} span={NO_SPAN} />,
    );
    expect(screen.getByTestId("review-verdict-badge")).toHaveTextContent("As intended");
    expect(screen.getByTestId("review-verdict-why")).toHaveTextContent("signature in force");
  });
});

describe("answering goes through the review in force during a re-read", () => {
  const inForce = review({
    id: 4,
    claims: [
      observation({ id: 40, review_id: 4 }),
      observation({ id: 41, review_id: 4, position: 1 }),
    ],
  });
  const states: Array<[string, ShotReview, ReviewBlock]> = [
    [
      "a running re-read",
      review({ id: 9, status: "running", finished_at: null, summary: null }),
      reviewBlock({ state: "running", review_id: 9, in_force_id: 4 }),
    ],
    [
      "a failed re-read",
      review({ id: 9, status: "failed", error: "boom", summary: null }),
      reviewBlock({ state: "failed", review_id: 9, in_force_id: 4 }),
    ],
  ];

  it.each(states)(
    "a Reject goes to the review in force, never the attempt: %s",
    async (_name, newest, reading) => {
      const user = setupUser();
      renderWithQueryClient(<Card reviews={[newest, inForce]} reading={reading} />);
      const [first, second] = screen.getAllByTestId("claim");
      await user.click(within(first).getByTestId("claim-reject"));
      await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(1));
      // Never the newest attempt's id (9), which answers 409.
      expect(answerReviewClaim.mock.calls[0].slice(0, 2)).toEqual([4, 40]);
      await user.click(within(second).getByTestId("claim-reject"));
      await waitFor(() => expect(answerReviewClaim).toHaveBeenCalledTimes(2));
      expect(answerReviewClaim.mock.calls[1].slice(0, 2)).toEqual([4, 41]);
    },
  );
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
