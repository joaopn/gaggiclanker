import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@/api/types";
import { ChatTranscript } from "@/components/chat/ChatTranscript";
import { ChatRecipeContext, ChatThreadContext } from "@/components/chat/tellAgent";
import { knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { createKnowledgeInsight, getScopeOptions, getScopeReach } = vi.hoisted(() => ({
  createKnowledgeInsight: vi.fn(),
  getScopeOptions: vi.fn(),
  getScopeReach: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  createKnowledgeInsight,
  getScopeOptions,
  getScopeReach,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

function message(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: 1,
    thread_id: 9,
    run_id: null,
    role: "user",
    content: "",
    tool_calls: [],
    tool_results: [],
    usage: null,
    created_at: "2026-11-08T10:00:00.000Z",
    saved_insights: [],
    ...overrides,
  };
}

const SAVED_ID = 32;

const MESSAGES: ChatMessage[] = [
  message({ id: 1, content: "never suggest going finer than 8 on the Niche, it chokes" }),
  message({ id: 2, role: "assistant", content: "Noted: I will not go finer than 8." }),
  message({ id: 3, content: "thanks" }),
];

beforeEach(() => {
  vi.clearAllMocks();
  getScopeOptions.mockResolvedValue({
    recipe: [{ key: "grinder_id", value: 2, label: "grinder Niche Zero" }],
    beans: [],
    grinders: [],
    roast_levels: [],
    processes: [],
    origins: [],
  });
  getScopeReach.mockResolvedValue({ recipes: 5 });
  createKnowledgeInsight.mockResolvedValue(knowledgeInsight({ id: SAVED_ID, confirmed: true }));
});

function renderTranscript(messages = MESSAGES) {
  return renderWithQueryClient(
    <ChatThreadContext.Provider value={9}>
      <ChatRecipeContext.Provider value={3}>
        <ChatTranscript messages={messages} runs={[]} permissions={{}} />
      </ChatRecipeContext.Provider>
    </ChatThreadContext.Provider>,
  );
}

describe("Save as general insight", () => {
  it("sits under each message of the person's, and not under the agent's", () => {
    renderTranscript();

    expect(screen.getAllByTestId("save-as-general")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Save as general insight" })).toHaveLength(2);
  });

  it("is not offered under a card's own turn, which is the button's sentence and not theirs", () => {
    renderTranscript([
      message({ id: 1, content: "Declined: too cold, try 91 degrees" }),
      message({ id: 2, content: "Approved: Turbo, a new profile." }),
      message({ id: 3, content: "Accepted: your proposed change is now v2 of this Recipe." }),
      message({ id: 4, content: "always rinse the basket" }),
    ]);

    // Four user messages; only the person's own words carry the control.
    expect(screen.getAllByTestId("save-as-general")).toHaveLength(1);
  });

  it("is quiet until the message is hovered or focused, and always visible on touch", () => {
    renderTranscript();

    const control = screen.getAllByTestId("save-as-general")[0];
    expect(control.className).toContain("opacity-0");
    expect(control.className).toContain("group-hover:opacity-100");
    expect(control.className).toContain("group-focus-within:opacity-100");
    expect(control.className).toContain("focus-visible:opacity-100");
    expect(control.className).toContain("[@media(hover:none)]:opacity-100");
    // Reachable by keyboard: quiet is not hidden.
    expect(control).not.toHaveAttribute("tabindex", "-1");
    expect(control).toBeEnabled();
  });

  it("opens the picker inline with the whole message as the text", async () => {
    const user = setupUser();
    renderTranscript();

    await user.click(screen.getAllByTestId("save-as-general")[0]);

    const panel = screen.getByTestId("save-as-general-panel");
    expect(within(panel).getByTestId("save-as-general-text")).toHaveValue(
      "never suggest going finer than 8 on the Niche, it chokes",
    );
    expect(
      await within(panel).findByRole("checkbox", { name: "grinder Niche Zero" }),
    ).not.toBeChecked();
    await waitFor(() => expect(panel).toHaveTextContent("Nothing ticked: every Recipe."));
    expect(panel).toHaveTextContent("Matches 5 Recipes now.");
    expect(within(panel).getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(within(panel).getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("Cancel closes it and saves nothing", async () => {
    const user = setupUser();
    renderTranscript();

    await user.click(screen.getAllByTestId("save-as-general")[0]);
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByTestId("save-as-general-panel")).not.toBeInTheDocument();
    expect(createKnowledgeInsight).not.toHaveBeenCalled();
  });

  it("Save posts the text, the scope and the message, confirmed, once; then the message says it is saved and the control goes", async () => {
    const user = setupUser();
    renderTranscript();

    await user.click(screen.getAllByTestId("save-as-general")[0]);
    await user.click(await screen.findByRole("checkbox", { name: "grinder Niche Zero" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(createKnowledgeInsight).toHaveBeenCalledTimes(1));
    expect(createKnowledgeInsight).toHaveBeenCalledWith({
      text: "never suggest going finer than 8 on the Niche, it chokes",
      scope: { grinder_id: 2 },
      from_message_id: 1,
      confirmed: true,
    });
    const saved = await screen.findByTestId("saved-as-general");
    expect(saved).toHaveTextContent(`Saved as general insight #${SAVED_ID} · Knowledge page →`);
    expect(within(saved).getByRole("link", { name: "Knowledge page →" })).toHaveAttribute(
      "href",
      "/knowledge?tab=insights",
    );
    // That message's control is gone; the other message keeps its own.
    expect(screen.getAllByTestId("save-as-general")).toHaveLength(1);
    expect(screen.queryByTestId("save-as-general-panel")).not.toBeInTheDocument();
  });

  it("reads 'saved' from the thread, so it survives a reload and shows no control", () => {
    renderTranscript([
      message({ id: 1, content: "always rinse", saved_insights: [{ id: 17 }] }),
      message({ id: 3, content: "thanks" }),
    ]);

    expect(screen.getByTestId("saved-as-general")).toHaveTextContent(
      "Saved as general insight #17 · Knowledge page →",
    );
    expect(screen.getAllByTestId("save-as-general")).toHaveLength(1);
  });

  it("keeps the panel open and says nothing was saved when the save fails", async () => {
    const user = setupUser();
    createKnowledgeInsight.mockRejectedValue(new Error("The disk is full"));
    renderTranscript();

    await user.click(screen.getAllByTestId("save-as-general")[0]);
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(createKnowledgeInsight).toHaveBeenCalled());
    expect(screen.getByTestId("save-as-general-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("saved-as-general")).not.toBeInTheDocument();
  });

  it("refuses a message longer than a general insight can be, in words", async () => {
    const user = setupUser();
    renderTranscript([message({ id: 1, content: "x".repeat(2001) })]);

    await user.click(screen.getByTestId("save-as-general"));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "holds up to 2000 characters; this is 2001",
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("Save is sent once", () => {
  it("a double click on Save writes one insight, whatever React has re-rendered by then", async () => {
    const user = setupUser();
    let release: (value: unknown) => void = () => {};
    createKnowledgeInsight.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    renderTranscript();

    await user.click(screen.getAllByTestId("save-as-general")[0]);
    const save = await screen.findByRole("button", { name: "Save" });
    await user.dblClick(save);
    await user.click(save);

    expect(createKnowledgeInsight).toHaveBeenCalledTimes(1);
    release(knowledgeInsight({ id: SAVED_ID, confirmed: true }));
    expect(await screen.findByTestId("saved-as-general")).toBeInTheDocument();
  });
});
