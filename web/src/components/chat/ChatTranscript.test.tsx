import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, ChatRun } from "@/api/types";
import { ChatTranscript, toTurns } from "@/components/chat/ChatTranscript";
import { insightDeletion, knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { designProposal, outcomeProposal, proposal } from "@/test/setsFixtures";

const { getSetProposals, getOutcomeProposals, getKnowledgeInsight, getInsightDeletions } =
  vi.hoisted(() => ({
    getSetProposals: vi.fn(),
    getOutcomeProposals: vi.fn(),
    getKnowledgeInsight: vi.fn(),
    getInsightDeletions: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSetProposals,
  getOutcomeProposals,
  getKnowledgeInsight,
  getInsightDeletions,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getSetProposals.mockResolvedValue({ items: [] });
  getOutcomeProposals.mockResolvedValue({ items: [] });
  getInsightDeletions.mockResolvedValue({ items: [] });
  getKnowledgeInsight.mockResolvedValue(
    knowledgeInsight({
      id: 7,
      text: "Two clicks finer on the Niche.",
      set_id: 3,
      set_version_id: 22,
      set_version_label: "v2",
      general: false,
      scope: {},
      evidence_shot_ids: [4],
    }),
  );
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
  propose_outcome: "propose",
  record_insight: "propose",
  propose_insight_deletion: "propose",
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

describe("toTurns, as the runner stores a turn that has text beside its tool calls", () => {
  // One assistant message carries the text and the calls together; the tool
  // message with the results follows it.
  const STORED: ChatMessage[] = [
    message({ id: 1, role: "user", content: "How did v3 do?" }),
    message({
      id: 2,
      role: "assistant",
      content: "Grading v3 against its prediction.",
      tool_calls: [{ id: "c1", name: "get_shot", arguments: { shot_id: 129 } }],
    }),
    message({
      id: 3,
      role: "tool",
      tool_results: [{ id: "c1", name: "get_shot", ok: true, content: '{"shot": 129}' }],
    }),
    message({ id: 4, role: "assistant", content: "It held." }),
  ];

  it("pairs each result with the call of the message that made it", () => {
    const turns = toTurns(STORED);

    const withCall = turns.find((turn) => turn.trace.length > 0);
    expect(withCall?.content).toBe("Grading v3 against its prediction.");
    expect(withCall?.trace[0]).toMatchObject({
      name: "get_shot",
      ok: true,
      content: '{"shot": 129}',
    });
  });

  it("draws the card of a proposal made in such a message after a reload", async () => {
    getOutcomeProposals.mockResolvedValue({ items: [outcomeProposal()] });
    const messages: ChatMessage[] = [
      message({ id: 1, role: "user", content: "how did v2 do?" }),
      message({
        id: 2,
        role: "assistant",
        content: "Grading it now.",
        tool_calls: [{ id: "c1", name: "propose_outcome", arguments: { outcome: "partly_held" } }],
      }),
      message({
        id: 3,
        role: "tool",
        tool_results: [
          {
            id: "c1",
            name: "propose_outcome",
            ok: true,
            content: JSON.stringify({
              proposal_id: 11,
              set_id: 3,
              version: "v2",
              outcome: "partly_held",
              counted_shots: 3,
            }),
          },
        ],
      }),
    ];

    renderWithQueryClient(
      <ChatTranscript messages={messages} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(await screen.findByTestId("outcome-card")).toBeInTheDocument();
    expect(screen.queryByText("no result")).not.toBeInTheDocument();
  });
});

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
      items: [designProposal({ status: "accepted", changes: [], resulting_version_label: "v1" })],
    });

    renderWithQueryClient(
      <ChatTranscript messages={designed()} runs={[]} permissions={PERMISSIONS} />,
    );

    expect(await screen.findByTestId("proposal-decided")).toHaveTextContent("version 1 is set");
    expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
  });

  describe("a proposed grade", () => {
    function graded(): ChatMessage[] {
      return [
        message({ id: 1, role: "user", content: "how did v2 do?" }),
        message({
          id: 2,
          role: "assistant",
          tool_calls: [
            {
              id: "c1",
              name: "propose_outcome",
              arguments: { outcome: "partly_held", note: "time held; sourness did not move" },
            },
          ],
        }),
        message({
          id: 3,
          role: "tool",
          tool_results: [
            {
              id: "c1",
              name: "propose_outcome",
              ok: true,
              content: JSON.stringify({
                proposal_id: 11,
                set_id: 3,
                version: "v2",
                outcome: "partly_held",
                counted_shots: 3,
                status: "proposed",
              }),
            },
          ],
        }),
        message({ id: 4, role: "assistant", content: "Partly held." }),
      ];
    }

    it("marks the call as a proposal and draws the live card, read from the Set's grades", async () => {
      getOutcomeProposals.mockResolvedValue({ items: [outcomeProposal()] });

      renderWithQueryClient(
        <ChatTranscript messages={graded()} runs={[]} permissions={PERMISSIONS} />,
      );

      // Before the row is read back, the tool's own words stand in for it.
      const holder = screen.getByTestId("propose-card-outcome");
      expect(holder).toHaveTextContent("A grade for v2: partly held");
      const card = await within(holder).findByTestId("outcome-card");
      expect(getOutcomeProposals).toHaveBeenCalledWith(3);
      expect(within(card).getByRole("button", { name: /Accept as/ })).toBeInTheDocument();
      expect(screen.getByText("proposal")).toBeInTheDocument();
    });

    it("says how it was answered when the conversation is read again", async () => {
      getOutcomeProposals.mockResolvedValue({
        items: [outcomeProposal({ status: "accepted" })],
      });

      renderWithQueryClient(
        <ChatTranscript messages={graded()} runs={[]} permissions={PERMISSIONS} />,
      );

      expect(await screen.findByTestId("outcome-card-decided")).toHaveTextContent("Accepted");
      expect(screen.queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    });
  });

  describe("a proposed insight deletion", () => {
    function proposed(): ChatMessage[] {
      return [
        message({ id: 1, role: "user", content: "does the old one still hold?" }),
        message({
          id: 2,
          role: "assistant",
          tool_calls: [
            {
              id: "c1",
              name: "propose_insight_deletion",
              arguments: { insight_id: 7, reason: "The last two shots contradict it." },
            },
          ],
        }),
        message({
          id: 3,
          role: "tool",
          tool_results: [
            {
              id: "c1",
              name: "propose_insight_deletion",
              ok: true,
              content: JSON.stringify({
                proposal_id: 9,
                set_id: 3,
                insight_id: 7,
                insight_text: "Two clicks finer on the Niche.",
                status: "proposed",
              }),
            },
          ],
        }),
        message({ id: 4, role: "assistant", content: "I would drop it." }),
      ];
    }

    it("marks the call as a proposal and draws the live card with Delete and Keep", async () => {
      getInsightDeletions.mockResolvedValue({ items: [insightDeletion()] });

      renderWithQueryClient(
        <ChatTranscript messages={proposed()} runs={[]} permissions={PERMISSIONS} />,
      );

      // Before the row is read back, the tool's own words stand in for it.
      const holder = screen.getByTestId("propose-card-insight_deletion");
      expect(holder).toHaveTextContent("Two clicks finer on the Niche.");
      const card = await within(holder).findByTestId("insight-deletion-card");
      expect(getInsightDeletions).toHaveBeenCalledWith(3, null);
      expect(within(card).getByRole("button", { name: "Delete" })).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "Keep" })).toBeInTheDocument();
      expect(screen.getByText("proposal")).toBeInTheDocument();
    });

    it("says how it ended when the conversation is read again", async () => {
      getInsightDeletions.mockResolvedValue({ items: [insightDeletion({ status: "deleted" })] });

      renderWithQueryClient(
        <ChatTranscript messages={proposed()} runs={[]} permissions={PERMISSIONS} />,
      );

      expect(await screen.findByTestId("insight-deletion-decided")).toHaveTextContent("Deleted");
      expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    });
  });

  it("points a proposed profile change at the profiles page", () => {
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
    expect(within(card).getByRole("link", { name: /Proposed profile change/ })).toHaveAttribute(
      "href",
      "/profiles#staged",
    );
  });

  describe("a proposed insight", () => {
    function learned(): ChatMessage[] {
      return [
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
    }

    it("says it is waiting, because that is the whole rule, until the live row is read", () => {
      getKnowledgeInsight.mockReturnValue(new Promise(() => undefined));
      renderWithQueryClient(
        <ChatTranscript messages={learned()} runs={[]} permissions={PERMISSIONS} />,
      );

      const card = screen.getByTestId("propose-card-insight");
      expect(card).toHaveTextContent("Two clicks finer on the Niche.");
      expect(card).toHaveTextContent("waiting for you to add or dismiss it");
    });

    it("draws the live card, with Add and Dismiss, and no link to the Knowledge page", async () => {
      renderWithQueryClient(
        <ChatTranscript messages={learned()} runs={[]} permissions={PERMISSIONS} />,
      );

      const card = await screen.findByTestId("chat-insight");
      expect(getKnowledgeInsight).toHaveBeenCalledWith(7);
      expect(within(card).getByRole("button", { name: "Add" })).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /Knowledge/ })).not.toBeInTheDocument();
      expect(document.querySelector('a[href*="/knowledge"]')).toBeNull();
    });
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

