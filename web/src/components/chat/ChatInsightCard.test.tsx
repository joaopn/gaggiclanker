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

  it("says when it was learned before versions were recorded", async () => {
    getKnowledgeInsight.mockResolvedValue(
      insight({ set_version_id: null, set_version_label: null }),
    );
    renderCard();
    expect(await screen.findByTestId("chat-insight-about")).toHaveTextContent(
      "learned before versions were recorded",
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
});
