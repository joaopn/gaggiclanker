import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgePage } from "@/pages/KnowledgePage";
import { knowledgeChunk, knowledgeDoc, knowledgeInsight, rule } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  getKnowledgeRules,
  patchKnowledgeRule,
  reloadKnowledgeRules,
  getKnowledgeDocs,
  getKnowledgeDoc,
  getKnowledgeInsights,
  searchKnowledge,
} = vi.hoisted(() => ({
  getKnowledgeRules: vi.fn(),
  patchKnowledgeRule: vi.fn(),
  reloadKnowledgeRules: vi.fn(),
  getKnowledgeDocs: vi.fn(),
  getKnowledgeDoc: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  searchKnowledge: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getKnowledgeRules,
  patchKnowledgeRule,
  reloadKnowledgeRules,
  getKnowledgeDocs,
  getKnowledgeDoc,
  getKnowledgeInsights,
  searchKnowledge,
}));

const RULES = {
  items: [
    rule(),
    rule({
      id: 2,
      category: "temperature_by_roast",
      key: "light",
      value: { text: "Light roasts extract at 94-96 °C.", min_c: 94, max_c: 96 },
      unit: "c",
      enabled: false,
    }),
  ],
  categories: ["dial_in_order", "temperature_by_roast"],
};

beforeEach(() => {
  vi.clearAllMocks();
  getKnowledgeRules.mockResolvedValue(RULES);
  patchKnowledgeRule.mockImplementation(async (id: number, patch: Record<string, unknown>) => ({
    ...RULES.items.find((entry) => entry.id === id),
    ...patch,
  }));
  reloadKnowledgeRules.mockResolvedValue({ changed: 3 });
  getKnowledgeDocs.mockResolvedValue({ items: [knowledgeDoc()] });
  getKnowledgeDoc.mockResolvedValue({ doc: knowledgeDoc(), chunks: [knowledgeChunk()] });
  getKnowledgeInsights.mockResolvedValue({
    items: [knowledgeInsight()],
    scope_keys: ["grinder_id"],
  });
  searchKnowledge.mockResolvedValue({ query: "", items: [] });
});

describe("KnowledgePage", () => {
  it("groups the rules by category, with their provenance", async () => {
    renderWithQueryClient(<KnowledgePage />);

    // Each category name appears twice: once as a filter chip, once as the
    // heading of its own card.
    expect(await screen.findAllByText("dial in order")).toHaveLength(2);
    expect(screen.getAllByText("temperature by roast")).toHaveLength(2);
    expect(screen.getByText("Grind first, then yield, then temperature.")).toBeInTheDocument();
    expect(screen.getAllByText(/gaggimate-barista/)[0]).toBeInTheDocument();
    // "2 rules, 1 turned off" — the count of what is off is the thing worth
    // noticing on this page.
    expect(screen.getByText(/2 rules, 1 turned off/)).toBeInTheDocument();
  });

  it("turns a rule off", async () => {
    const user = setupUser();
    renderWithQueryClient(<KnowledgePage />);

    const [row] = await screen.findAllByTestId("rule");
    await user.click(within(row).getByTestId("toggle-rule"));

    await waitFor(() => expect(patchKnowledgeRule).toHaveBeenCalledWith(1, { enabled: false }));
  });

  it("edits a rule's value, and refuses to send text that is not JSON", async () => {
    const user = setupUser();
    renderWithQueryClient(<KnowledgePage />);

    const [row] = await screen.findAllByTestId("rule");
    await user.click(within(row).getByRole("button", { name: "Edit" }));

    const box = within(row).getByLabelText("Value for hierarchy");
    await user.clear(box);
    await user.type(box, "{{not json");
    await user.click(within(row).getByRole("button", { name: "Save" }));
    // Parsed in the page, so a stray brace is a message beside the box rather
    // than a 400 from three layers away.
    expect(patchKnowledgeRule).not.toHaveBeenCalled();

    await user.clear(box);
    await user.type(box, '{{"text": "mine"}');
    await user.click(within(row).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(patchKnowledgeRule).toHaveBeenCalledWith(1, { value: { text: "mine" } }),
    );
  });

  it("highlights the rule an analysis linked to", async () => {
    renderWithQueryClient(<KnowledgePage />, { initialEntries: ["/knowledge?rule=hierarchy"] });

    const [row] = await screen.findAllByTestId("rule");
    expect(row.className).toContain("border-primary");
  });

  it("reloads the tier from the shipped file", async () => {
    const user = setupUser();
    renderWithQueryClient(<KnowledgePage />);

    await user.click(await screen.findByTestId("reload-rules"));
    await waitFor(() => expect(reloadKnowledgeRules).toHaveBeenCalled());
  });

  it("filters by category", async () => {
    const user = setupUser();
    renderWithQueryClient(<KnowledgePage />);

    const chips = await screen.findAllByRole("button", { name: "temperature by roast" });
    await user.click(chips[0]);
    await waitFor(() =>
      expect(getKnowledgeRules).toHaveBeenCalledWith({ category: "temperature_by_roast" }),
    );
  });
});

