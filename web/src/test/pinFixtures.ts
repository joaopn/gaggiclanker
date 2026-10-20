import type { BoardRowPin, PinDetail, PinPreview, RecipePin } from "@/api/types";

/** A live pin as the pin routes answer it. */
export function pinDetail(overrides: Partial<PinDetail> = {}): PinDetail {
  return {
    id: 5,
    recipe_version_id: 22,
    board_id: 7,
    profile_version_id: 901,
    pinned_at: "2026-10-03T09:00:00Z",
    unpinned_at: null,
    recipe_id: 3,
    recipe_name: "Ethiopia Guji · Blooming",
    recipe_archived: false,
    version_label: "v1.2",
    original_version_id: 40,
    original_label: "Blooming",
    name: "Blooming v1.2",
    shot_count: 14,
    ...overrides,
  };
}

/** One line of the Recipe page's "Pinned on the machine" card. */
export function recipePin(overrides: Partial<RecipePin> = {}): RecipePin {
  return {
    pin_id: 5,
    recipe_version_id: 22,
    version_label: "v1.2",
    name: "Blooming v1.2",
    board_id: 7,
    pinned_at: "2026-10-03T09:00:00Z",
    shot_count: 14,
    ...overrides,
  };
}

/** The pin a profile-list row carries. */
export function boardRowPin(overrides: Partial<BoardRowPin> = {}): BoardRowPin {
  return {
    pin_id: 5,
    recipe_id: 3,
    recipe_name: "Ethiopia Guji · Blooming",
    recipe_version_id: 22,
    version_label: "v1.2",
    original_label: "Blooming",
    original_version_id: 40,
    shot_count: 14,
    ...overrides,
  };
}

/** What pinning a version would do: the default state, nothing in the way. */
export function pinPreview(overrides: Partial<PinPreview> = {}): PinPreview {
  return {
    name: "Blooming v1.2",
    taken: false,
    refused: null,
    clamp_changes: [],
    ...overrides,
  };
}
