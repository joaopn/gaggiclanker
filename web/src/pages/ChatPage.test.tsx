import { act, screen, waitFor, within } from "@testing-library/react";
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
  openChatThread,
  deleteChatThread,
  sendChatMessage,
  cancelChatRun,
  getSets,
  getSetProposals,
  acceptSetProposal,
} = vi.hoisted(() => ({
  getChatThreads: vi.fn(),
  getChatThread: vi.fn(),
  getChatTools: vi.fn(),
  createChatThread: vi.fn(),
  openChatThread: vi.fn(),
  deleteChatThread: vi.fn(),
  sendChatMessage: vi.fn(),
  cancelChatRun: vi.fn(),
  getSets: vi.fn(),
  getSetProposals: vi.fn(),
  acceptSetProposal: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getChatThreads,
  getChatThread,
  getChatTools,
  createChatThread,
  openChatThread,
  deleteChatThread,
  sendChatMessage,
  cancelChatRun,
  getSets,
  getSetProposals,
  acceptSetProposal,
}));

const THREAD = {
  id: 1,
  title: "Why is Guji sour?",
  set_id: 3,
  set_name: "Guji on the Niche",
  set_version_id: 30,
  set_version_no: 4,
  dead_end: false,
  message_count: 2,
  created_at: "2026-03-01T10:00:00.000Z",
  updated_at: "2026-03-01T10:01:00.000Z",
};

/** Another conversation, with whatever the test needs changed. */
function thread(over: Partial<typeof THREAD> & { id: number }) {
  return { ...THREAD, title: `Conversation ${over.id}`, ...over };
}

/** The two Sets the Sets list serves, in the order the API sorts them. */
const SETS = [
  { id: 3, name: "Guji on the Niche", current_version_id: 30, current_version_no: 4 },
  { id: 4, name: "Kenya AA on the Niche", current_version_id: 40, current_version_no: 1 },
];

/**
 * A folder's disclosure button, by the name it shows.
 *
 * Matched as a prefix: the button's accessible name carries the conversation
 * count after the label.
 */
