import { describe, expect, it } from "vitest";
import type { ChatThread, RecipeRow } from "@/api/types";
import {
  buildFolders,
  defaultFolderKey,
  GENERAL,
  latestRecipeId,
} from "@/components/chat/ConversationPicker";

function recipeRow(id: number, created_at: string, over: Partial<RecipeRow> = {}): RecipeRow {
  return {
    id,
    name: `Recipe ${id}`,
    created_at,
    current_version_label: "v1",
    ...over,
  } as RecipeRow;
}

function thread(id: number, recipe_id: number | null): ChatThread {
  return { id, recipe_id, title: `t${id}`, updated_at: "2026-03-01T10:00:00.000Z" } as ChatThread;
}

describe("latestRecipeId", () => {
  it("is the Recipe made last, whatever order the list serves them in", () => {
    const recipes = [
      recipeRow(1, "2026-01-01T00:00:00Z"),
      recipeRow(2, "2026-03-01T00:00:00Z"),
      recipeRow(3, "2026-02-01T00:00:00Z"),
    ];
    expect(latestRecipeId(recipes)).toBe(2);
  });

  it("breaks a tie on the time by the higher id, the later insert", () => {
    expect(
      latestRecipeId([recipeRow(7, "2026-01-01T00:00:00Z"), recipeRow(5, "2026-01-01T00:00:00Z")]),
    ).toBe(7);
    expect(
      latestRecipeId([recipeRow(5, "2026-01-01T00:00:00Z"), recipeRow(7, "2026-01-01T00:00:00Z")]),
    ).toBe(7);
  });

  it("is null with no Recipes", () => {
    expect(latestRecipeId([])).toBeNull();
  });
});

describe("defaultFolderKey", () => {
  const recipes = [recipeRow(1, "2026-01-01T00:00:00Z"), recipeRow(2, "2026-03-01T00:00:00Z")];

  it("opens the selected conversation's badge first, then a link's, then the newest Recipe", () => {
    const folders = buildFolders([thread(10, 1), thread(11, 99)], recipes);
    const pick = (selectedId: number | null, linkedRecipeId: number | null) =>
      defaultFolderKey({ folders, recipes, selectedId, linkedRecipeId });

    expect(pick(10, 2)).toBe("recipe-1");
    // A conversation whose Recipe is archived opens that Recipe's own badge.
    expect(pick(11, null)).toBe("archived-99");
    expect(pick(null, 1)).toBe("recipe-1");
    expect(pick(null, null)).toBe("recipe-2");
    // A link naming a Recipe with no badge falls through to the newest.
    expect(pick(null, 42)).toBe("recipe-2");
    // A selected id not in any list yet (just created) falls through too.
    expect(pick(77, null)).toBe("recipe-2");
  });

  it("is General when there are no Recipes", () => {
    expect(
      defaultFolderKey({
        folders: buildFolders([], []),
        recipes: [],
        selectedId: null,
        linkedRecipeId: null,
      }),
    ).toBe(GENERAL);
  });
});

describe("buildFolders", () => {
  it("puts the current version on a Recipe's badge, and none on a Recipe being designed", () => {
    const folders = buildFolders(
      [],
      [
        recipeRow(1, "2026-01-01T00:00:00Z", { current_version_label: "v1.2" }),
        recipeRow(2, "2026-02-01T00:00:00Z", { designing: true }),
      ],
    );
    expect(folders.map((folder) => folder.versionLabel)).toEqual([null, "v1.2", null]);
  });
});

describe("buildFolders, archived Recipes", () => {
  it("gives each archived Recipe its own folder, named from its conversations, after the live ones", () => {
    const threads = [
      { ...thread(1, 98), recipe_name: "Old decaf" },
      { ...thread(2, 99), recipe_name: "Finished Ethiopia" },
      { ...thread(3, 98), recipe_name: "Old decaf" },
      thread(4, 1),
    ] as ChatThread[];
    const folders = buildFolders(threads, [recipeRow(1, "2026-01-01T00:00:00Z")]);
    expect(
      folders.map((folder) => [folder.key, folder.label, folder.archived, folder.canCreate]),
    ).toEqual([
      ["general", "General", false, true],
      ["recipe-1", "Recipe 1", false, true],
      ["archived-98", "Old decaf", true, false],
      ["archived-99", "Finished Ethiopia", true, false],
    ]);
    expect(folders[2].threads.map((row) => row.id)).toEqual([1, 3]);
  });
});
