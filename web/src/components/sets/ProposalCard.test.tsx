import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProposalCard } from "@/components/sets/ProposalCard";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { proposal } from "@/test/setsFixtures";

const { acceptSetProposal, declineSetProposal, toastError, toastSuccess } = vi.hoisted(() => ({
  acceptSetProposal: vi.fn(),
  declineSetProposal: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  acceptSetProposal,
  declineSetProposal,
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: vi.fn() },
}));

beforeEach(() => {
  vi.clearAllMocks();
});

/**
 * The card is the one place a person decides, so what it has to get right is
 * the decision: what would change, what it claims, and whether it is still a
 * question at all. The refusals matter as much as the accept — a Set that moved
 * on and a prediction nobody graded are both answered in words the person can
 * act on, and a toast that said "Could not accept" would throw those away.
 */
describe("ProposalCard", () => {
  it("shows the change, the reason and the prediction with what it is against", () => {
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    const card = screen.getByTestId("proposal-card");
    expect(card).toHaveAttribute("data-status", "proposed");
    expect(screen.getByTestId("proposal-changes")).toHaveTextContent("Dose");
    expect(screen.getByTestId("proposal-changes")).toHaveTextContent("18.5 g");
    expect(card).toHaveTextContent("Half a gram more, to carry the finish.");
    const prediction = screen.getByTestId("proposal-prediction");
    expect(prediction).toHaveTextContent("compared to v2");
    expect(prediction).toHaveTextContent("a touch more body");
  });

  it("says nothing is sent to the machine either way", () => {
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);
    expect(screen.getByTestId("proposal-card")).toHaveTextContent(
      "Nothing is sent to the machine either way",
    );
  });

  it("accepts on a press and says what was recorded", async () => {
    const user = setupUser();
    acceptSetProposal.mockResolvedValue({
      proposal: proposal({ status: "accepted", resulting_version_no: 3 }),
      version: { version_no: 3 },
    });
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(acceptSetProposal).toHaveBeenCalledWith(3, 5);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(String(toastSuccess.mock.calls[0][0])).toContain("Version 3 recorded");
  });

  it("asks why before declining, and declines with the note", async () => {
    const user = setupUser();
    declineSetProposal.mockResolvedValue({
      proposal: proposal({ status: "declined" }),
      version: null,
    });
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    // The field exists before it is opened, so `aria-controls` names something
    // a reader can reach; it is hidden until the person says Decline.
    const button = screen.getByRole("button", { name: /^Decline$/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("decline-note")).toHaveAttribute("hidden");
    expect(declineSetProposal).not.toHaveBeenCalled();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("decline-note")).not.toHaveAttribute("hidden");

    await user.type(screen.getByLabelText(/Why not/), "The dose is not the problem.");
    await user.click(screen.getByRole("button", { name: "Decline it" }));

    expect(declineSetProposal).toHaveBeenCalledWith(3, 5, {
      note: "The dose is not the problem.",
    });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Proposal declined"));
  });

  it("declines with no note when there is nothing to add", async () => {
    const user = setupUser();
    declineSetProposal.mockResolvedValue({
      proposal: proposal({ status: "declined" }),
      version: null,
    });
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.click(screen.getByRole("button", { name: "Decline it" }));

    expect(declineSetProposal).toHaveBeenCalledWith(3, 5, { note: "" });
  });

  it("submits the note on Enter, because it is one line", async () => {
    const user = setupUser();
    declineSetProposal.mockResolvedValue({
      proposal: proposal({ status: "declined" }),
      version: null,
    });
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "Tried it last week.{Enter}");

    expect(declineSetProposal).toHaveBeenCalledWith(3, 5, { note: "Tried it last week." });
  });

  it("Escape closes the field and declines nothing", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "half a thought{Escape}");

    expect(declineSetProposal).not.toHaveBeenCalled();
    expect(screen.getByTestId("decline-note")).toHaveAttribute("hidden");
    // And the half-typed thought is gone with it, rather than waiting to be
    // sent with the next decline.
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    expect(screen.getByLabelText(/Why not/)).toHaveValue("");
  });

  it("caps the note where the route does", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));

    expect(screen.getByLabelText(/Why not/)).toHaveAttribute("maxlength", "500");
  });

  it("does not carry a half-typed turn-down onto another proposal", async () => {
    const user = setupUser();
    const { rerender } = renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "about this one");

    rerender(<ProposalCard setId={3} proposal={proposal({ id: 6 })} />);

    expect(screen.getByTestId("decline-note")).toHaveAttribute("hidden");
  });

  it("shows a proposal nobody can read as one, with only a way out", () => {
    renderWithQueryClient(
      <ProposalCard setId={3} proposal={proposal({ readable: false, changes: [], changed: [] })} />,
    );

    expect(screen.getByTestId("proposal-unreadable")).toHaveTextContent("cannot be read");
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Decline$/ })).toBeInTheDocument();
  });

  it("offers nothing to press on one the Set overtook", () => {
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal({ status: "stale" })} />);

    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Decline$/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent("moved on to another version");
  });

  it("toasts the server's own words when the Set has moved on", async () => {
    const user = setupUser();
    acceptSetProposal.mockRejectedValue(
      new Error("Proposal 5 was made against a version that is no longer current"),
    );
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "Proposal 5 was made against a version that is no longer current",
      ),
    );
  });

  it("toasts the server's own words when the prediction is still open", async () => {
    const user = setupUser();
    acceptSetProposal.mockRejectedValue(
      new Error("This version's prediction has not been graded yet"),
    );
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("This version's prediction has not been graded yet"),
    );
  });

  it("warns before the press when the Set has already moved on", () => {
    renderWithQueryClient(
      <ProposalCard setId={3} proposal={proposal({ base_is_current: false })} />,
    );
    expect(screen.getByTestId("proposal-stale")).toHaveTextContent("moved on");
  });

  it("says a combined change cannot be separated by its own prediction", () => {
    renderWithQueryClient(
      <ProposalCard
        setId={3}
        proposal={proposal({
          combined_reason: "A finer grind chokes at the old dose.",
          changed: ["the dose", "the grind"],
        })}
      />,
    );
    expect(screen.getByTestId("proposal-combined")).toHaveTextContent(
      "cannot say which of them did anything",
    );
  });

  it("stops offering the buttons once it has been answered", () => {
    renderWithQueryClient(
      <ProposalCard
        setId={3}
        proposal={proposal({
          status: "accepted",
          resulting_version_no: 3,
          decided_at: "2026-03-02T09:00:00.000Z",
        })}
      />,
    );

    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent("Accepted as v3");
  });

  it("keeps a declined proposal's reason, which is what it is worth", () => {
    renderWithQueryClient(
      <ProposalCard
        setId={3}
        proposal={proposal({ status: "declined", decline_note: "The dose is not the problem." })}
      />,
    );
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent(
      "The dose is not the problem.",
    );
  });

  it("links back to the conversation only where that is somewhere else", () => {
    const { unmount } = renderWithQueryClient(
      <ProposalCard setId={3} proposal={proposal()} showThreadLink />,
    );
    expect(screen.getByTestId("proposal-thread").querySelector("a")).toHaveAttribute(
      "href",
      "/chat?thread=9",
    );
    unmount();

    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);
    expect(screen.queryByTestId("proposal-thread")).not.toBeInTheDocument();
  });
});