function folder(name: string) {
  return screen.getByRole("button", {
    name: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`),
  });
}

/**
 * The region a folder's button names, open or closed.
 *
 * By id rather than by role: a closed folder's list is `hidden`, which is the
 * point — out of the accessibility tree and still in the document, so
 * `aria-controls` resolves to something a reader can reach.
 */
function region(name: string) {
  const id = folder(name).getAttribute("aria-controls") ?? "";
  const element = document.getElementById(id);
  if (!element) throw new Error(`aria-controls of "${name}" resolves to nothing`);
  return element;
}

/** Open a folder and hand back its region. */
async function opened(user: ReturnType<typeof setupUser>, name: string) {
  await user.click(folder(name));
  return region(name);
}

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
  openChatThread.mockResolvedValue({ ...THREAD, id: 5, title: "v4, argued" });
  deleteChatThread.mockResolvedValue({ deleted: true });
  sendChatMessage.mockResolvedValue({
    run: { ...DETAIL.runs[0], id: 9, status: "running" },
    message: DETAIL.messages[0],
  });
  cancelChatRun.mockResolvedValue({ ...DETAIL.runs[0], id: 9, status: "cancelled" });
  getSets.mockResolvedValue({ items: SETS });
  getSetProposals.mockResolvedValue({ items: [] });
});

describe("ChatPage folders", () => {
  it("labels a conversation with the version it is about, and mutes a dead end", async () => {
    getChatThreads.mockResolvedValue([
      thread({ id: 1, title: "the live one", set_version_no: 4 }),
      thread({ id: 2, title: "the abandoned one", set_version_no: 2, dead_end: true }),
    ]);
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?set=3"] });

    await screen.findByRole("button", { name: /^Guji on the Niche/ });
    const rows = within(region("Guji on the Niche")).getAllByTestId("thread-row");

    expect(rows[0]).toHaveTextContent("v4");
    expect(rows[0]).toHaveTextContent("the live one");
    expect(rows[0]).toHaveAttribute("data-dead-end", "no");
    // In words as well as in grey: what was argued there is not the line being
    // brewed any more.
    expect(rows[1]).toHaveAttribute("data-dead-end", "yes");
    expect(rows[1]).toHaveTextContent("dead end");
  });

  it("draws General and a folder per Set, including a Set nobody has asked about", async () => {
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^General/ });
    // Every non-archived Set, in the order the Sets list served them, so the
    // Set the machine is set up for comes first.
    const names = screen
      .getAllByRole("button")
      .filter((button) => button.hasAttribute("aria-expanded"))
      .map((button) => (button.textContent ?? "").replace(/\d+$/, ""));
    expect(names).toEqual(["General", "Guji on the Niche", "Kenya AA on the Niche"]);
    // Kenya has no conversation yet and is a folder all the same: an empty
    // folder with a New button is how you start one.
    expect(region("Kenya AA on the Niche")).toHaveTextContent("New");
  });

  it("files a conversation under its Set's folder and not under General", async () => {
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await screen.findByRole("button", { name: /^Guji on the Niche/ });
    expect(region("Guji on the Niche")).toHaveTextContent("Why is Guji sour?");
    expect(region("General")).not.toHaveTextContent("Why is Guji sour?");
    // Nor under another Set's folder: a folder holds its own Set's questions only.
    expect(region("Kenya AA on the Niche")).not.toHaveTextContent("Why is Guji sour?");
  });

  it("orders a folder's conversations newest first", async () => {
    getChatThreads.mockResolvedValue([
      thread({ id: 1, title: "the older one", updated_at: "2026-03-01T10:00:00.000Z" }),
      thread({ id: 2, title: "the newer one", updated_at: "2026-03-02T10:00:00.000Z" }),
    ]);
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?set=3"] });

    await screen.findByRole("button", { name: /^Guji on the Niche/ });
    const titles = within(region("Guji on the Niche"))
      .getAllByRole("listitem")
      .map((item) => item.textContent ?? "");
    expect(titles[0]).toContain("the newer one");
    expect(titles[1]).toContain("the older one");
  });

  it("puts a conversation whose Set is gone in Archived Sets, with no New", async () => {
    const user = setupUser();
    getChatThreads.mockResolvedValue([
      thread({ id: 7, title: "the finished bag", set_id: 99, set_name: "Finished Ethiopia" }),
    ]);
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^Archived Sets/ });
    const inside = await opened(user, "Archived Sets");

    expect(inside).toHaveTextContent("the finished bag");
    // The folder is named for the state, so the row carries the Set's name.
    expect(inside).toHaveTextContent("Finished Ethiopia");
    // The bag is finished; the questions about it are not wrong, but there is
    // no starting a new one. Asserted with the folder open, so the absence is
    // about the button and not about `hidden`.
    expect(within(inside).queryByRole("button", { name: /^New/ })).not.toBeInTheDocument();
  });

  it("shows no Archived Sets folder when nothing is in it", async () => {
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^General/ });
    expect(screen.queryByRole("button", { name: /^Archived Sets/ })).not.toBeInTheDocument();
  });

  it("is just General when there are no Sets at all", async () => {
    getSets.mockResolvedValue({ items: [] });
    getChatThreads.mockResolvedValue([]);
    renderWithQueryClient(<ChatPage />);

    expect(await screen.findByRole("button", { name: /^General/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Niche/ })).not.toBeInTheDocument();
  });

  it("creates in the folder's scope and selects what it made", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^Kenya AA on the Niche/ });
    const inside = await opened(user, "Kenya AA on the Niche");
    await user.click(
      within(inside).getByRole("button", { name: "New conversation in Kenya AA on the Niche" }),
    );

    await waitFor(() => expect(createChatThread).toHaveBeenCalledWith({ title: "", set_id: 4 }));
    // Selected: the transcript pane is now that conversation's.
    await waitFor(() => expect(getChatThread).toHaveBeenCalledWith(2));
  });

  it("creates with no Set from General", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />);

    await user.click(await screen.findByRole("button", { name: "New conversation in General" }));

    await waitFor(() => expect(createChatThread).toHaveBeenCalledWith({ title: "", set_id: null }));
  });

  it("is a disclosure: aria-expanded, a region that resolves, hidden when closed", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />);

    const button = await screen.findByRole("button", { name: /^Kenya AA on the Niche/ });
    const inside = region("Kenya AA on the Niche");
    // Closed by default — nothing selected and no `?set=` — and still rendered,
    // so `aria-controls` names something a reader can reach.
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(inside).toHaveAttribute("hidden");

    await user.click(button);

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(inside).not.toHaveAttribute("hidden");
  });

  it("opens General when nothing else says otherwise", async () => {
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^General/ });
    expect(folder("General")).toHaveAttribute("aria-expanded", "true");
    expect(folder("Guji on the Niche")).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the folder holding the selected conversation", async () => {
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await screen.findByRole("button", { name: /^Guji on the Niche/ });
    expect(folder("Guji on the Niche")).toHaveAttribute("aria-expanded", "true");
    expect(folder("General")).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the folder a ?set= link names", async () => {
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?set=4"] });

    await screen.findByRole("button", { name: /^Kenya AA on the Niche/ });
    expect(folder("Kenya AA on the Niche")).toHaveAttribute("aria-expanded", "true");
  });

  it("has no scope select any more", async () => {
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^General/ });
    // The control that used to sit under the list, and overflowed the card.
    expect(screen.queryByLabelText("Scope a new conversation")).toBeNull();
  });
});

describe("ChatPage", () => {
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

  it("follows a run that is already going when it loads", async () => {
    // What the New Set dialog's design path relies on: it sends the first
    // message, then comes here, and the answer is already being written.
    getChatThread.mockResolvedValue({
      ...DETAIL,
      runs: [...DETAIL.runs, { ...DETAIL.runs[0], id: 31, status: "running", finished_at: null }],
    });
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    expect(await screen.findByRole("button", { name: /stop/i })).toBeInTheDocument();
    await waitFor(() =>
      expect(useSse).toHaveBeenCalledWith(
        expect.stringContaining("/chat/runs/31/stream"),
        expect.any(Function),
      ),
    );
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
    const user = setupUser();
    getChatThreads.mockResolvedValue([]);
    renderWithQueryClient(<ChatPage />, {
      initialEntries: ["/chat?set=3&ask=How%20is%20Guji%20going%3F"],
    });

    expect(await screen.findByLabelText("Message")).toHaveValue("How is Guji going?");
    // The folder the question will land in is open, and the composer's card
    // says so — with the version it would land on, which is the Set's current.
    await screen.findByRole("button", { name: /^Guji on the Niche/ });
    expect(folder("Guji on the Niche")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("A new conversation about Guji on the Niche v4")).toBeInTheDocument();
    // A Set link on its own creates nothing until something is sent.
    expect(openChatThread).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /send/i }));

    // And the first question creates it in that Set, not as a general one.
    await waitFor(() => expect(createChatThread).toHaveBeenCalledWith({ title: "", set_id: 3 }));
  });

  it("says a first question with no scope is a general conversation", async () => {
    getChatThreads.mockResolvedValue([]);
    renderWithQueryClient(<ChatPage />);

    expect(await screen.findByText("General")).toBeInTheDocument();
  });

  it("opens or continues a version's conversation from a Discuss link", async () => {
    getChatThreads.mockResolvedValue([]);
    renderWithQueryClient(<ChatPage />, {
      initialEntries: ["/chat?set=3&version=30&ask=What%20now%3F"],
    });

    await waitFor(() => expect(openChatThread).toHaveBeenCalledWith(3, 30));
    // The room it opened is the one on screen, and the typed question survived
    // the round trip.
    await waitFor(() => expect(getChatThread).toHaveBeenCalledWith(5));
    expect(screen.getByLabelText("Message")).toHaveValue("What now?");
  });

  it("says which version the open conversation is about", async () => {
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    expect(await screen.findByText("About Guji on the Niche v4")).toBeInTheDocument();
  });

  it("asks for the tool list of the kind of conversation it is showing", async () => {
    getChatTools.mockResolvedValue({
      tools: [
        { name: "list_set_shots", permission: "read", description: "this Set's shots" },
        { name: "propose_set_version", permission: "propose", description: "a version" },
      ],
    });
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await screen.findByTestId("chat-transcript");
    await waitFor(() => expect(getChatTools).toHaveBeenCalledWith("set"));
    expect(screen.getByTestId("chat-tools")).toHaveTextContent("list_set_shots");
    expect(screen.getByTestId("chat-tools")).toHaveTextContent("It can see this Set only");
  });

  it("shows no tool list until it knows what kind of conversation it is", async () => {
    // The thread never arrives: the page is selecting one and does not yet
    // know whether it is a Set's or a general one.
    getChatThread.mockReturnValue(new Promise(() => {}));
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await screen.findByRole("button", { name: /^General/ });

    expect(screen.queryByTestId("chat-tools")).not.toBeInTheDocument();
    // And it did not guess: no list was asked for at all.
    expect(getChatTools).not.toHaveBeenCalled();
  });

  it("asks for the general tool list when nothing is selected", async () => {
    getChatThreads.mockResolvedValue([]);
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^General/ });
    await waitFor(() => expect(getChatTools).toHaveBeenCalledWith("general"));
  });

  it("deletes a conversation from inside its folder", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await user.click(await screen.findByRole("button", { name: /delete why is guji sour/i }));

    await waitFor(() => expect(deleteChatThread).toHaveBeenCalledWith(1));
  });
});

describe("ChatPage, a Set being designed", () => {
  const DESIGNING = [
    ...SETS,
    {
      id: 6,
      name: "Guji on the DF64",
      designing: true,
      current_version_id: 60,
      current_version_no: 1,
    },
  ];
  const DESIGN_THREAD = thread({
    id: 12,
    title: "Help me design this Set.",
    set_id: 6,
    set_name: "Guji on the DF64",
    set_version_id: 60,
    set_version_no: 1,
  });

  it("badges the folder of a Set being designed, and no other", async () => {
    getSets.mockResolvedValue({ items: DESIGNING });
    renderWithQueryClient(<ChatPage />);

    await screen.findByRole("button", { name: /^Guji on the DF64/ });
    expect(within(folder("Guji on the DF64")).getByTestId("set-designing")).toHaveTextContent(
      "Designing",
    );
    expect(within(folder("Guji on the Niche")).queryByTestId("set-designing")).toBeNull();
    expect(within(folder("General")).queryByTestId("set-designing")).toBeNull();
  });

  it("asks for the design tools for a conversation on a Set being designed", async () => {
    getSets.mockResolvedValue({ items: DESIGNING });
    getChatThreads.mockResolvedValue([DESIGN_THREAD]);
    getChatThread.mockResolvedValue({ ...DETAIL, thread: DESIGN_THREAD });
    getChatTools.mockResolvedValue({
      tools: [{ name: "propose_initial_recipe", permission: "propose", description: "v1" }],
    });
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=12"] });

    await waitFor(() => expect(getChatTools).toHaveBeenCalledWith("set", { designing: true }));
    // Only that list: the ordinary Set list would be tools the agent does not have.
    expect(getChatTools).not.toHaveBeenCalledWith("set");
    expect(await screen.findByTestId("chat-tools")).toHaveTextContent("propose_initial_recipe");
    expect(screen.getByTestId("chat-tools")).toHaveTextContent("designing this Set's first recipe");
  });

  it("asks without it for a Set that has its recipe", async () => {
    getSets.mockResolvedValue({ items: DESIGNING });
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    await waitFor(() => expect(getChatTools).toHaveBeenCalledWith("set"));
    expect(getChatTools).not.toHaveBeenCalledWith("set", { designing: true });
  });

  it("switches to the Set tools once the Sets list says the design is over", async () => {
    getSets.mockResolvedValue({ items: DESIGNING });
    getChatThreads.mockResolvedValue([DESIGN_THREAD]);
    getChatThread.mockResolvedValue({ ...DETAIL, thread: DESIGN_THREAD });
    const { queryClient } = renderWithQueryClient(<ChatPage />, {
      initialEntries: ["/chat?thread=12"],
    });
    await waitFor(() => expect(getChatTools).toHaveBeenCalledWith("set", { designing: true }));

    // Accepting the first recipe clears the flag and invalidates the list.
    getSets.mockResolvedValue({
      items: DESIGNING.map((row) => (row.id === 6 ? { ...row, designing: false } : row)),
    });
    await queryClient.invalidateQueries({ queryKey: ["sets", "list"] });

    await waitFor(() => expect(getChatTools).toHaveBeenCalledWith("set"));
  });

  it("waits for the Sets before asking which tools a Set conversation has", async () => {
    getSets.mockReturnValue(new Promise(() => {}));
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    // The conversation is on screen, so the page knows it is a Set's: only the
    // Sets list, which says whether that Set is being designed, is missing.
    expect(await screen.findByText("Grind two clicks finer.")).toBeInTheDocument();
    expect(getChatTools).not.toHaveBeenCalled();
    expect(screen.queryByTestId("chat-tools")).not.toBeInTheDocument();
  });
});

/**
 * A card the agent proposed, answered from inside the conversation.
 *
 * The accept route knows nothing about chats, so without this the agent went on
 * as if the card were still waiting, and nobody told the person that the new
 * version is brewed and analysed in a new conversation. The page sends the
 * button's message as the next turn, and holds it while an answer is still
 * being written.
 */
/** The event handler the page gave the (stubbed) stream of one run, latest first. */
function streamHandler(runId: number): (message: { data: unknown }) => void {
  const calls = [...useSse.mock.calls].reverse();
  const call = calls.find((args) => String(args[0]).includes(`/chat/runs/${runId}/stream`));
  if (!call) throw new Error(`the page never followed run ${runId}`);
  return call[1] as (message: { data: unknown }) => void;
}

describe("ChatPage, accepting a card in the conversation", () => {
  const PROPOSED = {
    ...DETAIL,
    messages: [
      ...DETAIL.messages,
      {
        ...DETAIL.messages[1],
        id: 3,
        content: "",
        tool_calls: [{ id: "c1", name: "propose_set_version", arguments: {} }],
      },
      {
        ...DETAIL.messages[1],
        id: 4,
        role: "tool",
        content: "",
        usage: null,
        tool_results: [
          {
            id: "c1",
            name: "propose_set_version",
            ok: true,
            content: JSON.stringify({ proposal_id: 5, set_id: 3, changed: ["the dose"] }),
          },
        ],
      },
    ],
  };
  const WAITING = {
    id: 5,
    set_id: 3,
    kind: "change",
    thread_id: 1,
    base_version_id: 30,
    base_version_no: 4,
    base_is_current: true,
    changes: [
      { field: "dose_g", label: "Dose", before: "18 g", after: "18.5 g", from_profile: false },
    ],
    changed: ["the dose"],
    combined_reason: "",
    reason: "Half a gram more.",
    prediction: "Compared to v4, a touch more body.",
    compares_to_version_id: 30,
    compares_to_version_no: 4,
    status: "proposed",
    readable: true,
    draft_id: null,
    decline_note: "",
    resulting_version_id: null,
    resulting_version_no: null,
    created_at: "2026-03-01T10:00:10.000Z",
    decided_at: null,
  };

  beforeEach(() => {
    getChatThread.mockResolvedValue(PROPOSED);
    getSetProposals.mockResolvedValue({ items: [WAITING] });
    acceptSetProposal.mockResolvedValue({
      proposal: { ...WAITING, status: "accepted", resulting_version_no: 5 },
      version: { version_no: 5 },
    });
  });

  it("tells the agent what was accepted, and follows its answer", async () => {
    const user = setupUser();
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    const card = await screen.findByTestId("proposal-card");
    await user.click(within(card).getByRole("button", { name: /Accept/ }));

    await waitFor(() => expect(acceptSetProposal).toHaveBeenCalledWith(3, 5));
    await waitFor(() =>
      expect(sendChatMessage).toHaveBeenCalledWith(
        1,
        "Accepted: your proposed change is now version 5 of this Set.",
      ),
    );
    expect(sendChatMessage).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("holds the message until the answer being written has finished", async () => {
    const user = setupUser();
    getChatThread.mockResolvedValue({
      ...PROPOSED,
      runs: [
        ...PROPOSED.runs,
        { ...PROPOSED.runs[0], id: 31, status: "running", finished_at: null },
      ],
    });
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    const card = await screen.findByTestId("proposal-card");
    await screen.findByRole("button", { name: /stop/i });
    await user.click(within(card).getByRole("button", { name: /Accept/ }));
    await waitFor(() => expect(acceptSetProposal).toHaveBeenCalled());
    // Give the page every chance to send it early.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(sendChatMessage).not.toHaveBeenCalled();

    // The run in flight ends; the stored transcript no longer has it running.
    getChatThread.mockResolvedValue(PROPOSED);
    const onEvent = streamHandler(31);
    act(() => onEvent({ data: { seq: 1, kind: "completed" } }));

    await waitFor(() =>
      expect(sendChatMessage).toHaveBeenCalledWith(
        1,
        "Accepted: your proposed change is now version 5 of this Set.",
      ),
    );

    // And only once, however many times the run it started ends.
    const followed = streamHandler(9);
    act(() => followed({ data: { seq: 1, kind: "completed" } }));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(sendChatMessage).toHaveBeenCalledTimes(1);
  });

  it("keeps it for its own conversation when the person moves to another", async () => {
    const user = setupUser();
    const running = {
      ...PROPOSED,
      runs: [
        ...PROPOSED.runs,
        { ...PROPOSED.runs[0], id: 31, status: "running", finished_at: null },
      ],
    };
    const other = thread({ id: 2 });
    getChatThreads.mockResolvedValue([THREAD, other]);
    getChatThread.mockImplementation(async (id: number) =>
      id === 2 ? { ...DETAIL, thread: other, messages: [], runs: [] } : running,
    );
    renderWithQueryClient(<ChatPage />, { initialEntries: ["/chat?thread=1"] });

    const card = await screen.findByTestId("proposal-card");
    await screen.findByRole("button", { name: /stop/i });
    await user.click(within(card).getByRole("button", { name: /Accept/ }));
    await waitFor(() => expect(acceptSetProposal).toHaveBeenCalled());

    // Somewhere else, nothing is running there, and the message is not its.
    await user.click(await screen.findByText("Conversation 2"));
    await waitFor(() => expect(screen.queryByTestId("proposal-card")).not.toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(sendChatMessage).not.toHaveBeenCalled();

    // Back, with the answer that was being written now finished.
    getChatThread.mockImplementation(async (id: number) =>
      id === 2 ? { ...DETAIL, thread: other, messages: [], runs: [] } : PROPOSED,
    );
    await user.click(screen.getByText("Why is Guji sour?"));

    await waitFor(() =>
      expect(sendChatMessage).toHaveBeenCalledWith(
        1,
        "Accepted: your proposed change is now version 5 of this Set.",
      ),
    );
    expect(sendChatMessage).toHaveBeenCalledTimes(1);
  });
});
