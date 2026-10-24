import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { TellAgentContext } from "@/components/chat/tellAgent";
import {
  acceptHint,
  designVersionFields,
  handSteps,
  ProposalCard,
} from "@/components/recipes/ProposalCard";
import { SyncOwnerProvider } from "@/components/sync/SyncOwner";
import { draft, draftProfile, proProfile, standing } from "@/test/draftFixtures";
import { designProposal, outcomeProposal, proposal } from "@/test/recipesFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const {
  acceptRecipeProposal,
  declineRecipeProposal,
  getDraftStanding,
  checkDraftName,
  runSync,
  getSyncStatus,
  getDeviceStatus,
  toastError,
  toastSuccess,
} = vi.hoisted(() => ({
  acceptRecipeProposal: vi.fn(),
  declineRecipeProposal: vi.fn(),
  getDraftStanding: vi.fn(),
  checkDraftName: vi.fn(),
  runSync: vi.fn(),
  getSyncStatus: vi.fn(),
  getDeviceStatus: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  acceptRecipeProposal,
  declineRecipeProposal,
  getDraftStanding,
  checkDraftName,
  runSync,
  getSyncStatus,
  getDeviceStatus,
}));

// Canvas is opaque to jsdom: the chart is a figure that says what it was asked to draw.
vi.mock("@/components/charts/ProfileCurveChart", () => ({
  ProfileCurveChart: ({ title }: { title?: string }) => (
    <figure data-testid="profile-curve" data-title={title ?? ""} />
  ),
}));

/** The first recipe's profile as the standing read serves it while the card waits. */
function designStanding(overrides: Record<string, unknown> = {}, newProfile = false) {
  return standing({
    draft: draft({ id: 14, draft_label: "Guji Bloom", is_new: newProfile }),
    profile: draftProfile({ label: "Guji Bloom" }),
    active_profile: newProfile ? null : draftProfile(),
    landing: {
      draft_id: 14,
      already_on_board_label: null,
      plain: { row_id: newProfile ? null : 4, row_label: newProfile ? null : "Guji Bloom" },
      for_recipe: null,
    },
    ...overrides,
  });
}

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: vi.fn() },
}));

beforeEach(() => {
  vi.clearAllMocks();
  // A first recipe's draft continues a profile unless a test says it is a new one.
  getDraftStanding.mockResolvedValue(designStanding());
  checkDraftName.mockImplementation(async (_id: number, label: string) => ({
    label: label.trim(),
    refused: null,
  }));
  runSync.mockResolvedValue({ queued: ["shots", "profiles"] });
  getSyncStatus.mockResolvedValue({
    configured: true,
    connected: true,
    running: false,
    last_runs: {},
    last_error: null,
    counts: {
      total: 0,
      quarantined: 0,
      deleted_on_device: 0,
      incomplete: 0,
      samples: 0,
      needs_recipe: 0,
    },
    recent_events: [],
  });
  getDeviceStatus.mockResolvedValue({
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: null,
    last_status: null,
  });
});

/** The card with the app's sync owner above it, as AppShell mounts it. */
function renderCard(ui: React.ReactElement) {
  return renderWithQueryClient(<SyncOwnerProvider>{ui}</SyncOwnerProvider>);
}

/**
 * The card is the one place a person decides, so what it has to get right is
 * the decision: what would change, what it claims, and whether it is still a
 * question at all. The refusals matter as much as the accept — a Recipe that moved
 * on and a prediction nobody graded are both answered in words the person can
 * act on, and a toast that said "Could not accept" would throw those away.
 */
