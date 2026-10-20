import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecipesPage } from "@/pages/RecipesPage";
import { designingRecipe, recipeRow } from "@/test/recipesFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getRecipes, setAutomatch, getBeans, getGrinders, getMachines, getProfileVersions } =
  vi.hoisted(() => ({
    getRecipes: vi.fn(),
    setAutomatch: vi.fn(),
    getBeans: vi.fn(),
    getGrinders: vi.fn(),
    getMachines: vi.fn(),
    getProfileVersions: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getRecipes,
  setAutomatch,
  getBeans,
  getGrinders,
  getMachines,
  getProfileVersions,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getRecipes.mockResolvedValue({
    items: [
      recipeRow(),
      recipeRow({
        id: 4,
        name: "Kenya on the DF64",
        automatch: false,
        shot_count: 0,
        bean_name: "Kenya",
      }),
    ],
  });
  setAutomatch.mockResolvedValue(recipeRow({ id: 4, automatch: true }));
  getBeans.mockResolvedValue({ items: [] });
  getGrinders.mockResolvedValue({ items: [] });
  getMachines.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
});

describe("RecipesPage", () => {
  it("cards the identity, the counts and which ones collect new shots", async () => {
    renderWithQueryClient(<RecipesPage />);

    const cards = await screen.findAllByTestId("recipe-card");
    expect(cards).toHaveLength(2);
    // bean · grinder · profile vN, which is what identifies a Recipe at a glance.
    expect(cards[0]).toHaveTextContent("Ethiopia Guji · Niche Zero · 9 Bar Espresso v2");
    expect(cards[0]).toHaveTextContent("4 shots");
    expect(within(cards[0]).getByTestId("recipe-automatch")).toBeInTheDocument();
    expect(within(cards[1]).queryByTestId("recipe-automatch")).not.toBeInTheDocument();
  });

  it("turns matching on for one Recipe and off for another, touching nothing else", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipesPage />);

    const cards = await screen.findAllByTestId("recipe-card");
    await user.click(within(cards[1]).getByRole("button", { name: /File matching shots here/ }));

    await waitFor(() => expect(setAutomatch).toHaveBeenCalled());
    expect(setAutomatch.mock.calls[0]).toEqual([4, true]);

    // The Recipe already collecting offers the other direction, and no Recipe is
    // switched off by another being switched on.
    await user.click(within(cards[0]).getByRole("button", { name: /Stop filing shots here/ }));
    await waitFor(() => expect(setAutomatch).toHaveBeenCalledTimes(2));
    expect(setAutomatch.mock.calls[1]).toEqual([3, false]);
  });

  it("explains what a shot with no Recipe costs when there are none", async () => {
    getRecipes.mockResolvedValue({ items: [] });
    renderWithQueryClient(<RecipesPage />);

    expect(await screen.findByText("No Recipes yet")).toBeInTheDocument();
    expect(screen.getByText(/needs a Recipe/)).toBeInTheDocument();
  });

  it("opens the New Recipe dialog from the header", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipesPage />);

    await user.click(await screen.findByRole("button", { name: /New Recipe/ }));

    expect(await screen.findByTestId("new-recipe-dialog")).toBeInTheDocument();
  });
});

