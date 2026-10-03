import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { OutcomeCard, outcomeFailure } from "@/components/chat/OutcomeCard";
import { TellAgentContext } from "@/components/chat/tellAgent";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { outcomeProposal, version, vocabulary } from "@/test/setsFixtures";

const {
  getOutcomeProposals,
  acceptOutcomeProposal,
  changeOutcomeProposal,
  dismissOutcomeProposal,
  getVocabulary,
  toastSuccess,
  toastError,
} = vi.hoisted(() => ({
  getOutcomeProposals: vi.fn(),
  acceptOutcomeProposal: vi.fn(),
  changeOutcomeProposal: vi.fn(),
  dismissOutcomeProposal: vi.fn(),
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
});

function renderCard() {
  return renderWithQueryClient(<OutcomeCard setId={3} proposalId={11} fallback={FALLBACK} />);
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
          <OutcomeCard setId={3} proposalId={11} fallback={FALLBACK} />
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
        "Dismissed: “one shot is not a result” Nothing was recorded",
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
      ["dismissed", { decision_note: "no" }, "Dismissed: “no” Nothing was recorded"],
      ["superseded", {}, "A newer grade replaced this one, so it was never applied."],
    ] as const)("%s says how it ended and offers no buttons", async (status, extra, words) => {
      getOutcomeProposals.mockResolvedValue({
        items: [outcomeProposal({ status, ...extra })],
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
        "This grade was already answered, here in another tab or on the Set page.",
      );
      // Still a card with buttons: the failure is not a state it was left in.
      expect(screen.getByRole("button", { name: /Accept/ })).toBeEnabled();
      expect(toastError).toHaveBeenCalled();
    });

    it("when there is nothing left to grade", () => {
      const error = new ApiClientError("Version 22 has no shot you have formed a view about", {
        status: 409,
        code: "NOTHING_TO_GRADE",
      });
      expect(outcomeFailure(error)).toBe(
        "No shot of this version is labelled Keep or Improve any more, so there is nothing to grade. Label a shot again, or dismiss this grade.",
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
});
