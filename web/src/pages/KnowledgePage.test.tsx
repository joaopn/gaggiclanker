import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgePage } from "@/pages/KnowledgePage";
import { rule } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { getKnowledgeRules, patchKnowledgeRule, reloadKnowledgeRules } = vi.hoisted(() => ({
  getKnowledgeRules: vi.fn(),
  patchKnowledgeRule: vi.fn(),
  reloadKnowledgeRules: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getKnowledgeRules,
  patchKnowledgeRule,
  reloadKnowledgeRules,
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
