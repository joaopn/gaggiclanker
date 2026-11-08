import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import {
  type GeneralDeletionFallback,
  GeneralInsightDeletionCard,
} from "@/components/chat/GeneralInsightDeletionCard";
import { proposalFrom } from "@/components/chat/ProposeCard";
import { ChatThreadContext, TellAgentContext } from "@/components/chat/tellAgent";
import { queryKeys } from "@/lib/queryKeys";
import { generalInsightDeletion, knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const {
  getGeneralInsightDeletion,
  acceptGeneralInsightDeletion,
  keepGeneralInsightDeletion,
  getKnowledgeInsight,
  toastSuccess,
} = vi.hoisted(() => ({
  getGeneralInsightDeletion: vi.fn(),
  acceptGeneralInsightDeletion: vi.fn(),
  keepGeneralInsightDeletion: vi.fn(),
  getKnowledgeInsight: vi.fn(),
  toastSuccess: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getGeneralInsightDeletion,
  acceptGeneralInsightDeletion,
  keepGeneralInsightDeletion,
  getKnowledgeInsight,
}));
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const FALLBACK: GeneralDeletionFallback = {
  insightId: 7,
  text: "Never go finer than 8 on the Niche Zero: it chokes.",
  said: "forget the Niche note, 7 is fine now",
  scopeLabels: ["grinder Niche Zero"],
  reach: 4,
};

beforeEach(() => {
  vi.clearAllMocks();
  getGeneralInsightDeletion.mockResolvedValue(generalInsightDeletion());
  getKnowledgeInsight.mockResolvedValue(
    knowledgeInsight({ id: 7, source: "user", confirmed: true, scope: { grinder_id: 2 } }),
  );
});

function renderCard(tell?: (message: string) => void) {
  return renderWithQueryClient(
    <ChatThreadContext.Provider value={14}>
      <TellAgentContext.Provider value={tell ?? null}>
        <GeneralInsightDeletionCard proposalId={12} fallback={FALLBACK} />
      </TellAgentContext.Provider>
    </ChatThreadContext.Provider>,
  );
}

describe("GeneralInsightDeletionCard", () => {
  it("shows the tool's own words until the live proposal has been read", () => {
    getGeneralInsightDeletion.mockReturnValue(new Promise(() => undefined));
    renderCard();

    const card = screen.getByTestId("general-deletion-card");
    expect(card).toHaveAttribute("data-status", "loading");
    expect(card).toHaveTextContent("Delete general insight #7?");
    expect(card).toHaveTextContent(FALLBACK.text);
    expect(card).toHaveTextContent(`You said: “${FALLBACK.said}”`);
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("is the sketch: title, text, who and where and how far, what you said, why, and two buttons", async () => {
    renderCard();

    const card = await screen
      .findByRole("button", { name: "Delete" })
      .then(() => screen.getByTestId("general-deletion-card"));
    expect(getGeneralInsightDeletion).toHaveBeenCalledWith(12);
    expect(card).toHaveAttribute("data-status", "proposed");
    expect(within(card).getByTestId("general-deletion-title")).toHaveTextContent(
      "Delete general insight #7?",
    );
    expect(within(card).getByTestId("general-deletion-text")).toHaveTextContent(FALLBACK.text);
    await waitFor(() =>
      expect(within(card).getByTestId("general-deletion-where")).toHaveTextContent(
        "your note · grinder Niche Zero · told to 4 Recipes now",
      ),
    );
    expect(within(card).getByTestId("general-deletion-said")).toHaveTextContent(
      `You said: “${FALLBACK.said}”`,
    );
    expect(within(card).getByTestId("general-deletion-reason")).toHaveTextContent(
      "The person said 7 is fine now.",
    );
    expect(card).toHaveTextContent(
      "Deleting it changes what all 4 Recipes' conversations are told.",
    );
    expect(within(card).getByRole("button", { name: "Keep" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Delete" })).toBeEnabled();
    expect(acceptGeneralInsightDeletion).not.toHaveBeenCalled();
    expect(card).not.toHaveTextContent(/_id|thread/);
  });

  it("says from Find patterns for a pattern's insight, and every Recipe for no scope", async () => {
    getGeneralInsightDeletion.mockResolvedValue(
      generalInsightDeletion({ scope_labels: [], reach: 1 }),
    );
    getKnowledgeInsight.mockResolvedValue(
      knowledgeInsight({ id: 7, source: "pattern", pattern_run_id: 3, confirmed: true }),
    );
    renderCard();

    await waitFor(() =>
      expect(screen.getByTestId("general-deletion-where")).toHaveTextContent(
        "from Find patterns · every Recipe · told to 1 Recipe now",
      ),
    );
  });

  it("deletes on Delete, says Deleted., and sends no chat message", async () => {
    const user = setupUser();
    const tell = vi.fn();
    acceptGeneralInsightDeletion.mockResolvedValue(
      generalInsightDeletion({
        status: "deleted",
        insight_id: null,
        scope_labels: [],
        reach: null,
      }),
    );
    renderCard(tell);

    await user.click(await screen.findByRole("button", { name: "Delete" }));

    expect(acceptGeneralInsightDeletion).toHaveBeenCalledWith(12);
    expect(keepGeneralInsightDeletion).not.toHaveBeenCalled();
    const card = screen.getByTestId("general-deletion-card");
    await waitFor(() => expect(card).toHaveAttribute("data-status", "deleted"));
    expect(screen.getByTestId("general-deletion-decided")).toHaveTextContent("Deleted.");
    expect(screen.queryByRole("button", { name: "Keep" })).not.toBeInTheDocument();
    expect(tell).not.toHaveBeenCalled();
  });

  it("keeps on Keep and says Kept.", async () => {
    const user = setupUser();
    keepGeneralInsightDeletion.mockResolvedValue(generalInsightDeletion({ status: "kept" }));
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Keep" }));

    expect(keepGeneralInsightDeletion).toHaveBeenCalledWith(12);
    expect(acceptGeneralInsightDeletion).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByTestId("general-deletion-decided")).toHaveTextContent("Kept."),
    );
  });

  it("a second click while the first is in flight sends nothing more", async () => {
    const user = setupUser();
    let finish: (value: unknown) => void = () => undefined;
    acceptGeneralInsightDeletion.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    renderCard();
    const button = await screen.findByRole("button", { name: "Delete" });

    await act(async () => {
      button.click();
      button.click();
    });
    await user.click(button);

    expect(acceptGeneralInsightDeletion).toHaveBeenCalledTimes(1);
    finish(generalInsightDeletion({ status: "deleted", insight_id: null }));
  });

  it("says the stale sentence for a proposal the insight's change made stale", async () => {
    getGeneralInsightDeletion.mockResolvedValue(
      generalInsightDeletion({ status: "stale", stale_cause: "changed" }),
    );
    renderCard();

    expect(await screen.findByTestId("general-deletion-decided")).toHaveTextContent(
      "It was changed since the agent asked: nothing was deleted.",
    );
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("says a failed press in the server's words and leaves the buttons", async () => {
    const user = setupUser();
    acceptGeneralInsightDeletion.mockRejectedValue(
      new ApiClientError("General insight deletion 12 is kept", {
        status: 409,
        code: "INSIGHT_DELETION_DECIDED",
        details: { field: "status", message: "It was kept already." },
      }),
    );
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Delete" }));

    const alert = await screen.findByTestId("general-deletion-error");
    expect(alert).toHaveTextContent("It was kept already. Your press changed nothing.");
    expect(alert).not.toHaveTextContent(/INSIGHT_|409/);
  });

  it("says a network failure in words too", async () => {
    const user = setupUser();
    keepGeneralInsightDeletion.mockRejectedValue(new Error("Network down"));
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Keep" }));

    expect(await screen.findByTestId("general-deletion-error")).toHaveTextContent(
      "Network down Your press changed nothing.",
    );
  });
});

describe("what deleting it changes, by how many Recipes are told it", () => {
  it.each([
    [4, "Deleting it changes what all 4 Recipes' conversations are told."],
    [2, "Deleting it changes what all 2 Recipes' conversations are told."],
    [1, "Deleting it changes what 1 Recipe's conversations are told."],
    [0, "No Recipe is told it now."],
  ])("with %i it says %s", async (reach, words) => {
    getGeneralInsightDeletion.mockResolvedValue(generalInsightDeletion({ reach }));
    renderCard();

    const note = await screen.findByTestId("general-deletion-note");
    expect(note).toHaveTextContent(words);
    if (reach === 0) {
      expect(note).not.toHaveTextContent("Deleting it changes");
      expect(screen.getByTestId("general-deletion-where")).not.toHaveTextContent("told to");
    }
    expect(note).not.toHaveTextContent("1 Recipes");
  });

  it("says nothing of reach, and no 'told to', when the insight is no longer added", async () => {
    getGeneralInsightDeletion.mockResolvedValue(
      generalInsightDeletion({ reach: null, scope_labels: [] }),
    );
    renderCard();

    const note = await screen.findByTestId("general-deletion-note");
    expect(note).not.toHaveTextContent("Deleting it changes");
    expect(note).not.toHaveTextContent("No Recipe is told");
    expect(screen.getByTestId("general-deletion-where")).not.toHaveTextContent("told to");
  });
});

describe("a stale proposal says why", () => {
  it.each([
    ["changed", "It was changed since the agent asked: nothing was deleted."],
    ["deleted", "It was deleted another way since the agent asked: nothing was left to delete."],
  ] as const)("when the cause is %s", async (cause, words) => {
    getGeneralInsightDeletion.mockResolvedValue(
      generalInsightDeletion({
        status: "stale",
        stale_cause: cause,
        insight_id: cause === "deleted" ? null : 7,
        insight_text: cause === "deleted" ? "" : "Old words.",
      }),
    );
    renderCard();

    const decided = await screen.findByTestId("general-deletion-decided");
    expect(decided).toHaveTextContent(words);
    if (cause === "deleted") {
      // Not "changed ... nothing was deleted" beside "has since been removed".
      expect(decided).not.toHaveTextContent("changed since");
    }
  });
});

describe("a stale card for a taken-back insight scoped to one grinder", () => {
  it("keeps the scope it has and never says every Recipe, nor who is told it", async () => {
    getGeneralInsightDeletion.mockResolvedValue(
      generalInsightDeletion({
        status: "stale",
        stale_cause: "changed",
        insight_id: 7,
        scope_labels: ["grinder Niche Zero"],
        reach: null,
      }),
    );
    getKnowledgeInsight.mockResolvedValue(
      knowledgeInsight({ id: 7, source: "user", confirmed: false, scope: { grinder_id: 2 } }),
    );
    renderCard();

    await screen.findByTestId("general-deletion-decided");
    const where = screen.getByTestId("general-deletion-where");
    expect(where).toHaveTextContent("grinder Niche Zero");
    expect(where).not.toHaveTextContent("every Recipe");
    expect(where).not.toHaveTextContent("told to");
  });
});

describe("the conversation it answers in", () => {
  it.each([
    ["Delete", acceptGeneralInsightDeletion],
    ["Keep", keepGeneralInsightDeletion],
  ] as const)(
    "%s refreshes the conversation the card is in, by its real thread id",
    async (name, call) => {
      const user = setupUser();
      call.mockResolvedValue(generalInsightDeletion({ status: "kept" }));
      const { queryClient } = renderCard();
      const spy = vi.spyOn(queryClient, "invalidateQueries");

      await user.click(await screen.findByRole("button", { name }));

      await waitFor(() =>
        expect(spy).toHaveBeenCalledWith(
          expect.objectContaining({ queryKey: queryKeys.chat.thread("14") }),
        ),
      );
      expect(spy).not.toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: queryKeys.chat.thread("null") }),
      );
    },
  );
});

describe("the dispatch", () => {
  const content = JSON.stringify({
    proposal_id: 12,
    recipe_id: null,
    insight_id: 7,
    insight_text: FALLBACK.text,
    status: "proposed",
    general: true,
    said: FALLBACK.said,
    scope_labels: ["grinder Niche Zero"],
    reach: 4,
    note: "Nothing is deleted",
  });

  it("reads a general result into the general card, not the Recipe's", () => {
    const made = proposalFrom({
      id: "t1",
      name: "propose_insight_deletion",
      arguments: {},
      ok: true,
      content,
    });

    expect(made?.kind).toBe("general_insight_deletion");
    expect(made?.proposalId).toBe(12);
    expect(made?.generalDeletion).toEqual(FALLBACK);
  });

  it("still reads a Recipe's result into the Recipe's card", () => {
    const made = proposalFrom({
      id: "t2",
      name: "propose_insight_deletion",
      arguments: {},
      ok: true,
      content: JSON.stringify({
        proposal_id: 9,
        recipe_id: 3,
        insight_id: 7,
        insight_text: "x",
        general: false,
      }),
    });

    expect(made?.kind).toBe("insight_deletion");
    expect(made?.recipeId).toBe(3);
  });
});