describe("KnowledgePage — the three tiers", () => {
  it("opens on the rules and does not fetch the other tiers until asked", async () => {
    renderWithQueryClient(<KnowledgePage />);

    await screen.findAllByTestId("rule");
    expect(getKnowledgeDocs).not.toHaveBeenCalled();
    expect(getKnowledgeInsights).not.toHaveBeenCalled();
  });

  it("switches to the documents", async () => {
    const user = setupUser();
    renderWithQueryClient(<KnowledgePage />);

    await user.click(await screen.findByRole("tab", { name: "Docs" }));

    expect(await screen.findByTestId("doc-list")).toBeInTheDocument();
    expect(screen.getByText("Espresso Tasting Guide")).toBeInTheDocument();
  });

  it("switches to the insights", async () => {
    const user = setupUser();
    renderWithQueryClient(<KnowledgePage />);

    await user.click(await screen.findByRole("tab", { name: "Insights" }));

    expect(await screen.findByTestId("proposed-insights")).toBeInTheDocument();
  });

  it("carries a search hit's chunk into the URL, not just its document", async () => {
    const user = setupUser();
    searchKnowledge.mockResolvedValue({
      query: "sour",
      items: [{ chunk: knowledgeChunk(), score: 4.2, snippet: "Sour hits fast." }],
    });
    renderWithQueryClient(<KnowledgePage />);

    await user.click(await screen.findByRole("tab", { name: "Docs" }));
    await user.type(await screen.findByTestId("doc-search"), "sour");
    await user.click(await screen.findByText("ESPRESSO_TASTING_GUIDE#sour-vs-bitter"));

    const chunk = await screen.findByTestId("chunk");
    expect(chunk.className).toContain("border-primary");
  });

  it("lands on the passage a citation linked to, tab and all", async () => {
    // The `?doc=` parameter outranks a missing `?tab=`: a link that says what to
    // show must not land on a different tier.
    renderWithQueryClient(<KnowledgePage />, {
      initialEntries: [
        "/knowledge?doc=ESPRESSO_TASTING_GUIDE&chunk=ESPRESSO_TASTING_GUIDE%23sour-vs-bitter",
      ],
    });

    const chunk = await screen.findByTestId("chunk");
    expect(chunk).toHaveAttribute("data-chunk", "ESPRESSO_TASTING_GUIDE#sour-vs-bitter");
    expect(chunk.className).toContain("border-primary");
    expect(getKnowledgeDoc).toHaveBeenCalledWith("ESPRESSO_TASTING_GUIDE");
  });

  it("still highlights a rule a citation linked to", async () => {
    renderWithQueryClient(<KnowledgePage />, { initialEntries: ["/knowledge?rule=hierarchy"] });

    const [row] = await screen.findAllByTestId("rule");
    expect(row.className).toContain("border-primary");
  });
});
