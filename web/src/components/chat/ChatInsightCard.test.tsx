import { act, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatInsightCard } from "@/components/chat/ChatInsightCard";
import { ChatThreadContext, TellAgentContext } from "@/components/chat/tellAgent";
import { knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getKnowledgeInsight, patchKnowledgeInsight, dismissKnowledgeInsight } = vi.hoisted(() => ({
  getKnowledgeInsight: vi.fn(),
  patchKnowledgeInsight: vi.fn(),
  dismissKnowledgeInsight: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getKnowledgeInsight,
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

function insight(overrides: Parameters<typeof knowledgeInsight>[0] = {}) {
  return knowledgeInsight({
    id: 7,
    text: "Two clicks finer on the Niche.",
    set_id: 3,
    set_version_id: 22,
    set_version_label: "v2",
    general: false,
    scope: {},
    evidence_shot_ids: [4, 6],
    ...overrides,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  getKnowledgeInsight.mockResolvedValue(insight());
});

function renderCard() {
  return renderWithQueryClient(<ChatInsightCard insightId={7} text="Two clicks finer." />);
}

describe("ChatInsightCard", () => {
  it("renders the insight text as inline markdown, with citations linked", async () => {
    getKnowledgeInsight.mockResolvedValue(
      insight({ text: "**Finer** helps, see shot 4 and `x`.\n\n# Big\n- one" }),
    );
    renderCard();

    const text = await screen.findByTestId("chat-insight-text");
    expect(within(text).getByText("Finer").tagName).toBe("STRONG");
    expect(within(text).getByText("x").tagName).toBe("CODE");
    expect(within(text).getByRole("link", { name: "shot 4" })).toHaveAttribute("href", "/shots/4");
    expect(within(text).queryByRole("heading")).not.toBeInTheDocument();
    expect(within(text).queryByRole("list")).not.toBeInTheDocument();
  });

  it("shows the text, the version it was learned at and the evidence as links", async () => {
    renderCard();

    const card = await screen.findByTestId("chat-insight");
    expect(card).toHaveAttribute("data-state", "waiting");
    expect(within(card).getByTestId("chat-insight-text")).toHaveTextContent(
      "Two clicks finer on the Niche.",
    );
    expect(within(card).getByTestId("chat-insight-about")).toHaveTextContent(
      "About this Set only, learned at v2.",
    );
    expect(within(card).getByRole("link", { name: "shot 4" })).toHaveAttribute("href", "/shots/4");
    expect(within(card).getByRole("link", { name: "shot 6" })).toHaveAttribute("href", "/shots/6");
    expect(within(card).getByRole("button", { name: "Add" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Dismiss" })).toBeEnabled();
    // Says it is not evidence yet, and that it is this Set's alone.
    expect(card).toHaveTextContent("Not evidence until you add it");
    expect(card).toHaveTextContent("no other Set's");
  });

  it("says when no version is recorded", async () => {
    getKnowledgeInsight.mockResolvedValue(
      insight({ set_version_id: null, set_version_label: null }),
    );
    renderCard();
    expect(await screen.findByTestId("chat-insight-about")).toHaveTextContent(
      "version not recorded",
    );
  });

  it("has no link to the Knowledge page", async () => {
    renderCard();
    await screen.findByTestId("chat-insight");
    expect(document.querySelector('a[href*="/knowledge"]')).toBeNull();
  });

  it("adds it on a press and then says so, and says whose conversations are told", async () => {
    const user = setupUser();
    patchKnowledgeInsight.mockResolvedValue(insight({ confirmed: true }));
    getKnowledgeInsight
      .mockResolvedValueOnce(insight())
      .mockResolvedValue(insight({ confirmed: true }));
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Add" }));

    expect(patchKnowledgeInsight).toHaveBeenCalledWith(7, { confirmed: true });
    const decided = await screen.findByTestId("chat-insight-decided");
    expect(decided).toHaveTextContent("Added: this Set's later conversations will be told it.");
    expect(screen.getByRole("link", { name: "the Set's page" })).toHaveAttribute("href", "/sets/3");
    expect(screen.queryByRole("button", { name: "Add" })).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-insight")).toHaveAttribute("data-state", "added");
  });

  it("dismisses it on a press and says it is kept for the agent only", async () => {
    const user = setupUser();
    dismissKnowledgeInsight.mockResolvedValue(insight({ dismissed: true }));
    getKnowledgeInsight
      .mockResolvedValueOnce(insight())
      .mockResolvedValue(insight({ dismissed: true }));
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    expect(dismissKnowledgeInsight).toHaveBeenCalledWith(7);
    expect(await screen.findByTestId("chat-insight-decided")).toHaveTextContent(
      "Dismissed: it will not be put in front of the model.",
    );
    expect(screen.getByTestId("chat-insight")).toHaveAttribute("data-state", "dismissed");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("takes an added insight back", async () => {
    const user = setupUser();
    getKnowledgeInsight.mockResolvedValue(insight({ confirmed: true }));
    patchKnowledgeInsight.mockResolvedValue(insight({ confirmed: false }));
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Take back" }));

    expect(patchKnowledgeInsight).toHaveBeenCalledWith(7, { confirmed: false });
  });

  it("sends the agent nothing: it is told at the start of its next turn", async () => {
    const user = setupUser();
    const tell = vi.fn();
    patchKnowledgeInsight.mockResolvedValue(insight({ confirmed: true }));
    renderWithQueryClient(
      <TellAgentContext.Provider value={tell}>
        <ChatThreadContext.Provider value={9}>
          <ChatInsightCard insightId={7} text="x" />
        </ChatThreadContext.Provider>
      </TellAgentContext.Provider>,
    );

    await user.click(await screen.findByRole("button", { name: "Add" }));
    await act(async () => undefined);

    expect(tell).not.toHaveBeenCalled();
  });

  it("sends one request however fast the second click comes", async () => {
    const user = setupUser();
    let resolve: (value: unknown) => void = () => undefined;
    patchKnowledgeInsight.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    renderCard();

    await user.dblClick(await screen.findByRole("button", { name: "Add" }));
    await act(async () => resolve(insight({ confirmed: true })));

    expect(patchKnowledgeInsight).toHaveBeenCalledTimes(1);
  });

  it("says why a press failed", async () => {
    const user = setupUser();
    dismissKnowledgeInsight.mockRejectedValue(new Error("the box fell over"));
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    expect(await screen.findByTestId("chat-insight-error")).toHaveTextContent("the box fell over");
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeEnabled();
  });

  it("says an insight that no longer exists no longer exists", async () => {
    getKnowledgeInsight.mockRejectedValue(new Error("No insight 7"));
    renderCard();
    expect(await screen.findByText("This insight no longer exists.")).toBeInTheDocument();
  });

  describe("what it rests on and what it replaces", () => {
    const REST_CHANGED = {
      set_version_id: 21,
      label: "v1",
      outcome_then: "held",
      outcome_now: "failed",
      changed: true,
    } as const;
    const REST_SAME = {
      set_version_id: 22,
      label: "v2",
      outcome_then: "held",
      outcome_now: "held",
      changed: false,
    } as const;

    it("shows each version with its outcome now, and the old one beside a change", async () => {
      getKnowledgeInsight.mockResolvedValue(insight({ rests_on: [REST_SAME, REST_CHANGED] }));
      renderCard();

      const rests = await screen.findAllByTestId("rests-on-version");
      expect(rests.map((item) => item.getAttribute("data-changed"))).toEqual(["false", "true"]);
      expect(within(rests[0]).queryByTestId("rests-on-changed")).toBeNull();
      expect(rests[1]).toHaveTextContent("v1");
      expect(rests[1]).toHaveTextContent("Held");
      expect(rests[1]).toHaveTextContent("Failed");
      expect(within(rests[1]).getByTestId("rests-on-changed")).toHaveTextContent("changed since");
    });

    it("reads a cleared outcome as no outcome now", async () => {
      getKnowledgeInsight.mockResolvedValue(
        insight({ rests_on: [{ ...REST_CHANGED, outcome_now: null }] }),
      );
      renderCard();
      expect(await screen.findByTestId("rests-on")).toHaveTextContent("no outcome now");
    });

    it("shows nothing about versions for an insight that rests on shots alone", async () => {
      renderCard();
      await screen.findByTestId("chat-insight");
      expect(screen.queryByTestId("rests-on")).toBeNull();
    });

    it("shows the old insight above the new one and says adding deletes it", async () => {
      getKnowledgeInsight.mockResolvedValue(
        insight({ replaces_id: 5, replaces_text: "One click finer on the Niche." }),
      );
      renderCard();

      const replaces = await screen.findByTestId("chat-insight-replaces");
      expect(replaces).toHaveTextContent("Replaces an insight you added:");
      expect(replaces).toHaveTextContent("One click finer on the Niche.");
      const card = screen.getByTestId("chat-insight");
      expect(card).toHaveTextContent("Adding it deletes the old insight above.");
      // Above: the old text comes before the new text in the card.
      expect(
        replaces.compareDocumentPosition(screen.getByTestId("chat-insight-text")) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it("says it replaced and deleted the old insight once added", async () => {
      getKnowledgeInsight.mockResolvedValue(insight({ confirmed: true, replaced: "deleted" }));
      renderCard();

      const decided = await screen.findByTestId("chat-insight-decided");
      expect(decided).toHaveTextContent("It replaced and deleted the old insight.");
      expect(screen.queryByTestId("chat-insight-replaces")).toBeNull();
    });

    it("says the old one had already changed when adding deleted nothing", async () => {
      getKnowledgeInsight.mockResolvedValue(insight({ confirmed: true, replaced: "old_changed" }));
      renderCard();

      expect(await screen.findByTestId("chat-insight-decided")).toHaveTextContent(
        "had already changed, so nothing was deleted",
      );
    });

    it("says the old one is already gone while the new one still waits", async () => {
      getKnowledgeInsight.mockResolvedValue(insight({ replaced: "old_changed" }));
      renderCard();

      expect(await screen.findByTestId("chat-insight-waiting-note")).toHaveTextContent(
        "already gone, so adding it deletes nothing",
      );
      expect(screen.queryByTestId("chat-insight-replaces")).toBeNull();
    });

    it("adds a replacement with one press and sends it the conversation to refresh", async () => {
      const user = setupUser();
      patchKnowledgeInsight.mockResolvedValue(insight({ confirmed: true, replaced: "deleted" }));
      getKnowledgeInsight
        .mockResolvedValueOnce(insight({ replaces_id: 5, replaces_text: "Old." }))
        .mockResolvedValue(insight({ confirmed: true, replaced: "deleted" }));
      renderCard();

      await user.click(await screen.findByRole("button", { name: "Add" }));

      expect(patchKnowledgeInsight).toHaveBeenCalledTimes(1);
      expect(patchKnowledgeInsight).toHaveBeenCalledWith(7, { confirmed: true });
      expect(await screen.findByTestId("chat-insight-decided")).toHaveTextContent(
        "replaced and deleted the old insight",
      );
    });
  });
});
