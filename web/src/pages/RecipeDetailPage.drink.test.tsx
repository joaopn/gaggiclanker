import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RecipeDetailData, RecipeVersionRow } from "@/api/types";
import { RecipeDetailPage } from "@/pages/RecipeDetailPage";
import {
  designingDetail,
  labelCounts,
  recipeDetail,
  recipeRow,
  trends,
  version,
  vocabulary,
} from "@/test/recipesFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { signatureNone } from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));
vi.mock("react-chartjs-2", () => ({ Line: () => <canvas /> }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useParams: () => ({ recipeId: "3" }),
}));

const {
  getRecipe,
  getRecipeTrends,
  addRecipeVersion,
  getVocabulary,
  getKnowledgeInsights,
  getProfileVersions,
  getSignature,
  getSignatureOverrides,
} = vi.hoisted(() => ({
  getRecipe: vi.fn(),
  getRecipeTrends: vi.fn(),
  addRecipeVersion: vi.fn(),
  getVocabulary: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  getProfileVersions: vi.fn(),
  getSignature: vi.fn(),
  getSignatureOverrides: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getRecipe,
  getRecipeTrends,
  addRecipeVersion,
  getVocabulary,
  getKnowledgeInsights,
  getProfileVersions,
  getSignature,
  getSignatureOverrides,
}));

/** One entry of a Recipe's log, with the changes the server would say against its parent. */
function entry(
  overrides: Partial<RecipeVersionRow>,
  changes: RecipeDetailData["versions"][number]["changes"] = [],
): RecipeDetailData["versions"][number] {
  return {
    version: version(overrides),
    changes,
    shots: [],
    dead_end: false,
    labels: labelCounts(),
    outcome_proposal: null,
  };
}

/** A Cappuccino Recipe: v2 (Cappuccino, with notes) grew from v1 (Espresso, no notes). */
function cappuccino(drink: RecipeVersionRow["drink"] = "cappuccino", notes = "oat milk, ~150 ml") {
  return recipeDetail({
    recipe: recipeRow({ drink, drink_notes: notes }),
    versions: [
      entry(
        {
          id: 22,
          version_major: 2,
          is_current: true,
          parent_version_id: 21,
          parent_version_label: "v1",
          drink,
          drink_notes: notes,
        },
        [
          {
            field: "drink",
            label: "Drink",
            before: "Espresso",
            after: "Cappuccino",
            from_profile: false,
          },
          {
            field: "drink_notes",
            label: "Drink notes",
            before: null,
            after: notes,
            from_profile: false,
          },
        ],
      ),
      entry({ id: 21, drink: "espresso" }),
    ],
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  getRecipe.mockResolvedValue(cappuccino());
  getRecipeTrends.mockResolvedValue(trends());
  addRecipeVersion.mockResolvedValue(version());
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  getSignature.mockResolvedValue(signatureNone);
  getSignatureOverrides.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0 });
});

async function openForm() {
  const user = setupUser();
  renderWithQueryClient(<RecipeDetailPage />);
  await user.click(await screen.findByRole("button", { name: /Change something/ }));
  await screen.findByTestId("new-version-form");
  return user;
}

async function finish(user: ReturnType<typeof setupUser>) {
  await user.type(screen.getByLabelText("What are you trying?"), "a change");
  await user.click(screen.getByRole("button", { name: /^Record it as/ }));
  await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
  return addRecipeVersion.mock.calls[0][1] as Record<string, unknown>;
}