describe("ProposalCard", () => {
  it("shows the change, the reason and the prediction with what it is against", () => {
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    const card = screen.getByTestId("proposal-card");
    expect(card).toHaveAttribute("data-status", "proposed");
    expect(card).toHaveAttribute("data-kind", "change");
    // A change card has neither a first Recipe's details nor a link to a profile draft.
    expect(screen.queryByTestId("proposal-recipe")).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-draft-link")).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-changes")).toHaveTextContent("Dose");
    expect(screen.getByTestId("proposal-changes")).toHaveTextContent("18.5 g");
    expect(card).toHaveTextContent("Half a gram more, to carry the finish.");
    const prediction = screen.getByTestId("proposal-prediction");
    expect(prediction).toHaveTextContent("compared to v2");
    expect(prediction).toHaveTextContent("a touch more body");
  });

  it("renders the agent's reason as inline markdown, with citations linked", () => {
    renderWithQueryClient(
      <ProposalCard
        recipeId={3}
        proposal={proposal({
          reason: "**Finer** by one, as in `shot 4`. See shot 6.\n\n# Big\n- one",
        })}
      />,
    );

    const card = screen.getByTestId("proposal-card");
    expect(within(card).getByText("Finer").tagName).toBe("STRONG");
    expect(within(card).getByRole("link", { name: "shot 4" })).toHaveAttribute("href", "/shots/4");
    expect(within(card).getByRole("link", { name: "shot 6" })).toHaveAttribute("href", "/shots/6");
    const reason = within(card).getByText("Finer").closest("p") as HTMLElement;
    expect(within(reason).queryByRole("heading")).not.toBeInTheDocument();
    expect(within(reason).queryByRole("list")).not.toBeInTheDocument();
  });

  describe("when the agent's grade of the version it was made against is waiting", () => {
    it("says accepting also records that outcome, as the same badge the Recipe page uses", () => {
      renderWithQueryClient(
        <ProposalCard
          recipeId={3}
          proposal={proposal({ records_outcome: outcomeProposal({ outcome: "partly_held" }) })}
        />,
      );

      const line = screen.getByTestId("proposal-records-outcome");
      expect(line).toHaveTextContent("Accepting also records v2's outcome:");
      expect(within(line).getByText("Partly held")).toHaveAttribute("data-state", "partly_held");
      expect(line).toHaveTextContent("as the agent graded it on 3 counted shots");
    });

    it("says nothing when no grade is waiting", () => {
      renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);
      expect(screen.queryByTestId("proposal-records-outcome")).not.toBeInTheDocument();
    });

    it("says nothing once the change has been answered", () => {
      renderWithQueryClient(
        <ProposalCard
          recipeId={3}
          proposal={proposal({
            status: "accepted",
            resulting_version_label: "v2.1",
            records_outcome: outcomeProposal(),
          })}
        />,
      );
      expect(screen.queryByTestId("proposal-records-outcome")).not.toBeInTheDocument();
    });

    it("still accepts with one press", async () => {
      const user = setupUser();
      acceptRecipeProposal.mockResolvedValue({
        proposal: proposal({ status: "accepted", resulting_version_label: "v2.1" }),
        version: { version_label: "v2.1" },
      });
      renderWithQueryClient(
        <ProposalCard recipeId={3} proposal={proposal({ records_outcome: outcomeProposal() })} />,
      );

      await user.click(screen.getByRole("button", { name: /Accept/ }));

      expect(acceptRecipeProposal).toHaveBeenCalledTimes(1);
    });
  });

  it("says nothing is sent to the machine either way", () => {
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);
    expect(screen.getByTestId("proposal-card")).toHaveTextContent(
      "Nothing is sent to the machine either way",
    );
  });

  it("accepts on a press and says what was recorded", async () => {
    const user = setupUser();
    acceptRecipeProposal.mockResolvedValue({
      proposal: proposal({
        status: "accepted",
        resulting_version_label: "v2.1",
      }),
      version: { version_label: "v2.1" },
    });
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(acceptRecipeProposal).toHaveBeenCalledWith(3, 5, { major: false });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(String(toastSuccess.mock.calls[0][0])).toContain("v2.1 recorded");
    expect(String(toastSuccess.mock.calls[0][0])).not.toContain("3");
  });

  it("tells the agent in the conversation which version the accepted change became", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptRecipeProposal.mockResolvedValue({
      proposal: proposal({
        status: "accepted",
        resulting_version_label: "v2.1",
      }),
      version: { version_label: "v2.1" },
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard recipeId={3} proposal={proposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    // The Recipe prompt recognises the turn by its first word, and the version is
    // named as the person sees it — never by its ordinal.
    await waitFor(() =>
      expect(tell).toHaveBeenCalledWith(
        "Accepted: your proposed change (Dose 18 g → 18.5 g) is now v2.1 of this Recipe.",
      ),
    );
    expect(tell).toHaveBeenCalledTimes(1);
  });

  it("tells the agent the first recipe is version 1", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptRecipeProposal.mockResolvedValue({
      proposal: designProposal({ status: "accepted" }),
      version: { version_label: "v1" },
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard recipeId={6} proposal={designProposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() =>
      expect(tell).toHaveBeenCalledWith("Accepted: your first version is now v1 of this Recipe."),
    );
  });

  it("tells the agent nothing when an accept or a decline is refused", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptRecipeProposal.mockRejectedValue(
      new ApiClientError("The Recipe has moved on.", { status: 409, code: "PROPOSAL_STALE" }),
    );
    declineRecipeProposal.mockRejectedValue(
      new ApiClientError("Proposal 5 has already been answered", {
        status: 409,
        code: "PROPOSAL_DECIDED",
      }),
    );
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard recipeId={3} proposal={proposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /Accept/ }));
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.click(screen.getByRole("button", { name: /Decline it/ }));
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(2));

    expect(tell).not.toHaveBeenCalled();
  });

  it("tells the agent a decline with the reason the person gave", async () => {
    const user = setupUser();
    const tell = vi.fn();
    declineRecipeProposal.mockResolvedValue({
      proposal: proposal({ status: "declined", decline_note: "the dose is not the problem" }),
      version: null,
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard recipeId={3} proposal={proposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "  the dose is not the problem ");
    await user.click(screen.getByRole("button", { name: /Decline it/ }));

    await waitFor(() => expect(tell).toHaveBeenCalledWith("Declined: the dose is not the problem"));
    expect(tell).toHaveBeenCalledTimes(1);
  });

  it("says no reason was given when the note is left empty", async () => {
    const user = setupUser();
    const tell = vi.fn();
    declineRecipeProposal.mockResolvedValue({
      proposal: designProposal({ status: "declined" }),
      version: null,
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard recipeId={6} proposal={designProposal()} />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.click(screen.getByRole("button", { name: /Decline it/ }));

    await waitFor(() => expect(tell).toHaveBeenCalledWith("Declined: no reason given."));
    expect(declineRecipeProposal).toHaveBeenCalledWith(6, 8, { note: "" });
  });

  it("asks why before declining, and declines with the note", async () => {
    const user = setupUser();
    declineRecipeProposal.mockResolvedValue({
      proposal: proposal({ status: "declined" }),
      version: null,
    });
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    // The field exists before it is opened, so `aria-controls` names something
    // a reader can reach; it is hidden until the person says Decline.
    const button = screen.getByRole("button", { name: /^Decline$/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("decline-note")).toHaveAttribute("hidden");
    expect(declineRecipeProposal).not.toHaveBeenCalled();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("decline-note")).not.toHaveAttribute("hidden");

    await user.type(screen.getByLabelText(/Why not/), "The dose is not the problem.");
    await user.click(screen.getByRole("button", { name: "Decline it" }));

    expect(declineRecipeProposal).toHaveBeenCalledWith(3, 5, {
      note: "The dose is not the problem.",
    });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Proposal declined"));
  });

  it("submits the note on Enter, because it is one line", async () => {
    const user = setupUser();
    declineRecipeProposal.mockResolvedValue({
      proposal: proposal({ status: "declined" }),
      version: null,
    });
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "Tried it last week.{Enter}");

    expect(declineRecipeProposal).toHaveBeenCalledWith(3, 5, { note: "Tried it last week." });
  });

  it("Escape closes the field and declines nothing", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "half a thought{Escape}");

    expect(declineRecipeProposal).not.toHaveBeenCalled();
    expect(screen.getByTestId("decline-note")).toHaveAttribute("hidden");
    // And the half-typed thought is gone with it, rather than waiting to be
    // sent with the next decline.
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    expect(screen.getByLabelText(/Why not/)).toHaveValue("");
  });

  it("caps the note where the route does", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));

    expect(screen.getByLabelText(/Why not/)).toHaveAttribute("maxlength", "500");
  });

  it("does not carry a half-typed turn-down onto another proposal", async () => {
    const user = setupUser();
    const { rerender } = renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);
    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "about this one");

    rerender(<ProposalCard recipeId={3} proposal={proposal({ id: 6 })} />);

    expect(screen.getByTestId("decline-note")).toHaveAttribute("hidden");
  });

  it("shows a proposal nobody can read as one, with only a way out", () => {
    renderWithQueryClient(
      <ProposalCard
        recipeId={3}
        proposal={proposal({ readable: false, changes: [], changed: [] })}
      />,
    );

    expect(screen.getByTestId("proposal-unreadable")).toHaveTextContent("cannot be read");
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Decline$/ })).toBeInTheDocument();
  });

  it("offers nothing to press on one the Recipe overtook", () => {
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal({ status: "stale" })} />);

    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Decline$/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent("moved on to another version");
  });

  it("toasts the server's own words when the Recipe has moved on", async () => {
    const user = setupUser();
    acceptRecipeProposal.mockRejectedValue(
      new Error("Proposal 5 was made against a version that is no longer current"),
    );
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "Proposal 5 was made against a version that is no longer current",
      ),
    );
  });

  it("warns before the press when the Recipe has already moved on", () => {
    renderWithQueryClient(
      <ProposalCard recipeId={3} proposal={proposal({ base_is_current: false })} />,
    );
    expect(screen.getByTestId("proposal-stale")).toHaveTextContent("moved on");
  });

  it("says a combined change cannot be separated by its own prediction", () => {
    renderWithQueryClient(
      <ProposalCard
        recipeId={3}
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
        recipeId={3}
        proposal={proposal({
          status: "accepted",
          resulting_version_label: "v3",
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
        recipeId={3}
        proposal={proposal({ status: "declined", decline_note: "The dose is not the problem." })}
      />,
    );
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent(
      "The dose is not the problem.",
    );
  });

  it("links back to the conversation only where that is somewhere else", () => {
    const { unmount } = renderWithQueryClient(
      <ProposalCard recipeId={3} proposal={proposal()} showThreadLink />,
    );
    expect(screen.getByTestId("proposal-thread").querySelector("a")).toHaveAttribute(
      "href",
      "/chat?thread=9",
    );
    unmount();

    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);
    expect(screen.queryByTestId("proposal-thread")).not.toBeInTheDocument();
  });
});

