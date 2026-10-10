import type { DuplicateAnswer, DuplicatePreview } from "@/api/types";
import { setRow } from "@/test/setsFixtures";

/** What duplicating a version starts from: the default state, a profile and five insights. */
export function duplicatePreview(overrides: Partial<DuplicatePreview> = {}): DuplicatePreview {
  return {
    set_name: "Guji on the Niche (copy)",
    bean_id: 1,
    grinder_id: 1,
    recipe: {
      profile_label: "Blooming",
      grind_setting: "2.2",
      grind_value: 2.2,
      dose_g: 18,
      target_yield_g: 40,
      intent: "",
    },
    has_profile: true,
    insights: [
      { id: 11, text: "Grind finer than 2.3 runs sour on this bag", source: "user" },
      { id: 12, text: "Preinfusion under 6 s channels", source: "chat" },
      { id: 13, text: "40 g yield tastes thin", source: "chat" },
      { id: 14, text: "Cold cups bring out the sourness", source: "user" },
      { id: 15, text: "A wet puck means too fine", source: "chat" },
    ],
    ...overrides,
  };
}

/** What a duplicate answers with. */
export function duplicateAnswer(overrides: Partial<DuplicateAnswer> = {}): DuplicateAnswer {
  return {
    set: setRow({ id: 9, name: "Guji milk", version_count: 1, shot_count: 0 }),
    board_id: 31,
    ...overrides,
  };
}