describe("Change something: the drink", () => {
  it("starts Drink and Drink notes filled with the current version's", async () => {
    await openForm();
    const select = (await screen.findByLabelText("Drink")) as HTMLSelectElement;
    expect(select.value).toBe("cappuccino");
    expect(screen.getByLabelText("Drink notes")).toHaveValue("oat milk, ~150 ml");
    // The departure from "empty keeps" is stated under the fields.
    expect(screen.getByTestId("drink-hint")).toHaveTextContent(
      "Shows the current drink; pick another to change it.",
    );
    expect(screen.getByTestId("drink-notes-hint")).toHaveTextContent(
      "Shows the current notes; edit them, or clear to remove.",
    );
  });

  it("sends neither when neither was touched", async () => {
    const user = await openForm();
    const patch = await finish(user);
    expect(patch).not.toHaveProperty("drink");
    expect(patch).not.toHaveProperty("drink_notes");
    expect(patch.major).toBe(false);
  });

  it("switches to major with the reason when another drink is picked, and sends only the drink", async () => {
    const user = await openForm();
    expect(screen.getByRole("checkbox", { name: "Major change" })).not.toBeChecked();
    expect(screen.queryByTestId("major-suggested")).not.toBeInTheDocument();

    await user.selectOptions(await screen.findByLabelText("Drink"), "latte");
    expect(screen.getByRole("checkbox", { name: "Major change" })).toBeChecked();
    expect(screen.getByTestId("major-suggested")).toHaveTextContent(
      "v3 suggested: the drink changes",
    );
    const patch = await finish(user);
    expect(patch).toMatchObject({ drink: "latte", major: true });
    expect(patch).not.toHaveProperty("drink_notes");
  });

  it("lets the person switch the suggestion back, and puts it back with the old drink", async () => {
    const user = await openForm();
    await user.selectOptions(await screen.findByLabelText("Drink"), "latte");
    await user.click(screen.getByRole("checkbox", { name: "Major change" }));
    expect(screen.getByRole("checkbox", { name: "Major change" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Record it as v2.1" })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Drink"), "cappuccino");
    expect(screen.queryByTestId("major-suggested")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Major change" })).not.toBeChecked();
    const patch = await finish(user);
    expect(patch).not.toHaveProperty("drink");
  });

  it("suggests nothing for notes alone, and sends the changed notes", async () => {
    const user = await openForm();
    const notes = screen.getByLabelText("Drink notes");
    await user.clear(notes);
    await user.type(notes, "whole milk, 120 ml");
    expect(screen.queryByTestId("major-suggested")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Major change" })).not.toBeChecked();
    const patch = await finish(user);
    expect(patch).toMatchObject({ drink_notes: "whole milk, 120 ml", major: false });
    expect(patch).not.toHaveProperty("drink");
  });

  it("sends an empty string when the notes are cleared", async () => {
    const user = await openForm();
    await user.clear(screen.getByLabelText("Drink notes"));
    const patch = await finish(user);
    expect(patch).toHaveProperty("drink_notes", "");
  });

  describe("a version that states no drink", () => {
    beforeEach(() => {
      getRecipe.mockResolvedValue(
        recipeDetail({
          versions: [entry({ id: 22, version_major: 2, is_current: true, drink: null })],
        }),
      );
    });

    it("offers Not stated, selected, and sends no drink for it", async () => {
      const user = await openForm();
      const select = (await screen.findByLabelText("Drink")) as HTMLSelectElement;
      expect(select.value).toBe("");
      expect(within(select).getAllByRole("option")[0]).toHaveTextContent("Not stated");
      const patch = await finish(user);
      // Never null: the server refuses a drink once stated being unstated, and an omitted
      // field is inherited.
      expect(patch).not.toHaveProperty("drink");
    });

    it("sends the drink once one is picked, as a major", async () => {
      const user = await openForm();
      await user.selectOptions(await screen.findByLabelText("Drink"), "espresso");
      expect(screen.getByTestId("major-suggested")).toBeInTheDocument();
      const patch = await finish(user);
      expect(patch).toMatchObject({ drink: "espresso", major: true });
    });
  });

  it("asks nothing about the drink while version 1 is being designed: the brief's wins", async () => {
    getRecipe.mockResolvedValue(designingDetail());
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);
    await user.click(await screen.findByRole("button", { name: /Write version 1 by hand/ }));
    await screen.findByTestId("new-version-form");
    expect(screen.queryByTestId("drink-fields")).not.toBeInTheDocument();
  });
});
