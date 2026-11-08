import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import {
  GeneralInsightRequestCard,
  type GeneralRequestFallback,
} from "@/components/chat/GeneralInsightRequestCard";
import { proposalFrom } from "@/components/chat/ProposeCard";
import {
  ChatRecipeContext,
  ChatThreadContext,
  TellAgentContext,
} from "@/components/chat/tellAgent";
import { queryKeys } from "@/lib/queryKeys";
import { knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const {
  getKnowledgeInsight,
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  getScopeOptions,
  getScopeReach,
} = vi.hoisted(() => ({
  getKnowledgeInsight: vi.fn(),
  patchKnowledgeInsight: vi.fn(),
  dismissKnowledgeInsight: vi.fn(),
  getScopeOptions: vi.fn(),
  getScopeReach: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getKnowledgeInsight,
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  getScopeOptions,
  getScopeReach,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const INSIGHT_ID = 31;

const FALLBACK: GeneralRequestFallback = {
  text: "Never go finer than 8 on the Niche Zero: it chokes.",
  said: "never suggest going finer than 8 on the Niche, it chokes",
  scopeLabels: ["grinder Niche Zero"],
  reach: 4,
  replacesId: null,
  replacesText: "",
};

function row(overrides: Parameters<typeof knowledgeInsight>[0] = {}) {
  return knowledgeInsight({
    id: 31,
    text: FALLBACK.text,
    scope: { grinder_id: 2 },
    source: "user",
    recipe_id: null,
    general: true,
    confirmed: false,
    thread_id: 9,
    ...overrides,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  getKnowledgeInsight.mockResolvedValue(row());
  getScopeOptions.mockResolvedValue({
    recipe: [
      { key: "grinder_id", value: 2, label: "grinder Niche Zero" },
      { key: "bean_id", value: 5, label: "bean Ethiopia Guji Hambela" },
    ],
    beans: [],
    grinders: [],
    roast_levels: [],
    processes: [],
    origins: [],
  });
  getScopeReach.mockResolvedValue({ recipes: 4 });
  patchKnowledgeInsight.mockImplementation(async (id: number, patch: Record<string, unknown>) =>
    row({ id, ...patch, confirmed: true } as never),
  );
  dismissKnowledgeInsight.mockResolvedValue(row({ dismissed: true }));
});

function renderCard(tell?: (message: string) => void, recipeId: number | null = 3) {
  return renderWithQueryClient(
    <ChatThreadContext.Provider value={9}>
      <ChatRecipeContext.Provider value={recipeId}>
        <TellAgentContext.Provider value={tell ?? null}>
          <GeneralInsightRequestCard insightId={31} fallback={FALLBACK} />
        </TellAgentContext.Provider>
      </ChatRecipeContext.Provider>
    </ChatThreadContext.Provider>,
  );
}

describe("GeneralInsightRequestCard, waiting", () => {
  it("quotes what you said, offers the agent's text and scope to change, and says how far Add reaches", async () => {
    renderCard();

    await waitFor(() =>
      expect(screen.getByTestId("general-request")).toHaveAttribute("data-state", "waiting"),
    );
    const card = screen.getByTestId("general-request");
    expect(within(card).getByTestId("general-request-said")).toHaveTextContent(
      `You said: “${FALLBACK.said}”`,
    );
    expect(within(card).getByTestId("general-request-text")).toHaveValue(FALLBACK.text);
    // The agent's scope is pre-ticked; the other thing the Recipe states is not.
    expect(await within(card).findByRole("checkbox", { name: "grinder Niche Zero" })).toBeChecked();
    expect(
      within(card).getByRole("checkbox", { name: "bean Ethiopia Guji Hambela" }),
    ).not.toBeChecked();
    await waitFor(() =>
      expect(within(card).getByTestId("scope-picker-summary")).toHaveTextContent(
        "Matches 4 Recipes now.",
      ),
    );
    expect(card).toHaveTextContent("Adding it tells every matching Recipe's conversation");
    expect(within(card).getByRole("button", { name: "Add" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Dismiss" })).toBeEnabled();
    expect(card).not.toHaveTextContent(/_id|\b31\b/);
  });

  it("shows the tool's output until the live row arrives", async () => {
    getKnowledgeInsight.mockReturnValue(new Promise(() => {}));
    renderCard();

    const card = await screen.findByTestId("general-request");
    expect(card).toHaveAttribute("data-state", "loading");
    expect(card).toHaveTextContent(FALLBACK.text);
    expect(card).toHaveTextContent(FALLBACK.said);
    expect(card).toHaveTextContent("Applies to grinder Niche Zero · matches 4 Recipes now");
  });

  it("Add sends one PATCH with the edited words, the edited scope and the confirmation, and no chat message", async () => {
    const user = setupUser();
    const tell = vi.fn();
    renderCard(tell);

    const text = await screen.findByTestId("general-request-text");
    await user.clear(text);
    await user.type(text, "Stay at 8 or coarser on the Niche.");
    await user.click(await screen.findByRole("checkbox", { name: "bean Ethiopia Guji Hambela" }));
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(patchKnowledgeInsight).toHaveBeenCalledTimes(1));
    expect(patchKnowledgeInsight).toHaveBeenCalledWith(31, {
      text: "Stay at 8 or coarser on the Niche.",
      scope: { bean_id: 5, grinder_id: 2 },
      confirmed: true,
    });
    expect(dismissKnowledgeInsight).not.toHaveBeenCalled();
    // The agent learns the answer at its next turn, from the record: nothing is typed to it.
    expect(tell).not.toHaveBeenCalled();
  });

  it("Add saves exactly what the card shows, a value the Recipe no longer offers included", async () => {
    const user = setupUser();
    getKnowledgeInsight.mockResolvedValue(row({ scope: { bean_id: 7, grinder_id: 2 } }));
    getScopeOptions.mockResolvedValue({
      recipe: [{ key: "grinder_id", value: 2, label: "grinder Niche Zero" }],
      beans: [{ id: 7, label: "Old bag" }],
      grinders: [],
      roast_levels: [],
      processes: [],
      origins: [],
    });
    renderCard();

    expect(
      await screen.findByRole("checkbox", { name: "bean Old bag (not offered here any more)" }),
    ).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(patchKnowledgeInsight).toHaveBeenCalledWith(31, {
        text: FALLBACK.text,
        scope: { bean_id: 7, grinder_id: 2 },
        confirmed: true,
      }),
    );
  });

  it("in a General chat Add saves exactly what the selects show, an archived bean included", async () => {
    const user = setupUser();
    getKnowledgeInsight.mockResolvedValue(row({ scope: { bean_id: 2 } }));
    getScopeOptions.mockResolvedValue({
      recipe: null,
      beans: [{ id: 5, label: "Guji" }],
      grinders: [],
      roast_levels: [],
      processes: [],
      origins: [],
    });
    renderCard(undefined, null);

    const bean = await screen.findByLabelText("Bean");
    expect(
      await screen.findByRole("option", {
        name: "a bean not offered here any more",
      }),
    ).toBeInTheDocument();
    expect(bean).toHaveValue("2");
    expect(screen.getByTestId("scope-picker-summary")).not.toHaveTextContent("Nothing chosen");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(patchKnowledgeInsight).toHaveBeenCalledWith(31, {
        text: FALLBACK.text,
        scope: { bean_id: 2 },
        confirmed: true,
      }),
    );
  });

  it("names the stale bean by what the agent's request said, and Add still saves it", async () => {
    const user = setupUser();
    getKnowledgeInsight.mockResolvedValue(row({ scope: { bean_id: 2 } }));
    getScopeOptions.mockResolvedValue({
      recipe: null,
      beans: [{ id: 5, label: "Guji" }],
      grinders: [],
      roast_levels: [],
      processes: [],
      origins: [],
    });
    renderWithQueryClient(
      <ChatThreadContext.Provider value={9}>
        <ChatRecipeContext.Provider value={null}>
          <GeneralInsightRequestCard
            insightId={31}
            fallback={{ ...FALLBACK, scopeLabels: ["bean Ethiopia Guji"] }}
          />
        </ChatRecipeContext.Provider>
      </ChatThreadContext.Provider>,
    );

    expect(
      await screen.findByRole("option", { name: "bean Ethiopia Guji (not offered here any more)" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(patchKnowledgeInsight).toHaveBeenCalledWith(31, {
        text: FALLBACK.text,
        scope: { bean_id: 2 },
        confirmed: true,
      }),
    );
  });

  it("sends nothing twice for a double click on Add", async () => {
    const user = setupUser();
    let release: () => void = () => {};
    patchKnowledgeInsight.mockReturnValue(
      new Promise<never>((resolve) => {
        release = () => resolve(undefined as never);
      }),
    );
    renderCard();

    const add = await screen.findByRole("button", { name: "Add" });
    await user.dblClick(add);
    await user.click(add);

    expect(patchKnowledgeInsight).toHaveBeenCalledTimes(1);
    await act(async () => release());
  });

  it("will not add an empty text", async () => {
    const user = setupUser();
    renderCard();

    await user.clear(await screen.findByTestId("general-request-text"));

    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  });

  it("says in words that a failed Add did not add it, with the server's sentence", async () => {
    const user = setupUser();
    patchKnowledgeInsight.mockRejectedValue(
      new ApiClientError("That insight can no longer be changed", { status: 409 }),
    );
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Add" }));

    expect(await screen.findByTestId("general-request-error")).toHaveTextContent(
      "That insight can no longer be changed It was not added.",
    );
    expect(screen.getByTestId("general-request")).toHaveAttribute("data-state", "waiting");
  });

  it("Dismiss uses the dismiss route and sends no text or scope", async () => {
    const user = setupUser();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(dismissKnowledgeInsight).toHaveBeenCalledWith(31));
    expect(patchKnowledgeInsight).not.toHaveBeenCalled();
  });
});

describe("GeneralInsightRequestCard, answered", () => {
  it("added: the live text and scope by name, how far it reaches, and the way to the Knowledge page", async () => {
    getKnowledgeInsight.mockResolvedValue(
      row({ confirmed: true, text: "Edited on the Knowledge page." }),
    );
    renderCard();

    await waitFor(() =>
      expect(screen.getByTestId("general-request")).toHaveAttribute("data-state", "added"),
    );
    const card = screen.getByTestId("general-request");
    expect(within(card).getByTestId("general-request-text-live")).toHaveTextContent(
      "Edited on the Knowledge page.",
    );
    await waitFor(() =>
      expect(within(card).getByTestId("general-request-applies")).toHaveTextContent(
        "Applies to grinder Niche Zero · matches 4 Recipes now",
      ),
    );
    expect(card).toHaveTextContent(`General insight #${INSIGHT_ID} · added`);
    expect(
      within(card).getByRole("link", { name: /Edit or delete it on the Knowledge page/ }),
    ).toHaveAttribute("href", "/knowledge?tab=insights");
  });

  it("added with no scope says every Recipe", async () => {
    getKnowledgeInsight.mockResolvedValue(row({ confirmed: true, scope: {} }));
    renderCard();

    expect(await screen.findByTestId("general-request-applies")).toHaveTextContent(
      "Applies to every Recipe",
    );
  });

  it("dismissed: Not saved, with the text", async () => {
    getKnowledgeInsight.mockResolvedValue(row({ dismissed: true }));
    renderCard();

    await waitFor(() =>
      expect(screen.getByTestId("general-request")).toHaveAttribute("data-state", "dismissed"),
    );
    const card = screen.getByTestId("general-request");
    expect(within(card).getByTestId("general-request-dismissed")).toHaveTextContent(
      `Not saved. “${FALLBACK.text}”`,
    );
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });

  it("deleted (a 404): says it was deleted on the Knowledge page, and offers nothing", async () => {
    getKnowledgeInsight.mockRejectedValue(new ApiClientError("No insight 31", { status: 404 }));
    renderCard();

    await waitFor(() =>
      expect(screen.getByTestId("general-request")).toHaveAttribute("data-state", "deleted"),
    );
    const card = screen.getByTestId("general-request");
    expect(card).toHaveTextContent(
      `General insight #${INSIGHT_ID} · deleted on the Knowledge page`,
    );
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });

  it("another failure to read is not 'deleted'", async () => {
    getKnowledgeInsight.mockRejectedValue(new ApiClientError("Server error", { status: 500 }));
    renderCard();

    expect(await screen.findByText("Could not read where it stands now.")).toBeInTheDocument();
    expect(screen.getByTestId("general-request")).not.toHaveAttribute("data-state", "deleted");
  });
});

describe("the dispatch", () => {
  it("reads a request_general_insight result into this card, and ignores a refusal", () => {
    const content = JSON.stringify({
      insight_id: 31,
      text: FALLBACK.text,
      said: FALLBACK.said,
      scope: { grinder_id: 2 },
      scope_labels: ["grinder Niche Zero"],
      reach: 4,
      note: "Nothing is saved",
    });
    const made = proposalFrom({
      id: "t1",
      name: "request_general_insight",
      arguments: {},
      ok: true,
      content,
    });

    expect(made?.kind).toBe("general_insight_request");
    expect(made?.generalRequest).toEqual({
      insightId: 31,
      text: FALLBACK.text,
      said: FALLBACK.said,
      scopeLabels: ["grinder Niche Zero"],
      reach: 4,
      replacesId: null,
      replacesText: "",
    });
    expect(
      proposalFrom({
        id: "t2",
        name: "request_general_insight",
        arguments: {},
        ok: false,
        content: JSON.stringify({ error: "failed", detail: "Quote the person's words" }),
      }),
    ).toBeNull();
  });
});

describe("a replacement", () => {
  const OLD_ID = 7;
  const OLD_TEXT = "Never go finer than 6 on the Niche Zero.";
  const REPLACING: GeneralRequestFallback = {
    ...FALLBACK,
    replacesId: OLD_ID,
    replacesText: OLD_TEXT,
  };

  function oldRow(overrides: Parameters<typeof knowledgeInsight>[0] = {}) {
    return knowledgeInsight({
      id: OLD_ID,
      text: OLD_TEXT,
      scope: { grinder_id: 2 },
      source: "user",
      recipe_id: null,
      general: true,
      confirmed: true,
      ...overrides,
    });
  }

  function serve(request: ReturnType<typeof row>, old: ReturnType<typeof oldRow> | Error) {
    getKnowledgeInsight.mockImplementation(async (id: number) => {
      if (id === OLD_ID) {
        if (old instanceof Error) throw old;
        return old;
      }
      return request;
    });
  }

  function renderReplacing(fallback = REPLACING) {
    return renderWithQueryClient(
      <ChatThreadContext.Provider value={9}>
        <ChatRecipeContext.Provider value={3}>
          <GeneralInsightRequestCard insightId={31} fallback={fallback} />
        </ChatRecipeContext.Provider>
      </ChatThreadContext.Provider>,
    );
  }

  it("says what it replaces, read live, and what Adding does, next to the reach", async () => {
    serve(row({ replaces_id: OLD_ID, replaces_text: OLD_TEXT }), oldRow());
    renderReplacing();

    const line = await screen.findByTestId("general-request-replaces");
    await waitFor(() =>
      expect(line).toHaveTextContent(`Replaces #${OLD_ID} (your note; grinder Niche Zero):`),
    );
    expect(screen.getByTestId("general-request-replaces-text")).toHaveTextContent(OLD_TEXT);
    const note = screen.getByTestId("general-request-note");
    expect(note).toHaveTextContent(
      `Adding it deletes #${OLD_ID} and tells every matching Recipe's conversation the new one.`,
    );
    await waitFor(() => expect(note).toHaveTextContent("Matches 4 Recipes now."));
    expect(getKnowledgeInsight).toHaveBeenCalledWith(OLD_ID);
  });

  it("shows the words stored when it asked until the old insight is read, and the live words after", async () => {
    getKnowledgeInsight.mockImplementation((id: number) =>
      id === OLD_ID
        ? new Promise(() => {})
        : Promise.resolve(row({ replaces_id: OLD_ID, replaces_text: OLD_TEXT })),
    );
    renderReplacing();

    expect(await screen.findByTestId("general-request-replaces-text")).toHaveTextContent(OLD_TEXT);
  });

  it("uses the live words when the old insight was edited since", async () => {
    serve(
      row({ replaces_id: OLD_ID, replaces_text: OLD_TEXT }),
      oldRow({ text: "Never go finer than 7 on the Niche Zero." }),
    );
    renderReplacing();

    await waitFor(() =>
      expect(screen.getByTestId("general-request-replaces-text")).toHaveTextContent(
        "Never go finer than 7 on the Niche Zero.",
      ),
    );
  });

  it.each([
    ["deleted on the Knowledge page", new ApiClientError("No insight 7", { status: 404 })],
    ["taken back", oldRow({ confirmed: false })],
    ["dismissed", oldRow({ dismissed: true })],
    ["no longer general", oldRow({ recipe_id: 3, general: false })],
  ])("says the old insight has changed or gone when it was %s", async (_case, old) => {
    serve(row({ replaces_id: OLD_ID, replaces_text: OLD_TEXT }), old);
    renderReplacing();

    const gone = await screen.findByTestId("general-request-replaces-gone");
    expect(gone).toHaveTextContent(
      `#${OLD_ID} has changed or gone since: adding this will not delete anything.`,
    );
    expect(screen.queryByTestId("general-request-replaces")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeEnabled();
  });

  it("says so when the server has already cleared the link because the old one was deleted", async () => {
    serve(row({ replaces_id: null, replaces_text: "", replaced: "old_changed" }), oldRow());
    renderReplacing();

    expect(await screen.findByTestId("general-request-replaces-gone")).toHaveTextContent(
      `#${OLD_ID} has changed or gone since`,
    );
    // The old insight is not even read: nothing links to it any more.
    expect(getKnowledgeInsight).not.toHaveBeenCalledWith(OLD_ID);
  });

  it("is quiet about replacing when it replaces nothing", async () => {
    renderCard();

    await screen.findByTestId("general-request-note");
    expect(screen.queryByTestId("general-request-replaces")).not.toBeInTheDocument();
    expect(screen.queryByTestId("general-request-replaces-gone")).not.toBeInTheDocument();
  });

  it("once added says it replaced the old one", async () => {
    getKnowledgeInsight.mockResolvedValue(row({ confirmed: true, replaced: "deleted" }));
    renderReplacing();

    expect(await screen.findByTestId("general-request-replaced")).toHaveTextContent(
      `Replaced #${OLD_ID}`,
    );
  });

  it("once added says when the old one had already changed or gone, so nothing was replaced", async () => {
    getKnowledgeInsight.mockResolvedValue(row({ confirmed: true, replaced: "old_changed" }));
    renderReplacing();

    expect(await screen.findByTestId("general-request-replaced")).toHaveTextContent(
      `#${OLD_ID} had already changed or gone, so nothing was replaced`,
    );
  });

  it("an Add without a replacement says nothing about replacing", async () => {
    getKnowledgeInsight.mockResolvedValue(row({ confirmed: true }));
    renderCard();

    await screen.findByTestId("general-request-text-live");
    expect(screen.queryByTestId("general-request-replaced")).not.toBeInTheDocument();
  });

  it("reads the replacement out of the tool's result", () => {
    const made = proposalFrom({
      id: "t1",
      name: "request_general_insight",
      arguments: {},
      ok: true,
      content: JSON.stringify({
        insight_id: 31,
        text: FALLBACK.text,
        said: FALLBACK.said,
        scope: {},
        scope_labels: [],
        reach: 4,
        replaces_insight_id: OLD_ID,
        replaces_text: OLD_TEXT,
      }),
    });

    expect(made?.generalRequest).toMatchObject({ replacesId: OLD_ID, replacesText: OLD_TEXT });
  });
});

describe("the conversation it answers in", () => {
  it.each(["Add", "Dismiss"])(
    "%s refreshes the conversation the card is in, by its real thread id",
    async (name) => {
      const user = setupUser();
      const { queryClient } = renderCard();
      const spy = vi.spyOn(queryClient, "invalidateQueries");

      await user.click(await screen.findByRole("button", { name }));

      await waitFor(() =>
        expect(spy).toHaveBeenCalledWith(
          expect.objectContaining({ queryKey: queryKeys.chat.thread("9") }),
        ),
      );
    },
  );
});
