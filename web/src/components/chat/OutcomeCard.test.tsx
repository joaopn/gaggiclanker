import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { OutcomeCard, outcomeFailure } from "@/components/chat/OutcomeCard";
import { TellAgentContext } from "@/components/chat/tellAgent";
import { outcomeProposal, version, vocabulary } from "@/test/recipesFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const {
  getOutcomeProposals,
  acceptOutcomeProposal,
  changeOutcomeProposal,
  dismissOutcomeProposal,
  setVersionOutcome,
  clearVersionOutcome,
  getVocabulary,
  toastSuccess,
  toastError,
} = vi.hoisted(() => ({
  getOutcomeProposals: vi.fn(),
  acceptOutcomeProposal: vi.fn(),
  changeOutcomeProposal: vi.fn(),
  dismissOutcomeProposal: vi.fn(),
  setVersionOutcome: vi.fn(),
  clearVersionOutcome: vi.fn(),
  getVocabulary: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getOutcomeProposals,
  acceptOutcomeProposal,
  changeOutcomeProposal,
  dismissOutcomeProposal,
  setVersionOutcome,
  clearVersionOutcome,
  getVocabulary,
}));
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: vi.fn() },
}));

const FALLBACK = { version: "v2", outcome: "partly_held", countedShots: 3 };

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  getOutcomeProposals.mockResolvedValue({ items: [outcomeProposal()] });
  setVersionOutcome.mockResolvedValue(version());
  clearVersionOutcome.mockResolvedValue(version());
});

function renderCard() {
  return renderWithQueryClient(<OutcomeCard recipeId={3} proposalId={11} fallback={FALLBACK} />);
}

/**
 * The card is the one place the agent's grade becomes the version's outcome, so
 * what it has to get right is the decision: what is proposed and on how much
 * evidence, what each of the three buttons does, and what is left of the card
 * once the grade is no longer a question.
 */
