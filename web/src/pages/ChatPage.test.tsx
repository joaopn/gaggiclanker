import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "@/pages/ChatPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

// The stream is the one thing a jsdom test cannot have: `useSse` opens a real
// fetch and reads a body that never ends. Stubbed at the hook, so the page's
// own wiring — which run it follows, and when it stops — is still under test.
const { useSse } = vi.hoisted(() => ({ useSse: vi.fn() }));
vi.mock("@/hooks/useSse", () => ({ useSse }));

const {
  getChatThreads,
  getChatThread,
  getChatTools,
  createChatThread,
  deleteChatThread,
  sendChatMessage,
  cancelChatRun,
  getSets,
} = vi.hoisted(() => ({
  getChatThreads: vi.fn(),
  getChatThread: vi.fn(),
  getChatTools: vi.fn(),
  createChatThread: vi.fn(),
  deleteChatThread: vi.fn(),
  sendChatMessage: vi.fn(),
  cancelChatRun: vi.fn(),
  getSets: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getChatThreads,
  getChatThread,
  getChatTools,
  createChatThread,
  deleteChatThread,
  sendChatMessage,
  cancelChatRun,
  getSets,
}));

const THREAD = {
  id: 1,
  title: "Why is Guji sour?",
  set_id: 3,
  set_name: "Guji on the Niche",
  message_count: 2,
  created_at: "2026-03-01T10:00:00.000Z",
  updated_at: "2026-03-01T10:01:00.000Z",
};

const DETAIL = {
  thread: THREAD,
  messages: [
    {
      id: 1,
      thread_id: 1,
      run_id: 1,
      role: "user",
      content: "Why is Guji sour?",
      tool_calls: [],
      tool_results: [],
      usage: null,
      created_at: "2026-03-01T10:00:00.000Z",
    },
    {
      id: 2,
      thread_id: 1,
      run_id: 1,
      role: "assistant",
      content: "Grind two clicks finer.",
      tool_calls: [],
      tool_results: [],
      usage: { prompt_tokens: 1200, completion_tokens: 80 },
      created_at: "2026-03-01T10:00:20.000Z",
    },
  ],
  runs: [
    {
      id: 1,
      thread_id: 1,
      status: "ok",
      provider: "anthropic",
      model: "claude",
      error: null,
      usage: { prompt_tokens: 1200, completion_tokens: 80 },
      tool_rounds: 0,
      tool_calls: 0,
      started_at: "2026-03-01T10:00:00.000Z",
      finished_at: "2026-03-01T10:00:20.000Z",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  useSse.mockReturnValue({ connected: true });
  getChatThreads.mockResolvedValue([THREAD]);
  getChatThread.mockResolvedValue(DETAIL);
  getChatTools.mockResolvedValue({
    tools: [
      { name: "get_shot", permission: "read", description: "one shot" },
      { name: "propose_set_version", permission: "propose", description: "a version" },
    ],
  });
  createChatThread.mockResolvedValue({ ...THREAD, id: 2, title: "", message_count: 0 });
  deleteChatThread.mockResolvedValue({ deleted: true });
  sendChatMessage.mockResolvedValue({
    run: { ...DETAIL.runs[0], id: 9, status: "running" },
    message: DETAIL.messages[0],
  });
  cancelChatRun.mockResolvedValue({ ...DETAIL.runs[0], id: 9, status: "cancelled" });
  getSets.mockResolvedValue({ items: [{ id: 3, name: "Guji on the Niche" }] });
});

describe("ChatPage", () => {
  it("lists conversations with the Set each one is about", async () => {
    renderWithQueryClient(<ChatPage />);

    const list = await screen.findByRole("list", { name: "Conversations" });
    expect(await within(list).findByText("Why is Guji sour?")).toBeInTheDocument();
    // The scope badge: two threads with the same question and different Sets
    // get different answers, and nothing else on the row says which.
    expect(within(list).getByText("Guji on the Niche")).toBeInTheDocument();
  });

  it("opens a thread and shows its transcript and usage", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />);

    await user.click(await screen.findByText("Why is Guji sour?"));

    expect(await screen.findByTestId("chat-transcript")).toHaveTextContent(
      "Grind two clicks finer.",
    );
    expect(screen.getByTestId("chat-usage")).toHaveTextContent("1,200 in");
  });

  it("sends a question and follows the run it was given", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await screen.findByTestId("chat-transcript");
    await user.type(screen.getByLabelText("Message"), "what changed in v3?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledWith(1, "what changed in v3?"));
    // The composer is cleared and the button becomes Stop: the run is in flight.
    expect(await screen.findByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("creates a thread on the first question rather than refusing", async () => {
    const user = setupUser();
    getChatThreads.mockResolvedValue([]);
    renderWithQueryClient(<ChatPage />);

    await user.type(await screen.findByLabelText("Message"), "what is a 1:2 ratio?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(createChatThread).toHaveBeenCalled());
    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledWith(2, "what is a 1:2 ratio?"));
  });

  it("cancels the run in flight", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await screen.findByTestId("chat-transcript");
    await user.type(screen.getByLabelText("Message"), "go");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await user.click(await screen.findByRole("button", { name: /stop/i }));

    await waitFor(() => expect(cancelChatRun).toHaveBeenCalledWith(9));
  });

  it("prefills the composer and the scope from a Discuss in chat link", async () => {
    renderWithQueryClient(<ChatPage />, {
      initialEntries: ["/chat?set=3&ask=How%20is%20Guji%20going%3F"],
    });

    expect(await screen.findByLabelText("Message")).toHaveValue("How is Guji going?");
    await waitFor(() => expect(screen.getByLabelText("Scope a new conversation")).toHaveValue("3"));
  });

  it("deletes a conversation", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />);

    await user.click(await screen.findByRole("button", { name: /delete why is guji sour/i }));

    await waitFor(() => expect(deleteChatThread).toHaveBeenCalledWith(1));
  });
});
