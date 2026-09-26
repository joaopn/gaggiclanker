import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { TellAgentContext } from "@/components/chat/tellAgent";
import { designRecipe, ProposalCard } from "@/components/sets/ProposalCard";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { designProposal, proposal } from "@/test/setsFixtures";

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

  it("tells the agent in the conversation which version the accepted change became", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptSetProposal.mockResolvedValue({
      proposal: proposal({ status: "accepted", resulting_version_no: 3 }),
      version: { version_no: 3 },
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard setId={3} proposal={proposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    // The Set prompt recognises the turn by its first word.
    await waitFor(() =>
      expect(tell).toHaveBeenCalledWith(
        "Accepted: your proposed change is now version 3 of this Set.",
      ),
    );
    expect(tell).toHaveBeenCalledTimes(1);
  });

  it("tells the agent the first recipe is version 1", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptSetProposal.mockResolvedValue({
      proposal: designProposal({ status: "accepted" }),
      version: { version_no: 1 },
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard setId={6} proposal={designProposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() =>
      expect(tell).toHaveBeenCalledWith(
        "Accepted: your first recipe is now version 1 of this Set.",
      ),
    );
  });

  it("tells the agent nothing when the accept is refused or the card is declined", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptSetProposal.mockRejectedValue(
      new ApiClientError("The Set has moved on.", { status: 409, code: "PROPOSAL_STALE" }),
    );
    declineSetProposal.mockResolvedValue({
      proposal: proposal({ status: "declined" }),
      version: null,
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard setId={3} proposal={proposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /Accept/ }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.click(screen.getByRole("button", { name: /Decline it/ }));
    await waitFor(() => expect(declineSetProposal).toHaveBeenCalled());

    expect(tell).not.toHaveBeenCalled();
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

/**
 * A Set's first recipe: the same question as a change, asked about a Set with
 * no recipe yet. What it must get right is what the person is agreeing to — a
 * whole recipe, a profile that is only a draft — and what happens after.
 */
describe("ProposalCard, a first recipe", () => {
  it("renders the recipe, the draft it carries and no prediction", () => {
    renderWithQueryClient(<ProposalCard setId={6} proposal={designProposal()} />);

    const card = screen.getByTestId("proposal-card");
    expect(card).toHaveAttribute("data-kind", "design");
    expect(card).toHaveTextContent("The first recipe is waiting for you");
    const recipe = screen.getByTestId("proposal-recipe");
    expect(recipe).toHaveTextContent("Guji Bloom");
    expect(recipe).toHaveTextContent("94 °C");
    expect(recipe).toHaveTextContent("Grind20");
    expect(recipe).toHaveTextContent("Dose18 g");
    expect(recipe).toHaveTextContent("Yield40 g (1:2.2)");
    expect(screen.getByTestId("proposal-draft-link")).toHaveAttribute("href", "/profiles#staged");
    expect(card).toHaveTextContent("A bloom to open up a light natural");
    // A version 1 is a baseline: nothing to predict against, nothing compared.
    expect(screen.queryByTestId("proposal-prediction")).not.toBeInTheDocument();
    expect(card).not.toHaveTextContent("compared to");
    // Nor drawn as a diff: "not set → 18 g" five times says less than the recipe.
    expect(screen.queryByTestId("proposal-changes")).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-grind-relative")).not.toBeInTheDocument();
  });

  it("says a grind with no number behind it is relative", () => {
    const changes = designProposal().changes.filter((change) => change.field !== "grind_value");
    renderWithQueryClient(
      <ProposalCard
        setId={6}
        proposal={designProposal({
          changes: changes.map((change) =>
            change.field === "grind_setting"
              ? { ...change, after: "two finer than usual" }
              : change,
          ),
        })}
      />,
    );

    expect(screen.getByTestId("proposal-recipe")).toHaveTextContent("two finer than usual");
    expect(screen.getByTestId("proposal-grind-relative")).toHaveTextContent(
      "nothing anchors a number on your dial",
    );
  });

  it("accepts through the accept route and says version 1 is set, with the draft link", async () => {
    const user = setupUser();
    acceptSetProposal.mockResolvedValue({
      proposal: designProposal({
        status: "accepted",
        changes: [],
        resulting_version_id: 60,
        resulting_version_no: 1,
        decided_at: "2026-03-02T09:00:00.000Z",
      }),
      version: { version_no: 1 },
    });
    renderWithQueryClient(<ProposalCard setId={6} proposal={designProposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(acceptSetProposal).toHaveBeenCalledWith(6, 8);
    const decided = await screen.findByTestId("proposal-decided");
    expect(decided).toHaveTextContent("version 1 is set");
    expect(decided).toHaveTextContent("approve and push");
    expect(decided).toHaveTextContent("shots brewed on it are filed here");
    expect(
      within(decided).getByRole("link", { name: /draft on the Profiles page/ }),
    ).toHaveAttribute("href", "/profiles#staged");
    expect(within(decided).getByRole("link", { name: "version 1" })).toHaveAttribute(
      "href",
      "/sets/6",
    );
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(String(toastSuccess.mock.calls[0][0])).toContain("Version 1 is set");
  });

  it("declines with the note, as a change does", async () => {
    const user = setupUser();
    declineSetProposal.mockResolvedValue({
      proposal: designProposal({ status: "declined", decline_note: "too long a ratio" }),
      version: null,
    });
    renderWithQueryClient(<ProposalCard setId={6} proposal={designProposal()} />);

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "too long a ratio{Enter}");

    expect(declineSetProposal).toHaveBeenCalledWith(6, 8, { note: "too long a ratio" });
    expect(await screen.findByTestId("proposal-decided")).toHaveTextContent(
      "Declined: “too long a ratio” Its draft was discarded, unless it had already been pushed.",
    );
  });

  it("says in words that the draft is gone when the accept is refused for it", async () => {
    const user = setupUser();
    acceptSetProposal.mockRejectedValue(
      new ApiClientError("The profile draft proposal 8 carries is no longer open", {
        status: 409,
        code: "PROPOSAL_DRAFT_CLOSED",
      }),
    );
    renderWithQueryClient(<ProposalCard setId={6} proposal={designProposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    const closed = await screen.findByTestId("proposal-draft-closed");
    expect(closed).toHaveTextContent("discarded or replaced");
    expect(closed).toHaveTextContent("Decline this card and ask the agent for a new one.");
    expect(toastError).toHaveBeenCalledWith(
      "The profile draft proposal 8 carries is no longer open",
    );
    // Still a question: declining is the way on, and it is still offered.
    expect(screen.getByRole("button", { name: /^Decline$/ })).toBeInTheDocument();
  });

  it("does not blame the draft for any other refusal", async () => {
    const user = setupUser();
    acceptSetProposal.mockRejectedValue(
      new ApiClientError("Proposal 8 has already been answered", {
        status: 409,
        code: "PROPOSAL_DECIDED",
      }),
    );
    renderWithQueryClient(<ProposalCard setId={6} proposal={designProposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.queryByTestId("proposal-draft-closed")).not.toBeInTheDocument();
  });

  it("says a newer card replaced one that was overtaken", () => {
    renderWithQueryClient(
      <ProposalCard setId={6} proposal={designProposal({ status: "stale" })} />,
    );

    expect(screen.getByTestId("proposal-decided")).toHaveTextContent(
      "A newer card replaced this one",
    );
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Decline$/ })).not.toBeInTheDocument();
  });

  it("keeps saying how it was answered once it has been, and the recipe it set", () => {
    renderWithQueryClient(
      <ProposalCard
        setId={6}
        proposal={designProposal({ status: "accepted", resulting_version_no: 1 })}
      />,
    );

    expect(screen.getByTestId("proposal-card")).toHaveTextContent(
      "A first recipe that was proposed",
    );
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent("version 1 is set");
    // The server still draws an accepted first recipe against the empty one it
    // filled, so scrolling back through the conversation shows what was agreed.
    expect(screen.getByTestId("proposal-recipe")).toBeInTheDocument();
  });

  it("reads the recipe off the diff against the empty version 1", () => {
    expect(designRecipe(designProposal().changes)).toEqual({
      profile: "Guji Bloom",
      temperature: "94 °C",
      grind: "20",
      grindIsAbsolute: true,
      dose: "18 g",
      target: "40 g",
      ratio: "1:2.2",
    });
    expect(designRecipe([])).toEqual({
      profile: null,
      temperature: null,
      grind: null,
      grindIsAbsolute: false,
      dose: null,
      target: null,
      ratio: null,
    });
  });
});

describe("ProposalCard, a change, after this feature", () => {
  it("renders exactly the change card it always did", () => {
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    const card = screen.getByTestId("proposal-card");
    expect(card).toHaveAttribute("data-kind", "change");
    expect(card).toHaveTextContent("A change is waiting for you");
    expect(screen.getByTestId("proposal-changes")).toBeInTheDocument();
    expect(screen.getByTestId("proposal-prediction")).toHaveTextContent("compared to v2");
    expect(card).toHaveTextContent("Accepting records a new version of this Set.");
    expect(screen.queryByTestId("proposal-recipe")).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-draft-link")).not.toBeInTheDocument();
  });

  it("shows what the accept recorded without waiting for the lists to be read again", async () => {
    const user = setupUser();
    acceptSetProposal.mockResolvedValue({
      proposal: proposal({ status: "accepted", resulting_version_no: 3 }),
      version: { version_no: 3 },
    });
    renderWithQueryClient(<ProposalCard setId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(await screen.findByTestId("proposal-decided")).toHaveTextContent("Accepted as v3");
  });
});
