import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecipesPage } from "@/pages/RecipesPage";
import { designingRecipe, recipeRow, vocabulary } from "@/test/recipesFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getRecipes, getVocabulary, getBeans, getGrinders, getMachines, getProfileVersions } =
  vi.hoisted(() => ({
    getRecipes: vi.fn(),
    getVocabulary: vi.fn(),
    getBeans: vi.fn(),
    getGrinders: vi.fn(),
    getMachines: vi.fn(),
    getProfileVersions: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getRecipes,
  getVocabulary,
  getBeans,
  getGrinders,
  getMachines,
  getProfileVersions,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  getBeans.mockResolvedValue({ items: [] });
  getGrinders.mockResolvedValue({ items: [] });
  getMachines.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
});

const NOTES = "oat milk (Oatly Barista), ~150 ml; 1 pump vanilla";

describe("RecipesPage, the drink on a card", () => {
  it("puts the badge at the right of the name, and the notes as one line with the whole text on hover", async () => {
    getRecipes.mockResolvedValue({
      items: [recipeRow({ drink: "cappuccino", drink_notes: NOTES })],
    });
    renderWithQueryClient(<RecipesPage />);

    const card = await screen.findByTestId("recipe-card");
    const badge = await within(card).findByTestId("drink-badge");
    expect(badge).toHaveTextContent("Cappuccino");
    // In the header row, after the title link: the name stays the card's heading.
    const title = within(card).getByRole("link", { name: "Guji on the Niche" });
    expect(title.compareDocumentPosition(badge) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const notes = within(card).getByTestId("recipe-drink-notes");
    expect(notes).toHaveTextContent(NOTES);
    expect(notes).toHaveAttribute("title", NOTES);
    // One line cut with an ellipsis, never wrapped over the numbers. The card is a grid item and
    // would otherwise grow to its content's width: Chromium measured the page at 966 px on a
    // 360 px screen before `min-w-0` (jsdom has no layout, so the classes are what is pinned).
    expect(notes.className).toContain("truncate");
    expect(notes.className).toContain("min-w-0");
    expect(card.className).toContain("min-w-0");
  });

  it("shows no badge and no notes line for a Recipe that states no drink", async () => {
    getRecipes.mockResolvedValue({ items: [recipeRow({ drink: null, drink_notes: "" })] });
    renderWithQueryClient(<RecipesPage />);

    const card = await screen.findByTestId("recipe-card");
    expect(within(card).queryByTestId("drink-badge")).not.toBeInTheDocument();
    expect(within(card).queryByTestId("recipe-drink-notes")).not.toBeInTheDocument();
  });

  it("shows the notes without a badge when only the notes are there, and no badge for a Recipe being designed", async () => {
    getRecipes.mockResolvedValue({
      items: [designingRecipe({ drink: null }), recipeRow({ drink: null, drink_notes: "iced" })],
    });
    renderWithQueryClient(<RecipesPage />);

    const cards = await screen.findAllByTestId("recipe-card");
    expect(within(cards[0]).queryByTestId("drink-badge")).not.toBeInTheDocument();
    expect(within(cards[1]).queryByTestId("drink-badge")).not.toBeInTheDocument();
    expect(within(cards[1]).getByTestId("recipe-drink-notes")).toHaveTextContent("iced");
  });

  it("shows the brief's drink on a Recipe being designed, as the page heading does", async () => {
    getRecipes.mockResolvedValue({
      items: [
        designingRecipe({
          design_brief: {
            fork_profile_version_id: 7,
            usual_grind: "22",
            goal: "more body",
            grind_setting: "",
            drink: "latte",
            drink_notes: "",
          },
        }),
      ],
    });
    renderWithQueryClient(<RecipesPage />);
    const card = await screen.findByTestId("recipe-card");
    expect(await within(card).findByTestId("drink-badge")).toHaveTextContent("Latte");
  });
});