describe("RecipesPage, a Recipe being designed", () => {
  it("carries the badge and Continue designing in place of the recipe", async () => {
    getRecipes.mockResolvedValue({ items: [designingRecipe(), recipeRow()] });
    renderWithQueryClient(<RecipesPage />);

    const cards = await screen.findAllByTestId("recipe-card");
    expect(within(cards[0]).getByTestId("recipe-designing")).toHaveTextContent("Designing");
    expect(within(cards[0]).getByTestId("recipe-design-summary")).toHaveTextContent(
      "Ethiopia Guji · Niche Zero · no version yet",
    );
    // v1's own conversation, through the route Discuss opens or continues.
    expect(within(cards[0]).getByTestId("continue-designing")).toHaveAttribute(
      "href",
      "/chat?recipe=6&version=60",
    );

    // The Recipe beside it has a recipe, and none of this.
    expect(within(cards[1]).queryByTestId("recipe-designing")).not.toBeInTheDocument();
    expect(within(cards[1]).queryByTestId("continue-designing")).not.toBeInTheDocument();
    expect(cards[1]).toHaveTextContent("Ethiopia Guji · Niche Zero · 9 Bar Espresso v2");
  });

  it("offers no automatch on a Recipe with no profile to match on yet", async () => {
    // Both collect shots, as every new Recipe does; only one has a profile.
    getRecipes.mockResolvedValue({ items: [designingRecipe({ automatch: true }), recipeRow()] });
    renderWithQueryClient(<RecipesPage />);

    const cards = await screen.findAllByTestId("recipe-card");
    expect(within(cards[0]).queryByTestId("recipe-automatch")).not.toBeInTheDocument();
    expect(
      within(cards[0]).queryByRole("button", { name: /filing shots here|matching shots here/ }),
    ).not.toBeInTheDocument();
    expect(within(cards[1]).getByTestId("recipe-automatch")).toBeInTheDocument();
    expect(
      within(cards[1]).getByRole("button", { name: /Stop filing shots here/ }),
    ).toBeInTheDocument();
  });

  describe("How a Recipe works", () => {
    function explainer() {
      const section = screen.getByTestId("recipes-explainer");
      const button = within(section).getByRole("button", { name: "How a Recipe works" });
      const region = document.getElementById(String(button.getAttribute("aria-controls")));
      return { button, region: region as HTMLElement };
    }

    it("is open while nothing is being dialled in", async () => {
      getRecipes.mockResolvedValue({ items: [] });
      renderWithQueryClient(<RecipesPage />);

      await screen.findByText("No Recipes yet");
      const { button, region } = explainer();
      expect(button).toHaveAttribute("aria-expanded", "true");
      expect(region).not.toHaveAttribute("hidden");
      // Per bean, not per bag: a second bag of the same coffee is the same Recipe.
      expect(region).toHaveTextContent("A Recipe is how you dial in one bean.");
      expect(region).toHaveTextContent(
        "The Recipe doesn't copy any of that. It follows the profile.",
      );
    });

    it("counts only Recipes that are not archived as something being dialled in", async () => {
      getRecipes.mockResolvedValue({ items: [recipeRow({ archived: true, automatch: false })] });
      renderWithQueryClient(<RecipesPage />);

      await screen.findAllByTestId("recipe-card");
      expect(explainer().button).toHaveAttribute("aria-expanded", "true");
    });

    it("is closed once a Recipe exists, and its text is still there to open", async () => {
      renderWithQueryClient(<RecipesPage />);

      await screen.findAllByTestId("recipe-card");
      const { button, region } = explainer();
      expect(button).toHaveAttribute("aria-expanded", "false");
      // Always rendered, toggled with `hidden`, so aria-controls resolves.
      expect(region).toHaveAttribute("hidden");
    });

    it("remembers a choice made by hand, in either direction", async () => {
      const user = setupUser();
      const { unmount } = renderWithQueryClient(<RecipesPage />);
      await screen.findAllByTestId("recipe-card");
      await user.click(explainer().button);
      expect(explainer().button).toHaveAttribute("aria-expanded", "true");
      expect(window.localStorage.getItem("recipes.explainer.v1")).toBe("1");
      unmount();

      renderWithQueryClient(<RecipesPage />);
      await screen.findAllByTestId("recipe-card");
      expect(explainer().button).toHaveAttribute("aria-expanded", "true");
      await user.click(explainer().button);
      expect(window.localStorage.getItem("recipes.explainer.v1")).toBe("0");
    });

    it("reads the choice stored under the old key and writes only the new one", async () => {
      window.localStorage.setItem("sets.explainer.v1", "1");
      const user = setupUser();
      renderWithQueryClient(<RecipesPage />);
      await screen.findAllByTestId("recipe-card");
      expect(explainer().button).toHaveAttribute("aria-expanded", "true");
      await user.click(explainer().button);
      expect(window.localStorage.getItem("recipes.explainer.v1")).toBe("0");
      expect(window.localStorage.getItem("sets.explainer.v1")).toBe("1");
    });

    it("prefers the new key when both hold a choice", async () => {
      window.localStorage.setItem("sets.explainer.v1", "1");
      window.localStorage.setItem("recipes.explainer.v1", "0");
      renderWithQueryClient(<RecipesPage />);
      await screen.findAllByTestId("recipe-card");
      expect(explainer().button).toHaveAttribute("aria-expanded", "false");
    });

    it("keeps a closed choice even when there are no Recipes", async () => {
      window.localStorage.setItem("recipes.explainer.v1", "0");
      getRecipes.mockResolvedValue({ items: [] });
      renderWithQueryClient(<RecipesPage />);

      await screen.findByText("No Recipes yet");
      expect(explainer().button).toHaveAttribute("aria-expanded", "false");
    });
  });
});