describe("the line under an answer", () => {
  const usage = {
    prompt_tokens: 85_950,
    completion_tokens: 9600,
    context_tokens: 55_000,
    requests: 10,
    per_request: [{ context: 28_000 }, { context: 55_000 }],
  };
  const messages = [
    {
      id: 1,
      thread_id: 1,
      run_id: 5,
      role: "assistant",
      content: "First.",
      tool_calls: [],
      tool_results: [],
      usage: null,
      created_at: "",
    },
    {
      id: 2,
      thread_id: 1,
      run_id: 5,
      role: "assistant",
      content: "Last of the run.",
      tool_calls: [],
      tool_results: [],
      usage: null,
      created_at: "",
    },
  ] as ChatMessage[];
  const run = (overrides: Record<string, unknown>) =>
    ({
      id: 5,
      thread_id: 1,
      status: "ok",
      provider: "claude_code",
      model: "m",
      error: null,
      tool_rounds: 0,
      tool_calls: 0,
      started_at: "",
      finished_at: null,
      usage,
      ...overrides,
    }) as ChatRun;

  it("words the run's figures once, under its last answer", () => {
    renderWithQueryClient(<ChatTranscript messages={messages} runs={[run({})]} permissions={{}} />);

    const lines = screen.getAllByTestId("answer-usage");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent("10 requests · context 28k → 55k · 9.6k out");
  });

  it("is absent when the run reported no usage", () => {
    renderWithQueryClient(
      <ChatTranscript messages={messages} runs={[run({ usage: null })]} permissions={{}} />,
    );

    expect(screen.queryByTestId("answer-usage")).not.toBeInTheDocument();
  });

  const bare = (o: Partial<ChatMessage>) =>
    ({
      thread_id: 1,
      tool_calls: [],
      tool_results: [],
      usage: null,
      created_at: "",
      content: "",
      ...o,
    }) as ChatMessage;
  const usageRun = (id: number, provider: string, usage: Record<string, unknown>) =>
    run({ id, provider, usage });

  it("sits under the answer after the tool round, not under the text written beside the call", () => {
    const stored = [
      bare({ id: 1, role: "user", content: "Q1" }),
      bare({
        id: 2,
        role: "assistant",
        run_id: 5,
        content: "Let me look.",
        tool_calls: [{ id: "c1", name: "get_shot", arguments: {} }],
      }),
      bare({
        id: 3,
        role: "tool",
        run_id: 5,
        tool_results: [{ id: "c1", name: "get_shot", content: "x", ok: true }],
      }),
      bare({ id: 4, role: "assistant", run_id: 5, content: "Answer one." }),
      bare({ id: 5, role: "user", content: "Q2" }),
      bare({ id: 6, role: "assistant", run_id: 6, content: "Answer two." }),
    ];
    renderWithQueryClient(
      <ChatTranscript
        messages={stored}
        runs={[
          usageRun(5, "anthropic", {
            completion_tokens: 60,
            requests: 2,
            context_tokens: 1210,
            per_request: [{ context: 1150 }, { context: 1210 }],
          }),
          usageRun(6, "anthropic", {
            completion_tokens: 7,
            requests: 1,
            context_tokens: 1300,
            per_request: [{ context: 1300 }],
          }),
        ]}
        permissions={{}}
      />,
    );

    const lines = screen.getAllByTestId("answer-usage");
    expect(lines.map((line) => line.textContent)).toEqual([
      "2 requests · context 1.2k · 60 out",
      "1 request · context 1.3k · 7 out",
    ]);
    expect(lines[0].parentElement).toHaveTextContent("Answer one.");
    expect(lines[0].parentElement).not.toHaveTextContent("Let me look.");
    expect(lines[1].parentElement).toHaveTextContent("Answer two.");
  });

  it("sits under the answer when a Claude Code tool-call message with no text is folded away", () => {
    const stored = [
      bare({ id: 1, role: "user", content: "Q1" }),
      bare({
        id: 2,
        role: "assistant",
        run_id: 5,
        tool_calls: [{ id: "c1", name: "get_shot", arguments: {} }],
      }),
      bare({
        id: 3,
        role: "tool",
        run_id: 5,
        tool_results: [{ id: "c1", name: "get_shot", content: "x", ok: true }],
      }),
      bare({ id: 4, role: "assistant", run_id: 5, content: "Done." }),
    ];
    renderWithQueryClient(
      <ChatTranscript
        messages={stored}
        runs={[
          usageRun(5, "claude_code", {
            completion_tokens: 431,
            requests: 4,
            context_tokens: 21_741,
            context_window: 200_000,
            per_request: [{ context: 21_146 }, { context: 21_741 }],
          }),
        ]}
        permissions={{}}
      />,
    );

    const lines = screen.getAllByTestId("answer-usage");
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toBe("4 requests · context 21k → 22k · 431 out");
    expect(lines[0].parentElement).toHaveTextContent("Done.");
  });
});
