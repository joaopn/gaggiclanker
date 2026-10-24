import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { activeRecipes, RecipeChatBar, setChatQuestion } from "@/components/shots/RecipeChatBar";
import { recipeRow } from "@/test/recipesFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

/** The Chat page's query string a bar button links to, as a map. */
function linkParams(link: HTMLElement): Record<string, string> {
  const href = link.getAttribute("href") ?? "";
  expect(href.startsWith("/chat?")).toBe(true);
  return Object.fromEntries(new URLSearchParams(href.slice("/chat?".length)));
}

describe("activeRecipes", () => {
  it("keeps the Recipes being brewed, in the order the list serves them", () => {
    const recipes = [
      recipeRow({ id: 5, name: "Guji" }),
      recipeRow({ id: 4, name: "Old bag", archived: true }),
      recipeRow({ id: 3, name: "Being designed", designing: true }),
      recipeRow({ id: 2, name: "No version", current_version_id: null, current_version_label: "" }),
      recipeRow({ id: 1, name: "House blend" }),
    ];

    expect(activeRecipes(recipes).map((row) => row.id)).toEqual([5, 1]);
  });
});

describe("setChatQuestion", () => {
  it("names the version and asks what the judged shots show", () => {
    expect(setChatQuestion("v1.2")).toBe(
      "I've judged my latest shots on v1.2. What do they show, and what should I change next?",
    );
  });
});

describe("RecipeChatBar", () => {
  it("links every active Recipe to its current version's conversation with the question typed", () => {
    renderWithQueryClient(
      <RecipeChatBar
        recipes={[
          recipeRow({
            id: 5,
            name: "Guji on the Niche",
            current_version_id: 51,
            current_version_label: "v4",
          }),
          recipeRow({ id: 4, name: "Kenya AA", archived: true }),
          recipeRow({
            id: 2,
            name: "House blend",
            current_version_id: 21,
            current_version_label: "v1",
          }),
        ]}
      />,
    );

    const bar = screen.getByRole("navigation", { name: "Chat about a Recipe" });
    const links = within(bar).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Guji on the Niche · v4",
      "House blend · v1",
    ]);
    // The version is what makes the Chat page open or continue that version's
    // own conversation rather than only pointing at the Recipe.
    expect(linkParams(links[0])).toEqual({
      recipe: "5",
      version: "51",
      ask: setChatQuestion("v4"),
    });
    expect(linkParams(links[1])).toEqual({
      recipe: "2",
      version: "21",
      ask: setChatQuestion("v1"),
    });
  });

  it("draws nothing when no Recipe is active", () => {
    renderWithQueryClient(
      <RecipeChatBar
        recipes={[recipeRow({ archived: true }), recipeRow({ id: 9, designing: true })]}
      />,
    );

    expect(screen.queryByTestId("recipe-chat-bar")).not.toBeInTheDocument();
  });
});
