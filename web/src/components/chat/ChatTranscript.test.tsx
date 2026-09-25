import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@/api/types";
import { ChatTranscript, toTurns } from "@/components/chat/ChatTranscript";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { designProposal, proposal } from "@/test/setsFixtures";

const { getSetProposals } = vi.hoisted(() => ({ getSetProposals: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSetProposals,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getSetProposals.mockResolvedValue({ items: [] });
});

/**
 * The transcript is where the feature's honesty lives: a reader has to be able
 * to see what was called, whether anything was written, and follow every
 * citation. These tests assert on exactly that.
 */

const PERMISSIONS = {
  get_shot: "read",
  query_shots: "read",
  propose_set_version: "propose",
  propose_initial_recipe: "propose",
  record_insight: "propose",
};

function message(overrides: Partial<ChatMessage> & { id: number }): ChatMessage {
  return {
    thread_id: 1,
    run_id: 1,
    role: "assistant",
    content: "",
    tool_calls: [],
    tool_results: [],
    usage: null,
    created_at: "2026-03-01T10:00:00.000Z",
    ...overrides,
  } as ChatMessage;
}

const WITH_TOOLS: ChatMessage[] = [
  message({ id: 1, role: "user", content: "Why was shot 129 sour?" }),
  message({
    id: 2,
    role: "assistant",
    tool_calls: [{ id: "c1", name: "get_shot", arguments: { shot_id: 129 } }],
  }),
  message({
    id: 3,
    role: "tool",
    tool_results: [{ id: "c1", name: "get_shot", ok: true, content: '{"shot": {"shot_id": 129}}' }],
  }),
  message({
    id: 4,
    role: "assistant",
    content: "It ran fast. See ESPRESSO_TASTING_GUIDE#sour-vs-bitter, and compare shot 130.",
  }),
];

describe("toTurns", () => {
  it("folds a tool round into the answer it produced", () => {
    const turns = toTurns(WITH_TOOLS);

    expect(turns.map((turn) => turn.role)).toEqual(["user", "assistant"]);
    expect(turns[1].trace).toHaveLength(1);
    expect(turns[1].trace[0]).toMatchObject({ name: "get_shot", ok: true });
  });

  it("keeps calls that never got an answer, so a failed run is not silent", () => {
    const turns = toTurns(WITH_TOOLS.slice(0, 3));

    expect(turns.at(-1)?.trace).toHaveLength(1);
    expect(turns.at(-1)?.content).toBe("");
  });
});

describe("ChatTranscript", () => {
  it("renders the question, the answer and the tool trace", () => {
    renderWithQueryClient(
      <ChatTranscript messages={WITH_TOOLS} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(screen.getByText("Why was shot 129 sour?")).toBeInTheDocument();
    expect(screen.getByTestId("tool-trace")).toHaveTextContent("get_shot");
  });

  it("links a cited shot and a cited knowledge passage", () => {
    renderWithQueryClient(
      <ChatTranscript messages={WITH_TOOLS} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(screen.getByRole("link", { name: "shot 130" })).toHaveAttribute("href", "/shots/130");
    expect(
      screen.getByRole("link", { name: "ESPRESSO_TASTING_GUIDE#sour-vs-bitter" }),
    ).toHaveAttribute(
      "href",
      "/knowledge?tab=docs&doc=ESPRESSO_TASTING_GUIDE&chunk=ESPRESSO_TASTING_GUIDE%23sour-vs-bitter",
    );
  });

  it("shows a call's input and output only once it is expanded", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <ChatTranscript messages={WITH_TOOLS} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(screen.queryByText("Input")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /get_shot/ }));

    // Both halves: the arguments the model chose, and what came back. Either
    // alone leaves the answer half-checkable.
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("Output")).toBeInTheDocument();
    expect(screen.getAllByText(/"shot_id": 129/).length).toBeGreaterThan(0);
  });

  function proposed(): ChatMessage[] {
    return [
      message({ id: 1, role: "user", content: "what should I change?" }),
      message({
        id: 2,
        role: "assistant",
        tool_calls: [{ id: "c1", name: "propose_set_version", arguments: { reason: "finer" } }],
      }),
      message({
        id: 3,
        role: "tool",
        tool_results: [
          {
            id: "c1",
            name: "propose_set_version",
            ok: true,
            content: JSON.stringify({
              proposal_id: 5,
              set_id: 3,
              status: "proposed",
              changed: ["the dose"],
              change_summary: "Dose 18 g → 18.5 g",
            }),
          },
        ],
      }),
      message({ id: 4, role: "assistant", content: "Half a gram more — accept it if you agree." }),
    ];
  }

  it("marks a propose call as a proposal without expanding anything", () => {
    renderWithQueryClient(
      <ChatTranscript messages={proposed()} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(screen.getByTestId("tool-trace")).toHaveTextContent("proposal");
    // Before the row is read back, the transcript's own summary is the card.
    const card = screen.getByTestId("propose-card-set_version");
    expect(card).toHaveTextContent("A change to this Set: the dose");
    expect(card).toHaveTextContent("Dose 18 g → 18.5 g");
  });

  it("offers the decision on the card once the proposal is read back", async () => {
    getSetProposals.mockResolvedValue({ items: [proposal()] });

    renderWithQueryClient(
      <ChatTranscript messages={proposed()} runs={[]} permissions={PERMISSIONS} />,
    );

    // Live rather than from the transcript: a conversation read a week later
    // must not offer to accept something that was accepted on the Set page.
    const card = await screen.findByTestId("proposal-card");
    expect(within(card).getByRole("button", { name: /Accept/ })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Decline/ })).toBeInTheDocument();
  });

  it("says how a proposal was answered instead of offering it again", async () => {
    getSetProposals.mockResolvedValue({
      items: [proposal({ status: "declined", decline_note: "The dose is not the problem." })],
    });

    renderWithQueryClient(
      <ChatTranscript messages={proposed()} runs={[]} permissions={PERMISSIONS} />,
    );

    const decided = await screen.findByTestId("proposal-decided");
    expect(decided).toHaveTextContent("The dose is not the problem.");
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
  });

  function designed(): ChatMessage[] {
    return [
      message({ id: 1, role: "user", content: "Help me design this Set." }),
      message({
        id: 2,
        role: "assistant",
        tool_calls: [
          { id: "c1", name: "propose_initial_recipe", arguments: { reason: "a bloom" } },
        ],
      }),
      message({
        id: 3,
        role: "tool",
        tool_results: [
          {
            id: "c1",
            name: "propose_initial_recipe",
            ok: true,
            content: JSON.stringify({
              proposal_id: 8,
              set_id: 6,
              draft_id: 14,
              kind: "design",
              status: "proposed",
              recipe: {
                profile_version_id: 70,
                profile_label: "Guji Bloom",
                grind_setting: "20",
                grind_value: 20,
                dose_g: 18,
                target_yield_g: 40,
              },
              note: "Nothing exists yet.",
            }),
          },
        ],
      }),
    ];
  }

  it("turns a proposed first recipe into the live card, read from the Set's proposals", async () => {
    getSetProposals.mockResolvedValue({ items: [designProposal()] });

    renderWithQueryClient(
      <ChatTranscript messages={designed()} runs={[]} permissions={PERMISSIONS} />,
    );

    // Before the row is read back, the tool's own figures stand in for it.
    const holder = screen.getByTestId("propose-card-initial_recipe");
    expect(holder).toHaveTextContent("The first recipe for this Set");
    expect(holder).toHaveTextContent("Guji Bloom · grind 20 · 18 g in · 40 g out");

    // Then the row itself, with the buttons that answer it: not a snapshot.
    const card = await within(holder).findByTestId("proposal-card");
    expect(getSetProposals).toHaveBeenCalledWith(6);
    expect(card).toHaveAttribute("data-kind", "design");
    expect(within(card).getByTestId("proposal-recipe")).toHaveTextContent("Guji Bloom");
    expect(within(card).getByRole("button", { name: /Accept/ })).toBeInTheDocument();
  });

  it("says how a first recipe was answered when the conversation is read again", async () => {
    getSetProposals.mockResolvedValue({
      items: [designProposal({ status: "accepted", changes: [], resulting_version_no: 1 })],
    });

    renderWithQueryClient(
      <ChatTranscript messages={designed()} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(await screen.findByTestId("proposal-decided")).toHaveTextContent("version 1 is set");
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
  });

  it("points a drafted profile at the staging section of the profiles page", () => {
    // The queue is a section rather than a page, so the link carries the
    // anchor: landing at the top of the profiles page and leaving the reader
    // to find the draft they were just told about is half a link.
    const messages: ChatMessage[] = [
      message({ id: 1, role: "user", content: "draft me a softer ramp" }),
      message({
        id: 2,
        role: "assistant",
        tool_calls: [{ id: "c1", name: "draft_profile", arguments: { notes: "softer" } }],
      }),
      message({
        id: 3,
        role: "tool",
        tool_results: [
          {
            id: "c1",
            name: "draft_profile",
            ok: true,
            content: JSON.stringify({ draft_id: 12, change_summary: "Softer ramp." }),
          },
        ],
      }),
    ];

    renderWithQueryClient(
      <ChatTranscript messages={messages} runs={[]} permissions={PERMISSIONS} />,
    );

    const card = screen.getByTestId("propose-card-draft");
    expect(within(card).getByRole("link", { name: /Profile draft #12/ })).toHaveAttribute(
      "href",
      "/profiles#staged",
    );
  });

  it("says an insight is unconfirmed, because that is the whole rule", () => {
    const messages: ChatMessage[] = [
      message({
        id: 1,
        role: "assistant",
        tool_calls: [{ id: "c1", name: "record_insight", arguments: { text: "finer" } }],
      }),
      message({
        id: 2,
        role: "tool",
        tool_results: [
          {
            id: "c1",
            name: "record_insight",
            ok: true,
            content: JSON.stringify({ insight_id: 7, text: "Two clicks finer on the Niche." }),
          },
        ],
      }),
      message({ id: 3, role: "assistant", content: "Noted." }),
    ];

    renderWithQueryClient(
      <ChatTranscript messages={messages} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(screen.getByTestId("propose-card-insight")).toHaveTextContent("unconfirmed");
  });

  it("streams a live answer under the stored ones", () => {
    renderWithQueryClient(
      <ChatTranscript
        messages={WITH_TOOLS.slice(0, 1)}
        runs={[]}
        permissions={PERMISSIONS}
        live={{ text: "Looking at", trace: [], status: "running" }}
      />,
    );

    expect(screen.getByTestId("live-answer")).toHaveTextContent("Looking at");
  });

  it("shows a spinner while a live run has produced no text yet", () => {
    renderWithQueryClient(
      <ChatTranscript
        messages={[]}
        runs={[]}
        permissions={PERMISSIONS}
        live={{ text: "", trace: [], status: "running" }}
      />,
    );

    expect(screen.getByTestId("live-answer")).toHaveTextContent("Thinking");
  });

  it("shows a failed run's error rather than an empty answer", () => {
    renderWithQueryClient(
      <ChatTranscript
        messages={WITH_TOOLS.slice(0, 1)}
        runs={[
          {
            id: 1,
            thread_id: 1,
            status: "failed",
            provider: "anthropic",
            model: "",
            error: "rate_limited: slow down",
            usage: null,
            tool_rounds: 0,
            tool_calls: 0,
            started_at: "",
            finished_at: null,
          },
        ]}
        permissions={PERMISSIONS}
      />,
    );

    expect(screen.getByText(/rate_limited: slow down/)).toBeInTheDocument();
  });
});
