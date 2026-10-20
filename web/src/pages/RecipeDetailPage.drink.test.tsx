import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RecipeDetailData, RecipeVersionRow } from "@/api/types";
import { RecipeDetailPage } from "@/pages/RecipeDetailPage";
import {
  designingDetail,
  designingRecipe,
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
          // Served first, so the timeline's own ordering is what puts the drink line first.
          {
            field: "target_yield_g",
            label: "Target yield",
            before: "40 g",
            after: "32 g",
            from_profile: false,
          },
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

/** One entry of the log, found by its label once the log is on screen. */
async function entryOf(label: string): Promise<HTMLElement> {
  await screen.findAllByTestId("version-entry");
  const found = document.querySelector<HTMLElement>(
    `[data-testid="version-entry"][data-version="${label}"]`,
  );
  if (!found) throw new Error(`no ${label} in the log`);
  return found;
}

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

describe("the Recipe page: heading", () => {
  it("shows the badge beside the name and the notes under it", async () => {
    renderWithQueryClient(<RecipeDetailPage />);
    const heading = await screen.findByRole("heading", { level: 1 });
    // The heading stays the name alone; the badge is its neighbour.
    expect(heading).toHaveTextContent("Guji on the Niche");
    expect(heading).not.toHaveTextContent("Cappuccino");
    const badge = await screen.findAllByTestId("drink-badge");
    expect(heading.parentElement).toContainElement(badge[0]);
    expect(screen.getByTestId("recipe-drink-notes")).toHaveTextContent(
      "Drink notes: oat milk, ~150 ml",
    );
  });

  it("shows neither for a Recipe that states no drink", async () => {
    getRecipe.mockResolvedValue(
      recipeDetail({
        recipe: recipeRow({ drink: null, drink_notes: "" }),
        versions: [entry({ id: 22, version_major: 2, is_current: true, drink: null })],
      }),
    );
    renderWithQueryClient(<RecipeDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("drink-badge")).not.toBeInTheDocument();
    expect(screen.queryByTestId("recipe-drink-notes")).not.toBeInTheDocument();
  });

  it("shows the brief's drink while the Recipe is being designed, and no badge when the brief has none", async () => {
    const withBrief = designingRecipe({
      design_brief: {
        fork_profile_version_id: 7,
        usual_grind: "22",
        goal: "more body",
        grind_setting: "",
        drink: "latte",
        drink_notes: "iced",
      },
    });
    getRecipe.mockResolvedValue(designingDetail({ recipe: withBrief }));
    const { unmount } = renderWithQueryClient(<RecipeDetailPage />);
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(await screen.findByTestId("drink-badge")).toHaveTextContent("Latte");
    expect(heading.parentElement).toContainElement(screen.getByTestId("drink-badge"));
    expect(screen.getByTestId("recipe-drink-notes")).toHaveTextContent("Drink notes: iced");
    unmount();

    getRecipe.mockResolvedValue(designingDetail());
    renderWithQueryClient(<RecipeDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("drink-badge")).not.toBeInTheDocument();
  });
});

describe("the Recipe page: version timeline", () => {
  it("gives each version its badge and names the change in one line", async () => {
    renderWithQueryClient(<RecipeDetailPage />);
    const v2 = await entryOf("v2");
    expect(within(v2).getByTestId("drink-badge")).toHaveTextContent("Cappuccino");
    expect(within(v2).getByTestId("drink-change")).toHaveTextContent(
      "Drink: Espresso → Cappuccino · notes added",
    );
    // The drink comes first among the changes, as on the change card.
    const kinds = within(v2).getByTestId("version-changes").querySelectorAll("li");
    expect(kinds[0]).toHaveAttribute("data-testid", "drink-change");
    // The two are one line, not two chips of the generic kind: the yield is the only chip.
    const chips = within(v2).getAllByTestId("change");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent("Target yield");

    const v1 = document.querySelector<HTMLElement>(
      `[data-testid="version-entry"][data-version="v1"]`,
    );
    expect(within(v1 as HTMLElement).getByTestId("drink-badge")).toHaveTextContent("Espresso");
    expect(within(v1 as HTMLElement).queryByTestId("drink-change")).not.toBeInTheDocument();
  });

  it("names only the new drink when the parent stated none, and a notes change by its verb", async () => {
    getRecipe.mockResolvedValue(
      recipeDetail({
        versions: [
          entry(
            { id: 23, version_major: 2, version_minor: 1, is_current: true, drink: "cappuccino" },
            [
              {
                field: "drink_notes",
                label: "Drink notes",
                before: "oat",
                after: null,
                from_profile: false,
              },
            ],
          ),
          entry({ id: 22, version_major: 2, drink: "cappuccino" }, [
            {
              field: "drink",
              label: "Drink",
              before: null,
              after: "Cappuccino",
              from_profile: false,
            },
          ]),
          entry({ id: 21, drink: null }),
        ],
      }),
    );
    renderWithQueryClient(<RecipeDetailPage />);
    const v21 = await entryOf("v2.1");
    expect(within(v21).getByTestId("drink-change")).toHaveTextContent(/^notes removed$/);
    const v2 = document.querySelector<HTMLElement>(
      `[data-testid="version-entry"][data-version="v2"]`,
    );
    expect(within(v2 as HTMLElement).getByTestId("drink-change")).toHaveTextContent(
      /^Drink: Cappuccino$/,
    );
    // A version that states no drink has no badge: there is no "unknown" one.
    const v1 = document.querySelector<HTMLElement>(
      `[data-testid="version-entry"][data-version="v1"]`,
    );
    expect(within(v1 as HTMLElement).queryByTestId("drink-badge")).not.toBeInTheDocument();
  });
});

describe("the Recipe page: what records a version", () => {
  it("names the drink and its notes beside the grind, the dose and the profile", async () => {
    renderWithQueryClient(<RecipeDetailPage />);
    expect(
      await screen.findByText(
        /Changing the grind, the dose, the profile, the drink or its notes records a new version/,
      ),
    ).toBeInTheDocument();
  });

  it("says a change of drink is a major change in the Major change explanation", async () => {
    await openForm();
    expect(screen.getByTestId("major-choice-help")).toHaveTextContent(
      "A functional change to what the profile does, or a change of drink, is a major change; a new grind, dose or yield, or a tuned copy of the profile, is a minor one.",
    );
  });
});