describe("OutcomeCard", () => {
  it("shows the tool's own words until the live grade has been read", () => {
    getOutcomeProposals.mockReturnValue(new Promise(() => undefined));
    renderCard();

    const holder = screen.getByTestId("propose-card-outcome");
    expect(holder).toHaveTextContent("A grade for v2: partly held");
    expect(holder).toHaveTextContent("on 3 counted shots · waiting for you");
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
  });

  it("shows the version, the badge, the shots and the per-claim lines", async () => {
    renderCard();

    const card = await screen.findByTestId("outcome-card");
    expect(getOutcomeProposals).toHaveBeenCalledWith(3);
    expect(card).toHaveAttribute("data-status", "proposed");
    expect(card).toHaveTextContent("A grade for v2 is waiting for you");
    expect(within(card).getByTestId("outcome-card-badge")).toHaveTextContent("Partly held");
    expect(within(card).getByTestId("outcome-card-badge")).toHaveAttribute(
      "data-state",
      "partly_held",
    );
    expect(within(card).getByTestId("outcome-card-shots")).toHaveTextContent("on 3 counted shots");
    expect(within(card).getByTestId("outcome-card-shots")).not.toHaveTextContent("more since");
    expect(within(card).getByTestId("outcome-card-note")).toHaveTextContent(
      "Time held at 31 s against 28 s",
    );
    expect(within(card).getByRole("button", { name: "Accept as Partly held" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Record another outcome" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Dismiss" })).toBeEnabled();
  });

  it("says how many shots have counted since the grade was written", async () => {
    getOutcomeProposals.mockResolvedValue({
      items: [outcomeProposal({ counted_shots: 3, counted_shots_now: 5 })],
    });
    renderCard();

    expect(await screen.findByTestId("outcome-card-shots")).toHaveTextContent(
      "on 3 counted shots · 2 more since",
    );
  });

  it("says when the version's recorded outcome differs, so Accept is never a surprise", async () => {
    getOutcomeProposals.mockResolvedValue({
      items: [outcomeProposal({ version_outcome: "held" })],
    });
    renderCard();

    const differs = await screen.findByTestId("outcome-card-differs");
    expect(differs).toHaveTextContent("v2's outcome is recorded as");
    expect(differs).toHaveTextContent("Held");
    expect(differs).toHaveTextContent("accepting replaces it");
  });

  it("says nothing about a recorded outcome that already agrees", async () => {
    getOutcomeProposals.mockResolvedValue({
      items: [outcomeProposal({ version_outcome: "partly_held" })],
    });
    renderCard();
    await screen.findByTestId("outcome-card");
    expect(screen.queryByTestId("outcome-card-differs")).not.toBeInTheDocument();
  });

  describe("what accepting a version does to a waiting grade", () => {
    it("says accepting the next version records it while the outcome is open", async () => {
      renderCard();
      expect(await screen.findByText(/accepting that records this grade too/)).toBeVisible();
      expect(screen.queryByText(/does not record this grade/)).toBeNull();
    });

    it("says it does not once an outcome is recorded", async () => {
      getOutcomeProposals.mockResolvedValue({
        items: [outcomeProposal({ version_outcome: "inconclusive" })],
      });
      renderCard();
      expect(
        await screen.findByText(/accepting a version does not record this grade/),
      ).toBeVisible();
      expect(screen.queryByText(/accepting that records this grade too/)).toBeNull();
    });
  });

  describe("accepting", () => {
    it("records the grade as written and says what was recorded", async () => {
      const user = setupUser();
      acceptOutcomeProposal.mockResolvedValue({
        proposal: outcomeProposal({ status: "accepted", decided_at: "2026-03-02T09:00:00.000Z" }),
        version: version({ id: 22, outcome: "partly_held" }),
      });
      renderCard();

      await user.click(await screen.findByRole("button", { name: "Accept as Partly held" }));

      expect(acceptOutcomeProposal).toHaveBeenCalledWith(3, 11);
      expect(await screen.findByTestId("outcome-card-decided")).toHaveTextContent(
        "Accepted: v2's outcome is recorded as Partly held.",
      );
      expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    });

    it("sends the agent nothing: it is told at the start of its next turn", async () => {
      const user = setupUser();
      const tell = vi.fn();
      acceptOutcomeProposal.mockResolvedValue({
        proposal: outcomeProposal({ status: "accepted" }),
        version: version({ id: 22 }),
      });
      renderWithQueryClient(
        <TellAgentContext.Provider value={tell}>
          <OutcomeCard recipeId={3} proposalId={11} fallback={FALLBACK} />
        </TellAgentContext.Provider>,
      );

      await user.click(await screen.findByRole("button", { name: /Accept/ }));
      await screen.findByTestId("outcome-card-decided");

      expect(tell).not.toHaveBeenCalled();
    });

    it("sends one request however fast the second click comes", async () => {
      const user = setupUser();
      let resolve: (value: unknown) => void = () => undefined;
      acceptOutcomeProposal.mockReturnValue(
        new Promise((done) => {
          resolve = done;
        }),
      );
      renderCard();
      const button = await screen.findByRole("button", { name: /Accept/ });

      await user.dblClick(button);
      await act(async () => {
        resolve({
          proposal: outcomeProposal({ status: "accepted" }),
          version: version({ id: 22 }),
        });
      });

      expect(acceptOutcomeProposal).toHaveBeenCalledTimes(1);
    });
  });

  describe("recording another outcome", () => {
    it("offers the four choices inline, with the proposed one pointing back at Accept", async () => {
      const user = setupUser();
      renderCard();
      await user.click(await screen.findByRole("button", { name: "Record another outcome" }));

      const choices = screen.getByTestId("outcome-card-choices");
      expect(choices).toBeVisible();
      expect(
        within(choices)
          .getAllByRole("button")
          .map((b) => b.textContent),
      ).toEqual(["Record Held", "Record Partly held", "Record Failed", "Record Inconclusive"]);
      expect(within(choices).getByRole("button", { name: "Record Partly held" })).toBeDisabled();
      // No overlay: nothing is portalled out of the card.
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    });

    it("records the person's choice and says it replaced the agent's", async () => {
      const user = setupUser();
      changeOutcomeProposal.mockResolvedValue({
        proposal: outcomeProposal({ status: "changed", recorded_outcome: "failed" }),
        version: version({ id: 22, outcome: "failed" }),
      });
      renderCard();
      await user.click(await screen.findByRole("button", { name: "Record another outcome" }));

      await user.click(screen.getByRole("button", { name: "Record Failed" }));

      expect(changeOutcomeProposal).toHaveBeenCalledWith(3, 11, { outcome: "failed" });
      expect(await screen.findByTestId("outcome-card-decided")).toHaveTextContent(
        "You recorded Failed instead of Partly held for v2.",
      );
      expect(acceptOutcomeProposal).not.toHaveBeenCalled();
    });
  });

  describe("dismissing", () => {
    it("records nothing and may say why", async () => {
      const user = setupUser();
      dismissOutcomeProposal.mockResolvedValue({
        proposal: outcomeProposal({
          status: "dismissed",
          decision_note: "one shot is not a result",
        }),
        version: null,
      });
      renderCard();
      await user.click(await screen.findByRole("button", { name: "Dismiss" }));

      await user.type(screen.getByLabelText(/Why not/), "one shot is not a result");
      await user.click(screen.getByRole("button", { name: "Dismiss it" }));

      expect(dismissOutcomeProposal).toHaveBeenCalledWith(3, 11, {
        note: "one shot is not a result",
      });
      expect(await screen.findByTestId("outcome-card-decided")).toHaveTextContent(
        "Dismissed: “one shot is not a result”. Nothing was recorded",
      );
    });

    it("dismisses with no reason too", async () => {
      const user = setupUser();
      dismissOutcomeProposal.mockResolvedValue({
        proposal: outcomeProposal({ status: "dismissed" }),
        version: null,
      });
      renderCard();
      await user.click(await screen.findByRole("button", { name: "Dismiss" }));
      await user.click(screen.getByRole("button", { name: "Dismiss it" }));
      expect(dismissOutcomeProposal).toHaveBeenCalledWith(3, 11, { note: "" });
    });

    it("leaves a half-typed reason alone on Escape and dismisses nothing", async () => {
      const user = setupUser();
      renderCard();
      await user.click(await screen.findByRole("button", { name: "Dismiss" }));
      await user.type(screen.getByLabelText(/Why not/), "half");
      await user.keyboard("{Escape}");

      expect(dismissOutcomeProposal).not.toHaveBeenCalled();
      expect(screen.getByTestId("outcome-card-dismiss")).not.toBeVisible();
    });
  });

  describe("a grade that is no longer waiting", () => {
    it.each([
      ["accepted", {}, "Accepted: v2's outcome is recorded as Partly held."],
      [
        "changed",
        { recorded_outcome: "failed" as const },
        "You recorded Failed instead of Partly held for v2.",
      ],
      ["dismissed", { decision_note: "no" }, "Dismissed: “no”. Nothing was recorded"],
      ["superseded", {}, "A newer grade replaced this one, so it was never applied."],
    ] as const)("%s says how it ended and offers no buttons", async (status, extra, words) => {
      // An older card of its version: a plain record.
      getOutcomeProposals.mockResolvedValue({
        items: [outcomeProposal({ status, newest_for_version: false, ...extra })],
      });
      renderCard();

      const decided = await screen.findByTestId("outcome-card-decided");
      expect(decided).toHaveTextContent(words);
      expect(screen.getByTestId("outcome-card")).toHaveAttribute("data-status", status);
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });
  });

  describe("a press that fails says why in words", () => {
    it("when somebody already answered it", async () => {
      const user = setupUser();
      acceptOutcomeProposal.mockRejectedValue(
        new ApiClientError("Outcome proposal 11 has already been answered", {
          status: 409,
          code: "OUTCOME_PROPOSAL_DECIDED",
        }),
      );
      renderCard();

      await user.click(await screen.findByRole("button", { name: /Accept/ }));

      expect(await screen.findByTestId("outcome-card-error")).toHaveTextContent(
        "This grade was already answered, here in another tab or on the Recipe page.",
      );
      // Still a card with buttons: the failure is not a state it was left in.
      expect(screen.getByRole("button", { name: /Accept/ })).toBeEnabled();
      expect(toastError).toHaveBeenCalled();
    });

    it("when there is nothing left to grade", () => {
      const error = new ApiClientError("Version 22 has no Good or Bad shot yet", {
        status: 409,
        code: "NOTHING_TO_GRADE",
      });
      expect(outcomeFailure(error)).toBe(
        "No shot of this version is rated any more (a discarded one does not count), so there is nothing to grade. Rate a shot, or dismiss this grade.",
      );
    });

    it("with the server's own words for anything else", async () => {
      const user = setupUser();
      acceptOutcomeProposal.mockRejectedValue(new Error("the box fell over"));
      renderCard();
      await user.click(await screen.findByRole("button", { name: /Accept/ }));
      await waitFor(() =>
        expect(screen.getByTestId("outcome-card-error")).toHaveTextContent("the box fell over"),
      );
    });
  });

  /**
   * The newest answered card of a version is where the person finds the version's outcome as it
   * is now, and can change it. The proposal routes are never used for that: the strip writes
   * through the Recipe page's own route.
   */
  describe("the newest answered card of its version", () => {
    function decided(over: Parameters<typeof outcomeProposal>[0] = {}) {
      getOutcomeProposals.mockResolvedValue({
        items: [
          outcomeProposal({
            status: "accepted",
            version_outcome: "partly_held",
            version_outcome_counted_shots: 3,
            counted_shots_now: 3,
            ...over,
          }),
        ],
      });
    }

    it("offers Change the outcome when one is recorded and Record the outcome when open", async () => {
      decided();
      renderCard();
      expect(await screen.findByRole("button", { name: "Change the outcome" })).toBeVisible();
      // The strip is there but closed until it is asked for.
      expect(screen.getByTestId("outcome-card-strip")).not.toBeVisible();
    });

    it("says Record the outcome after the outcome was taken back", async () => {
      decided({ version_outcome: null, version_outcome_counted_shots: null });
      renderCard();
      expect(await screen.findByRole("button", { name: "Record the outcome" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "Change the outcome" })).toBeNull();
    });

    it("shows nothing of this on an older card, with the same data that shows it on the newest", async () => {
      const data = {
        status: "accepted" as const,
        version_outcome: "held" as const,
        version_outcome_counted_shots: 3,
        counted_shots_now: 5,
      };
      // Half one: on the newest card the same row says all three things.
      decided(data);
      const first = renderCard();
      expect(await screen.findByTestId("outcome-card-now-differs")).toBeInTheDocument();
      expect(screen.getByTestId("outcome-card-since")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Change the outcome" })).toBeInTheDocument();
      first.unmount();

      // Half two: on an older card it is a plain record.
      decided({ ...data, newest_for_version: false });
      renderCard();
      await screen.findByTestId("outcome-card-decided");
      expect(screen.queryByTestId("outcome-card-now")).toBeNull();
      expect(screen.queryByRole("button")).toBeNull();
    });

    it("reads what the version says now after the card was answered here and the outcome moved", async () => {
      const user = setupUser();
      getOutcomeProposals.mockResolvedValueOnce({ items: [outcomeProposal()] });
      acceptOutcomeProposal.mockResolvedValue({
        proposal: outcomeProposal({
          status: "accepted",
          version_outcome: "partly_held",
          version_outcome_counted_shots: 3,
        }),
        version: version({ id: 22, outcome: "partly_held" }),
      });
      // The list read after the press: somebody has since recorded Held on the Recipe page.
      decided({ version_outcome: "held" });
      renderCard();

      await user.click(await screen.findByRole("button", { name: /Accept/ }));

      expect(await screen.findByTestId("outcome-card-now-differs")).toHaveTextContent(
        "v2's outcome is now recorded as Held.",
      );
    });

    describe("the version's outcome now", () => {
      it("says it when it differs from what this card recorded", async () => {
        decided({ version_outcome: "held" });
        renderCard();
        expect(await screen.findByTestId("outcome-card-now-differs")).toHaveTextContent(
          "v2's outcome is now recorded as Held.",
        );
      });

      it("says it for a card the person changed, against what they recorded", async () => {
        decided({ status: "changed", recorded_outcome: "failed", version_outcome: "failed" });
        renderCard();
        await screen.findByTestId("outcome-card-decided");
        expect(screen.queryByTestId("outcome-card-now-differs")).toBeNull();
      });

      it("says it is open again after a clear", async () => {
        decided({ version_outcome: null, version_outcome_counted_shots: null });
        renderCard();
        expect(await screen.findByTestId("outcome-card-now-differs")).toHaveTextContent(
          "v2's outcome is open again.",
        );
      });

      it("says nothing while it is what this card recorded", async () => {
        decided({ version_outcome: "partly_held" });
        renderCard();
        await screen.findByTestId("outcome-card-decided");
        expect(screen.queryByTestId("outcome-card-now-differs")).toBeNull();
      });

      it("says nothing on a dismissed card, which recorded nothing", async () => {
        decided({ status: "dismissed", version_outcome: "held" });
        renderCard();
        await screen.findByTestId("outcome-card-decided");
        expect(screen.queryByTestId("outcome-card-now-differs")).toBeNull();
      });
    });

    describe("more counted shots since", () => {
      it.each(["accepted", "changed", "dismissed"] as const)(
        "anchors the count to the recorded outcome on a %s card",
        async (status) => {
          decided({ status, recorded_outcome: "partly_held", counted_shots_now: 6 });
          renderCard();
          expect(await screen.findByTestId("outcome-card-since")).toHaveTextContent(
            "3 more counted shots since the outcome was recorded on 3.",
          );
        },
      );

      it("says how many were counted after the recorded outcome", async () => {
        decided({ counted_shots_now: 5 });
        renderCard();
        expect(await screen.findByTestId("outcome-card-since")).toHaveTextContent(
          "2 more counted shots since the outcome was recorded on 3.",
        );
      });

      it("says one in the singular", async () => {
        decided({ counted_shots_now: 4 });
        renderCard();
        expect(await screen.findByTestId("outcome-card-since")).toHaveTextContent(
          "1 more counted shot since the outcome was recorded on 3.",
        );
      });

      it("says nothing when none were counted since, or when fewer are counted now", async () => {
        decided({ counted_shots_now: 3 });
        const first = renderCard();
        await screen.findByTestId("outcome-card-decided");
        expect(screen.queryByTestId("outcome-card-since")).toBeNull();
        first.unmount();

        decided({ counted_shots_now: 2 });
        renderCard();
        await screen.findByTestId("outcome-card-decided");
        expect(screen.queryByTestId("outcome-card-since")).toBeNull();
      });

      it("says nothing when the count the outcome rests on is not known", async () => {
        // Half one: with the count known the same row says it.
        decided({ counted_shots_now: 5 });
        const first = renderCard();
        expect(await screen.findByTestId("outcome-card-since")).toBeInTheDocument();
        first.unmount();

        decided({ counted_shots_now: 5, version_outcome_counted_shots: null });
        renderCard();
        await screen.findByTestId("outcome-card-decided");
        expect(screen.queryByTestId("outcome-card-since")).toBeNull();
      });
    });

    describe("the gate", () => {
      it("follows the version's prediction, like the Recipe page", async () => {
        // Half one: with a prediction the outcome can be changed.
        decided();
        const first = renderCard();
        expect(await screen.findByRole("button", { name: "Change the outcome" })).toBeVisible();
        first.unmount();

        // Half two: with none it can only be cleared, and the reason is said.
        decided({ version_has_prediction: false });
        const user = setupUser();
        renderCard();
        await user.click(await screen.findByRole("button", { name: "Clear the outcome" }));
        expect(screen.queryByRole("button", { name: "Change the outcome" })).toBeNull();
        expect(screen.getByTestId("outcome-clear-only")).toHaveTextContent("states no prediction");
      });

      it("offers nothing to record when no shot counts and nothing is recorded, and says why", async () => {
        decided({
          version_outcome: null,
          version_outcome_counted_shots: null,
          counted_shots_now: 0,
        });
        renderCard();
        expect(await screen.findByTestId("outcome-card-reason")).toHaveTextContent(
          "No rated shot yet",
        );
        expect(screen.queryByRole("button", { name: /outcome/ })).toBeNull();
      });

      it("still lets a recorded outcome be cleared once no shot counts, and not changed", async () => {
        const user = setupUser();
        decided({ counted_shots_now: 0 });
        renderCard();
        await user.click(await screen.findByRole("button", { name: "Clear the outcome" }));
        expect(screen.getByTestId("outcome-clear-only")).toHaveTextContent(
          "can still be taken back",
        );
        expect(screen.queryByRole("button", { name: "Save the outcome" })).toBeNull();
      });
    });

    describe("the strip", () => {
      it("records another outcome through the Recipe page's route and no proposal route", async () => {
        const user = setupUser();
        decided({ version_outcome: "partly_held", version_outcome_note: "A reason." });
        renderCard();

        await user.click(await screen.findByRole("button", { name: "Change the outcome" }));
        const strip = screen.getByTestId("outcome-card-strip");
        expect(strip).toBeVisible();
        // The four choices and the Why box, seeded from the version's outcome as it is now.
        await user.click(await within(strip).findByRole("button", { name: "Held" }));
        expect(within(strip).getByLabelText("Why")).toHaveValue("A reason.");
        await user.clear(within(strip).getByLabelText("Why"));
        await user.type(within(strip).getByLabelText("Why"), "Better on the next shots.");
        await user.click(within(strip).getByRole("button", { name: "Save the outcome" }));

        await waitFor(() =>
          expect(setVersionOutcome).toHaveBeenCalledWith(3, 22, {
            outcome: "held",
            note: "Better on the next shots.",
          }),
        );
        expect(acceptOutcomeProposal).not.toHaveBeenCalled();
        expect(changeOutcomeProposal).not.toHaveBeenCalled();
        expect(dismissOutcomeProposal).not.toHaveBeenCalled();
        // The write closes the strip, and the lists are read again.
        await waitFor(() => expect(screen.getByTestId("outcome-card-strip")).not.toBeVisible());
        await waitFor(() => expect(getOutcomeProposals.mock.calls.length).toBeGreaterThan(1));
      });

      it("clears the outcome through the Recipe page's route", async () => {
        const user = setupUser();
        decided();
        renderCard();

        await user.click(await screen.findByRole("button", { name: "Change the outcome" }));
        await user.click(
          within(screen.getByTestId("outcome-card-strip")).getByRole("button", { name: "Clear" }),
        );

        await waitFor(() => expect(clearVersionOutcome).toHaveBeenCalledWith(3, 22));
        expect(setVersionOutcome).not.toHaveBeenCalled();
        expect(dismissOutcomeProposal).not.toHaveBeenCalled();
      });

      it("offers no Clear while the outcome is open, and cancel closes it", async () => {
        const user = setupUser();
        decided({ version_outcome: null, version_outcome_counted_shots: null });
        renderCard();

        await user.click(await screen.findByRole("button", { name: "Record the outcome" }));
        const strip = screen.getByTestId("outcome-card-strip");
        expect(within(strip).queryByRole("button", { name: "Clear" })).toBeNull();
        await user.click(within(strip).getByRole("button", { name: "Cancel" }));
        expect(strip).not.toBeVisible();
      });
    });
  });
});