/**
 * A Recipe's first recipe: the same question as a change, asked about a Recipe with
 * no recipe yet. What it must get right is what the person is agreeing to — a
 * whole recipe, a profile that is only a draft — and what happens after.
 */
describe("ProposalCard, minor versions", () => {
  it("names the versions it talks about, never by their ordinals", () => {
    renderWithQueryClient(
      <ProposalCard
        recipeId={3}
        proposal={proposal({
          status: "accepted",
          prediction: "Compared to v1.1: a touch more body and no slower.",
          base_version_label: "v1.1",
          compares_to_version_label: "v1.1",
          resulting_version_label: "v1.2",
          decided_at: "2026-03-01T10:00:00.000Z",
        })}
      />,
    );

    const card = screen.getByTestId("proposal-card");
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent("Accepted as v1.2");
    expect(screen.getByTestId("proposal-prediction")).toHaveTextContent("compared to v1.1");
    expect(card).not.toHaveTextContent(/\bv3\b/);
    expect(card).not.toHaveTextContent(/\bv2\b/);
  });
});

describe("ProposalCard, major or minor", () => {
  it("preselects minor for a dial-in change, and says which version Accept records", async () => {
    const user = setupUser();
    acceptRecipeProposal.mockResolvedValue({
      proposal: proposal({ status: "accepted", resulting_version_label: "v3" }),
      version: { version_label: "v3" },
    });
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    const box = screen.getByRole("checkbox", { name: "Major change" });
    expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Accept as v2.1" })).toBeInTheDocument();
    expect(screen.queryByTestId("major-reason")).not.toBeInTheDocument();

    await user.click(box);
    expect(screen.getByRole("button", { name: "Accept as v3" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept as v3" }));

    expect(acceptRecipeProposal).toHaveBeenCalledWith(3, 5, { major: true });
  });

  it("shows the agent's suggestion of major with its reason, and preselects it", () => {
    renderWithQueryClient(
      <ProposalCard
        recipeId={3}
        proposal={proposal({
          suggest_major: true,
          major_reason: "Half a gram changes what this recipe is for.",
        })}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Major change" })).toBeChecked();
    expect(screen.getByTestId("major-reason")).toHaveTextContent(
      "The agent suggests a major version: “Half a gram changes what this recipe is for.”",
    );
    expect(screen.getByRole("button", { name: "Accept as v3" })).toBeInTheDocument();
  });

  it("offers no box on a first recipe, which fills version 1 either way", () => {
    renderWithQueryClient(<ProposalCard recipeId={6} proposal={designProposal()} />);

    expect(screen.queryByTestId("major-choice")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
  });
});

describe("ProposalCard, a first recipe with a new profile", () => {
  const accepted = (extra: Record<string, unknown> = {}) => ({
    proposal: designProposal({ status: "accepted", resulting_version_label: "v1" }),
    version: { version_label: "v1" },
    profile_draft_id: 14,
    profile_row_id: 4,
    ...extra,
  });

  it("replaces its profile line with the Name field and draws the profile's curve", async () => {
    getDraftStanding.mockResolvedValue(
      designStanding({ profile: proProfile({ label: "Guji Bloom" }) }, true),
    );
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    const field = await screen.findByTestId("proposal-name");
    expect(field).toHaveValue("Guji Bloom");
    // The profile line is the field now, and the draft is not a separate thing to go and answer.
    expect(screen.getByTestId("proposal-recipe")).not.toHaveTextContent("a new draft");
    expect(screen.queryByTestId("proposal-draft-link")).not.toBeInTheDocument();
    expect(await screen.findByTestId("profile-curve")).toHaveAttribute("data-title", "Proposed");
    expect(screen.getByRole("button", { name: "Accept and sync" })).toBeInTheDocument();
  });

  it("says Accept, and sends no sync, with Writes off", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(designStanding({ writes_enabled: false }, true));
    acceptRecipeProposal.mockResolvedValue(accepted());
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("accept-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    expect(screen.getByTestId("accept-proposal")).toHaveTextContent(/^Accept$/);
    await user.click(screen.getByTestId("accept-proposal"));

    await waitFor(() => expect(acceptRecipeProposal).toHaveBeenCalledTimes(1));
    expect(runSync).not.toHaveBeenCalled();
  });

  it("accepts under the typed name, then starts the sync once, and tells the agent the new name", async () => {
    const user = setupUser();
    const tell = vi.fn();
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    acceptRecipeProposal.mockResolvedValue(accepted());
    renderCard(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard recipeId={6} proposal={designProposal()} />
      </TellAgentContext.Provider>,
    );

    const field = await screen.findByTestId("proposal-name");
    await user.clear(field);
    await user.type(field, "Gentle Bloom");
    await waitFor(() =>
      expect(screen.getByTestId("accept-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.dblClick(screen.getByTestId("accept-proposal"));

    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    expect(acceptRecipeProposal).toHaveBeenCalledTimes(1);
    expect(acceptRecipeProposal).toHaveBeenCalledWith(6, 8, { profileLabel: "Gentle Bloom" });
    expect(tell).toHaveBeenCalledTimes(1);
    expect(tell).toHaveBeenCalledWith(
      "Accepted: your first version is now v1 of this Recipe, with its profile named Gentle Bloom (you proposed it as Guji Bloom).",
    );
  });

  it("sends the proposed name unchanged, and no rename, when the field is left alone", async () => {
    const user = setupUser();
    const tell = vi.fn();
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    acceptRecipeProposal.mockResolvedValue(accepted());
    renderCard(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard recipeId={6} proposal={designProposal()} />
      </TellAgentContext.Provider>,
    );

    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("accept-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(screen.getByTestId("accept-proposal"));

    await waitFor(() =>
      expect(tell).toHaveBeenCalledWith("Accepted: your first version is now v1 of this Recipe."),
    );
    expect(acceptRecipeProposal).toHaveBeenCalledWith(6, 8, { profileLabel: "Guji Bloom" });
  });

  it("starts no sync when the accept is refused", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    acceptRecipeProposal.mockRejectedValue(
      new ApiClientError("Gentle Bloom is already a profile.", { status: 422, code: "REFUSED" }),
    );
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("accept-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(screen.getByTestId("accept-proposal"));

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(runSync).not.toHaveBeenCalled();
  });

  it("is blocked, with the server's sentence under the field, while the name is taken or empty", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    checkDraftName.mockImplementation(async (_id: number, label: string) => ({
      label: label.trim(),
      refused: label.trim() === "Adaptive Bloom" ? "Adaptive Bloom is already a profile." : null,
    }));
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    const field = await screen.findByTestId("proposal-name");
    await user.clear(field);
    expect(screen.getByTestId("proposal-name-problem")).toHaveTextContent("A profile needs a name");
    await user.click(screen.getByTestId("accept-proposal"));
    await user.type(field, "Adaptive Bloom");
    await waitFor(() =>
      expect(screen.getByTestId("proposal-name-problem")).toHaveTextContent(
        "Adaptive Bloom is already a profile.",
      ),
    );
    await user.click(screen.getByTestId("accept-proposal"));

    expect(acceptRecipeProposal).not.toHaveBeenCalled();
  });

  it("sends no name, and no sync, for a draft that was already answered elsewhere", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(
      designStanding({
        draft: draft({ id: 14, status: "approved", draft_label: "Guji Bloom" }),
        landing: null,
        state: "approved",
        reason: "It goes to the machine at the next sync.",
      }),
    );
    acceptRecipeProposal.mockResolvedValue(
      accepted({ profile_draft_id: null, profile_row_id: null }),
    );
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await screen.findByText(
      /Accepting makes this the Recipe's version 1, with the profile as it stands/,
    );
    expect(screen.queryByTestId("proposal-name")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));

    await waitFor(() => expect(acceptRecipeProposal).toHaveBeenCalledWith(6, 8));
    expect(runSync).not.toHaveBeenCalled();
    expect(String(toastSuccess.mock.calls[0][0])).toBe("Accepted: version 1 is set.");
  });

  it("says where the profile stands once the recipe is accepted, in the server's words", async () => {
    getDraftStanding.mockResolvedValue(
      designStanding({
        draft: draft({ id: 14, status: "approved", draft_label: "Gentle Bloom" }),
        landing: null,
        state: "approved",
        reason: "It goes to the machine at the next sync.",
        recipe_version_label: "v1",
      }),
    );
    renderCard(
      <ProposalCard
        recipeId={6}
        proposal={designProposal({ status: "accepted", resulting_version_label: "v1" })}
      />,
    );

    const line = await screen.findByTestId("profile-standing");
    expect(line).toHaveTextContent("✓ Approved · Gentle Bloom");
    expect(line).toHaveTextContent("It goes to the machine at the next sync.");
  });

  it("gives one toast only: the sync's own, when the accept syncs, and none of its own", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    acceptRecipeProposal.mockResolvedValue(accepted());
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("accept-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(screen.getByTestId("accept-proposal"));

    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("promises only a sync in its hint, not what the sync will do", async () => {
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    const hint = await screen.findByText(
      /Accepting makes this the Recipe's version 1 and approves/,
    );
    expect(hint).toHaveTextContent("It also starts a sync; you select it on the machine yourself.");
    expect(hint).not.toHaveTextContent("puts the profile on the machine");
  });

  it("keeps the Name field out of the recipe's definition list", async () => {
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    const field = await screen.findByTestId("proposal-name");
    expect(field.closest("dl")).toBeNull();
    const recipe = screen.getByTestId("proposal-recipe");
    expect(recipe).not.toHaveTextContent("Profile");
    expect(recipe.querySelectorAll("dt")).toHaveLength(recipe.querySelectorAll("dd").length);
  });

  it("says Accept, gives the top bar's words and starts no sync when the machine cannot be reached", async () => {
    const user = setupUser();
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: false,
      host: "",
      identity: null,
      last_status: null,
    });
    getDraftStanding.mockResolvedValue(designStanding({}, true));
    acceptRecipeProposal.mockResolvedValue(accepted());
    renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("accept-proposal")).toHaveTextContent(/^Accept$/),
    );
    expect(
      screen.getByText(/The machine is not reachable. The archive still works; a sync cannot./),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("accept-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(screen.getByTestId("accept-proposal"));

    await waitFor(() => expect(acceptRecipeProposal).toHaveBeenCalledTimes(1));
    expect(runSync).not.toHaveBeenCalled();
  });

  it("says what the agent proposed beside the approved name once the recipe is accepted", async () => {
    getDraftStanding.mockResolvedValue(
      designStanding({
        draft: draft({ id: 14, status: "approved", draft_label: "Kenya Bright Bloom" }),
        landing: null,
        state: "approved",
        reason: "It goes to the machine at the next sync.",
      }),
    );
    renderCard(
      <ProposalCard
        recipeId={6}
        proposal={designProposal({ status: "accepted", resulting_version_label: "v1" })}
        proposedProfileAs="Kenya Bloom"
      />,
    );

    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      "✓ Approved as Kenya Bright Bloom (proposed as Kenya Bloom)",
    );
  });

  it("starts from its own name when it is handed another proposal", async () => {
    const user = setupUser();
    getDraftStanding.mockImplementation(async (id: number) =>
      designStanding({ draft: draft({ id, is_new: true, draft_label: `Bloom ${id}` }) }, true),
    );
    const view = renderCard(<ProposalCard recipeId={6} proposal={designProposal()} />);
    const field = await screen.findByTestId("proposal-name");
    await user.type(field, " half typed");

    view.rerender(
      <SyncOwnerProvider>
        <ProposalCard recipeId={6} proposal={designProposal({ id: 9, draft_id: 15 })} />
      </SyncOwnerProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("proposal-name")).toHaveValue("Bloom 15"));
  });
});

describe("ProposalCard, a first recipe", () => {
  it("renders the recipe, the draft it carries and no prediction", async () => {
    renderWithQueryClient(<ProposalCard recipeId={6} proposal={designProposal()} />);

    const card = screen.getByTestId("proposal-card");
    expect(card).toHaveAttribute("data-kind", "design");
    expect(card).toHaveTextContent("Version 1 is waiting for you");
    const recipe = screen.getByTestId("proposal-recipe");
    expect(recipe).toHaveTextContent("Guji Bloom");
    expect(recipe).toHaveTextContent("94 °C");
    expect(recipe).toHaveTextContent("Grind20");
    expect(recipe).toHaveTextContent("Dose18 g");
    expect(recipe).toHaveTextContent("Yield40 g (1:2.2)");
    // The draft continues a profile the person has, so its line stays and links the draft.
    expect(await screen.findByTestId("proposal-draft-link")).toHaveAttribute(
      "href",
      "/profiles#staged",
    );
    expect(screen.queryByTestId("proposal-name")).not.toBeInTheDocument();
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
        recipeId={6}
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

  it("accepts through the accept route and says version 1 is set, with the proposal link", async () => {
    const user = setupUser();
    acceptRecipeProposal.mockResolvedValue({
      proposal: designProposal({
        status: "accepted",
        changes: [],
        resulting_version_id: 60,
        resulting_version_label: "v1",
        decided_at: "2026-03-02T09:00:00.000Z",
      }),
      version: { version_label: "v1" },
    });
    renderWithQueryClient(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(acceptRecipeProposal).toHaveBeenCalledWith(6, 8);
    const decided = await screen.findByTestId("proposal-decided");
    expect(decided).toHaveTextContent("version 1 is set");
    expect(decided).toHaveTextContent("shots brewed on its profile are filed here");
    expect(within(decided).getByRole("link", { name: "version 1" })).toHaveAttribute(
      "href",
      "/recipes/6",
    );
    // The profile was approved with it: nothing sends the person to the Profiles page for it.
    expect(decided).not.toHaveTextContent("make active");
    expect(within(decided).queryByRole("link", { name: /Profiles page/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    expect(String(toastSuccess.mock.calls[0][0])).toContain("version 1 is set");
  });

  it("declines with the note, as a change does", async () => {
    const user = setupUser();
    declineRecipeProposal.mockResolvedValue({
      proposal: designProposal({ status: "declined", decline_note: "too long a ratio" }),
      version: null,
    });
    renderWithQueryClient(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await user.click(screen.getByRole("button", { name: /^Decline$/ }));
    await user.type(screen.getByLabelText(/Why not/), "too long a ratio{Enter}");

    expect(declineRecipeProposal).toHaveBeenCalledWith(6, 8, { note: "too long a ratio" });
    expect(await screen.findByTestId("proposal-decided")).toHaveTextContent(
      "Declined: “too long a ratio” Its draft was discarded, unless it had already been put on the machine.",
    );
  });

  it("says in words that the draft is gone when the accept is refused for it", async () => {
    const user = setupUser();
    acceptRecipeProposal.mockRejectedValue(
      new ApiClientError("The profile draft proposal 8 carries is no longer open", {
        status: 409,
        code: "PROPOSAL_DRAFT_CLOSED",
      }),
    );
    renderWithQueryClient(<ProposalCard recipeId={6} proposal={designProposal()} />);

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
    acceptRecipeProposal.mockRejectedValue(
      new ApiClientError("Proposal 8 has already been answered", {
        status: 409,
        code: "PROPOSAL_DECIDED",
      }),
    );
    renderWithQueryClient(<ProposalCard recipeId={6} proposal={designProposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.queryByTestId("proposal-draft-closed")).not.toBeInTheDocument();
  });

  it("says a newer card replaced one that was overtaken", () => {
    renderWithQueryClient(
      <ProposalCard recipeId={6} proposal={designProposal({ status: "stale" })} />,
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
        recipeId={6}
        proposal={designProposal({ status: "accepted", resulting_version_label: "v1" })}
      />,
    );

    expect(screen.getByTestId("proposal-card")).toHaveTextContent("A version 1 that was proposed");
    expect(screen.getByTestId("proposal-decided")).toHaveTextContent("version 1 is set");
    // The server still draws an accepted first recipe against the empty one it
    // filled, so scrolling back through the conversation shows what was agreed.
    expect(screen.getByTestId("proposal-recipe")).toBeInTheDocument();
  });

  it("reads the recipe off the diff against the empty version 1", () => {
    expect(designVersionFields(designProposal().changes)).toEqual({
      profile: "Guji Bloom",
      temperature: "94 °C",
      grind: "20",
      grindIsAbsolute: true,
      dose: "18 g",
      target: "40 g",
      ratio: "1:2.2",
    });
    expect(designVersionFields([])).toEqual({
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
  it("shows what the accept recorded without waiting for the lists to be read again", async () => {
    const user = setupUser();
    acceptRecipeProposal.mockResolvedValue({
      proposal: proposal({ status: "accepted", resulting_version_label: "v3" }),
      version: { version_label: "v3" },
    });
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={proposal()} />);

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    expect(await screen.findByTestId("proposal-decided")).toHaveTextContent("Accepted as v3");
  });
});

/**
 * A grind change was accepted in production and the card said only "Accepted
 * as v2": no push appeared, because none was needed, and nothing said so. The
 * agent, asked, invented a profile push and a staging queue. The card is where
 * the next step is said, from the change it shows.
 */
describe("the next step after an accept", () => {
  const grind = [
    { field: "grind_setting", label: "Grind", before: "2", after: "1", from_profile: false },
    { field: "grind_value", label: "Grind value", before: "2", after: "1", from_profile: false },
  ];
  const accepted = (overrides: Partial<Parameters<typeof proposal>[0]> = {}) =>
    proposal({
      status: "accepted",
      resulting_version_label: "v2",
      decided_at: "2026-03-02T09:00:00.000Z",
      changes: grind,
      changed: ["the grind"],
      ...overrides,
    });

  it("says a grind change needs nothing on the machine, and what to do instead", () => {
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={accepted()} />);

    const decided = screen.getByTestId("proposal-decided");
    expect(decided).toHaveTextContent("Accepted as v2");
    const step = screen.getByTestId("proposal-next-step");
    expect(step).toHaveTextContent(
      "Nothing goes to the machine: the profile is unchanged, so there is nothing to push.",
    );
    expect(step).toHaveTextContent("Set the grinder to 1 and brew");
    expect(step).toHaveTextContent("filed under v2 by themselves");
    // The words are the grind, once: not "set the grinder to 1 and 1".
    expect(step).not.toHaveTextContent("1 and 1");
  });

  it("names every hand step of a change that moves two things", () => {
    expect(
      handSteps([
        { field: "dose_g", label: "Dose", before: "18 g", after: "18.5 g", from_profile: false },
        {
          field: "target_yield_g",
          label: "Target yield",
          before: "36 g",
          after: "38 g",
          from_profile: false,
        },
      ]),
    ).toEqual(["dose 18.5 g", "stop at 38 g out"]);
    // A grind with only a number still names the number.
    expect(
      handSteps([
        {
          field: "grind_value",
          label: "Grind value",
          before: "22",
          after: "21",
          from_profile: false,
        },
      ]),
    ).toEqual(["set the grinder to 21"]);
    // A cleared field is nothing to do by hand.
    expect(
      handSteps([
        { field: "dose_g", label: "Dose", before: "18 g", after: null, from_profile: false },
      ]),
    ).toEqual([]);
  });

  it("says before the press what the person does, and that nothing is pushed", () => {
    renderWithQueryClient(
      <ProposalCard recipeId={3} proposal={proposal({ changes: grind, changed: ["the grind"] })} />,
    );
    expect(screen.getByTestId("proposal-card")).toHaveTextContent(
      "Nothing is sent to the machine either way: to brew it, you set the grinder to 1 yourself — the profile stays as it is, so there is nothing to push.",
    );
    expect(acceptHint([])).toBe(
      "Accepting records a new version of this Recipe. Nothing is sent to the machine either way.",
    );
  });

  it("names the accepted change to the agent", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptRecipeProposal.mockResolvedValue({
      proposal: accepted(),
      version: { version_label: "v1.1" },
    });
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ProposalCard
          recipeId={3}
          proposal={proposal({ changes: grind, changed: ["the grind"] })}
        />
      </TellAgentContext.Provider>,
    );

    await user.click(screen.getByRole("button", { name: /Accept/ }));

    await waitFor(() =>
      expect(tell).toHaveBeenCalledWith(
        "Accepted: your proposed change (Grind 2 → 1; Grind value 2 → 1) is now v1.1 of this Recipe.",
      ),
    );
  });
});

describe("ProposalCard, the drink", () => {
  const drinkProposal = () =>
    proposal({
      changes: [
        {
          field: "target_yield_g",
          label: "Target yield",
          before: "40 g",
          after: "32 g",
          from_profile: false,
        },
        {
          field: "drink",
          label: "Drink",
          before: "Espresso",
          after: "Cappuccino",
          from_profile: false,
        },
        {
          field: "drink_notes",
          label: "Drink notes",
          before: null,
          after: "whole milk, 120 ml",
          from_profile: false,
        },
      ],
      suggest_major: true,
      major_reason: "the drink changes",
    });

  it("draws the drink and the notes first, the notes as an addition in the person's words", () => {
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={drinkProposal()} />);
    const rows = within(screen.getByTestId("proposal-changes")).getAllByRole("listitem");
    expect(rows.map((row) => row.dataset.field)).toEqual([
      "drink",
      "drink_notes",
      "target_yield_g",
    ]);
    expect(rows[0]).toHaveTextContent("Drink");
    expect(rows[0]).toHaveTextContent("Espresso");
    expect(rows[0]).toHaveTextContent("Cappuccino");
    expect(rows[1]).toHaveTextContent("Notes");
    expect(rows[1]).toHaveTextContent("+ “whole milk, 120 ml”");
  });

  it("draws notes removed and notes changed", () => {
    renderWithQueryClient(
      <ProposalCard
        recipeId={3}
        proposal={proposal({
          changes: [
            {
              field: "drink_notes",
              label: "Drink notes",
              before: "oat",
              after: "whole",
              from_profile: false,
            },
          ],
        })}
      />,
    );
    const row = screen.getByTestId("proposal-changes").querySelector("li");
    expect(row).toHaveTextContent("“oat”");
    expect(row).toHaveTextContent("“whole”");
  });

  it("draws notes removed as a subtraction", () => {
    renderWithQueryClient(
      <ProposalCard
        recipeId={3}
        proposal={proposal({
          changes: [
            {
              field: "drink_notes",
              label: "Drink notes",
              before: "oat",
              after: null,
              from_profile: false,
            },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("proposal-changes")).toHaveTextContent("− “oat”");
  });

  it("shows the suggestion as served: the major is chosen, with the served reason", () => {
    renderWithQueryClient(<ProposalCard recipeId={3} proposal={drinkProposal()} />);
    expect(screen.getByRole("checkbox", { name: "Major change" })).toBeChecked();
    // The server's own reason, said as the form says it: no agent said it.
    expect(screen.getByTestId("major-suggested")).toHaveTextContent(
      "v3 suggested: the drink changes",
    );
    expect(screen.queryByTestId("major-reason")).not.toBeInTheDocument();
    expect(screen.queryByText(/The agent suggests/)).not.toBeInTheDocument();
    expect(screen.getByTestId("accept-proposal")).toHaveTextContent("v3");
  });
});
