import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InsightsTab } from "@/components/knowledge/InsightsTab";
import { knowledgeInsight } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  getKnowledgeInsights,
  createKnowledgeInsight,
  patchKnowledgeInsight,
  deleteKnowledgeInsight,
} = vi.hoisted(() => ({
  getKnowledgeInsights: vi.fn(),
  createKnowledgeInsight: vi.fn(),
  patchKnowledgeInsight: vi.fn(),
  deleteKnowledgeInsight: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getKnowledgeInsights,
  createKnowledgeInsight,
  patchKnowledgeInsight,
  deleteKnowledgeInsight,
}));

const INSIGHTS = {
  items: [
    knowledgeInsight(),
    knowledgeInsight({
      id: 2,
      scope: {},
      text: "This machine reads a degree and a half low at 93.",
      source: "user",
      confirmed: true,
      confirmed_at: "2026-03-03T10:00:00.000Z",
      evidence_shot_ids: [],
      analysis_id: null,
    }),
  ],
  scope_keys: ["bean_id", "roast_level", "process", "origin", "grinder_id", "profile_style"],
};

beforeEach(() => {
  vi.clearAllMocks();
  getKnowledgeInsights.mockResolvedValue(INSIGHTS);
  createKnowledgeInsight.mockResolvedValue(knowledgeInsight({ id: 3, confirmed: true }));
  patchKnowledgeInsight.mockImplementation(async (id: number, patch: Record<string, unknown>) => ({
    ...INSIGHTS.items.find((entry) => entry.id === id),
    ...patch,
  }));
  deleteKnowledgeInsight.mockResolvedValue({ deleted: true });
});

describe("InsightsTab", () => {
  it("separates what is waiting for a decision from what is already believed", async () => {
    renderWithQueryClient(<InsightsTab />);

    const proposed = await screen.findByTestId("proposed-insights");
    expect(within(proposed).getAllByTestId("insight")).toHaveLength(1);
    expect(proposed).toHaveTextContent("proposed, not confirmed");

    const confirmed = screen.getByTestId("confirmed-insights");
    expect(within(confirmed).getAllByTestId("insight")).toHaveLength(1);
    expect(confirmed).toHaveTextContent("This machine reads a degree and a half low");
  });

  it("shows the scope, because the text means nothing without it", async () => {
    renderWithQueryClient(<InsightsTab />);

    const scopes = await screen.findAllByTestId("insight-scope");
    expect(scopes[0]).toHaveTextContent("grinder=2");
    expect(scopes[0]).toHaveTextContent("process=natural");
    // An empty scope is a claim about every shot, and it says so.
    expect(scopes[1]).toHaveTextContent("any shot");
  });

  it("links the evidence to the shots it was drawn from", async () => {
    renderWithQueryClient(<InsightsTab />);

    const proposed = await screen.findByTestId("proposed-insights");
    expect(within(proposed).getByRole("link", { name: "shot 4" })).toHaveAttribute(
      "href",
      "/shots/4",
    );
    expect(within(proposed).getByRole("link", { name: "shot 6" })).toBeInTheDocument();
  });

  it("confirms a proposal, which is what puts it in front of the model", async () => {
    const user = setupUser();
    renderWithQueryClient(<InsightsTab />);

    const proposed = await screen.findByTestId("proposed-insights");
    await user.click(within(proposed).getByTestId("toggle-insight"));

    await waitFor(() => expect(patchKnowledgeInsight).toHaveBeenCalledWith(1, { confirmed: true }));
  });

  it("takes a confirmed insight back out again", async () => {
    const user = setupUser();
    renderWithQueryClient(<InsightsTab />);

    const confirmed = await screen.findByTestId("confirmed-insights");
    await user.click(within(confirmed).getByTestId("toggle-insight"));

    await waitFor(() =>
      expect(patchKnowledgeInsight).toHaveBeenCalledWith(2, { confirmed: false }),
    );
  });

  it("deletes one, because an insight is this box's own", async () => {
    const user = setupUser();
    renderWithQueryClient(<InsightsTab />);

    const proposed = await screen.findByTestId("proposed-insights");
    await user.click(within(proposed).getByTestId("delete-insight"));

    await waitFor(() => expect(deleteKnowledgeInsight).toHaveBeenCalledWith(1));
  });

  it("edits the text in place", async () => {
    const user = setupUser();
    renderWithQueryClient(<InsightsTab />);

    await user.click(await screen.findByRole("button", { name: "Edit insight 1" }));
    const card = screen.getAllByTestId("insight")[0];
    const box = within(card).getByRole("textbox", { name: "Text of insight 1" });
    await user.clear(box);
    await user.type(box, "Three numbers finer.");
    // Scoped to the card: the "write one yourself" form has a Save button too.
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(patchKnowledgeInsight).toHaveBeenCalledWith(1, { text: "Three numbers finer." }),
    );
  });

  it("writes one by hand, already confirmed", async () => {
    const user = setupUser();
    renderWithQueryClient(<InsightsTab />);

    await user.type(await screen.findByTestId("insight-text"), "The Niche drifts as it warms.");
    const scope = screen.getByTestId("insight-scope-input");
    await user.clear(scope);
    await user.type(scope, '{{"grinder_id": 2}');
    await user.click(screen.getAllByRole("button", { name: "Save" })[0]);

    await waitFor(() =>
      expect(createKnowledgeInsight).toHaveBeenCalledWith({
        scope: { grinder_id: 2 },
        text: "The Niche drifts as it warms.",
        confirmed: true,
      }),
    );
  });

  it("reports a scope that is not JSON beside the box rather than sending it", async () => {
    const user = setupUser();
    renderWithQueryClient(<InsightsTab />);

    await user.type(await screen.findByTestId("insight-text"), "Something.");
    const scope = screen.getByTestId("insight-scope-input");
    await user.clear(scope);
    await user.type(scope, "not json");
    await user.click(screen.getAllByRole("button", { name: "Save" })[0]);

    expect(createKnowledgeInsight).not.toHaveBeenCalled();
    expect((await screen.findAllByText(/not valid JSON/i)).length).toBeGreaterThan(0);
  });

  it("says so when nothing has been learned yet", async () => {
    getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
    renderWithQueryClient(<InsightsTab />);

    expect(await screen.findByText("Nothing learned yet")).toBeInTheDocument();
  });
});

describe("InsightsTab ordering", () => {
  it("lists the newest first, against the server's prompt order", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [
        knowledgeInsight({ id: 1, text: "The oldest one." }),
        knowledgeInsight({ id: 2, text: "The middle one." }),
        knowledgeInsight({ id: 3, text: "The newest one." }),
      ],
      scope_keys: [],
    });
    renderWithQueryClient(<InsightsTab />);

    const cards = await screen.findAllByTestId("insight");
    // The server answers oldest-first because that is the order a *prompt*
    // wants; on a page the proposal you have not seen is the new one.
    expect(cards.map((card) => card.getAttribute("data-insight"))).toEqual(["3", "2", "1"]);
  });
});
