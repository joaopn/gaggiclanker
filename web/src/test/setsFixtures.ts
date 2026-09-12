import type {
  BeanRow,
  GrinderRow,
  SetDetailData,
  SetRow,
  SetTrends,
  SetVersionRow,
  ShotJudgement,
  Vocabulary,
} from "@/api/types";

/**
 * The shapes the Set and judgement components are handed, built once.
 *
 * Hand-written rather than captured from the server, unlike the shot-129
 * fixture: these rows carry no derived numbers — no diagnostics, no score — so
 * there is nothing a recording would prove that a literal does not. What
 * matters is that they match the pydantic models field for field, which the
 * generated types enforce at compile time.
 */

export const vocabulary: Vocabulary = {
  roast_levels: [
    { value: "light", label: "light" },
    { value: "medium-light", label: "medium light" },
    { value: "medium", label: "medium" },
    { value: "medium-dark", label: "medium dark" },
    { value: "dark", label: "dark" },
  ],
  processes: [
    { value: "washed", label: "washed" },
    { value: "natural", label: "natural" },
    { value: "honey", label: "honey" },
    { value: "anaerobic", label: "anaerobic" },
    { value: "other", label: "other" },
  ],
  burr_types: [
    { value: "conical", label: "conical" },
    { value: "flat", label: "flat" },
    { value: "unknown", label: "unknown" },
  ],
  step_units: [
    { value: "clicks", label: "clicks" },
    { value: "numbers", label: "numbers" },
    { value: "microns", label: "microns" },
    { value: "free", label: "free" },
  ],
  balances: [
    { value: "sour", label: "Sour — under-extracted" },
    { value: "balanced", label: "Balanced" },
    { value: "bitter", label: "Bitter — over-extracted" },
  ],
  decisions: [
    { value: "keep", label: "Keep this recipe" },
    { value: "adjust", label: "Adjust and pull again" },
    { value: "discard", label: "Discard — something went wrong" },
  ],
  origins: [
    { value: "manual", label: "You changed it" },
    { value: "analysis", label: "From an analysis" },
    { value: "chat", label: "From the chat" },
  ],
  taste_groups: [
    {
      value: "sour",
      label: "Sour side",
      meaning: "Under-extracted: the water left before it had taken the sugars.",
      tags: [
        {
          value: "sour",
          label: "sour",
          meaning: "Puckering, lemon-juice acidity with nothing behind it.",
        },
        {
          value: "salty",
          label: "salty",
          meaning: "A faint saline note — the classic under-extraction tell.",
        },
      ],
    },
    {
      value: "bitter",
      label: "Bitter side",
      meaning: "Over-extracted: the water kept going.",
      tags: [
        {
          value: "astringent",
          label: "astringent",
          meaning: "Mouth-drying grip, like over-steeped black tea.",
        },
      ],
    },
  ],
};

export function bean(overrides: Partial<BeanRow> = {}): BeanRow {
  return {
    id: 1,
    name: "Ethiopia Guji",
    roaster: "Hasbean",
    origin: "Ethiopia",
    variety: null,
    altitude_m: 1900,
    process: "natural",
    roast_level: "light",
    roast_date: "2026-04-01",
    decaf: false,
    tasting_notes_bag: "blueberry, jasmine",
    notes: "",
    archived: false,
    created_at: "2026-04-01T00:00:00.000Z",
    set_count: 1,
    ...overrides,
  };
}

export function grinder(overrides: Partial<GrinderRow> = {}): GrinderRow {
  return {
    id: 1,
    name: "Niche Zero",
    model: "NZ",
    burr_type: "conical",
    step_unit: "numbers",
    notes: "",
    created_at: "2026-04-01T00:00:00.000Z",
    set_count: 1,
    ...overrides,
  };
}

export function setRow(overrides: Partial<SetRow> = {}): SetRow {
  return {
    id: 3,
    name: "Guji on the Niche",
    bean_id: 1,
    bean_name: "Ethiopia Guji",
    bean_roast_date: "2026-04-01",
    machine_id: 1,
    machine_name: "kitchen",
    grinder_id: 1,
    grinder_name: "Niche Zero",
    status: "active",
    active: true,
    created_at: "2026-04-02T00:00:00.000Z",
    current_version_id: 22,
    current_version_no: 2,
    version_count: 2,
    shot_count: 4,
    profile_version_id: 7,
    profile_label: "9 Bar Espresso",
    ...overrides,
  };
}

export function version(overrides: Partial<SetVersionRow> = {}): SetVersionRow {
  return {
    id: 21,
    set_id: 3,
    version_no: 1,
    parent_version_id: null,
    profile_version_id: 7,
    profile_label: "9 Bar Espresso",
    grind_setting: "22",
    grind_value: 22,
    dose_g: 18,
    target_yield_g: 36,
    target_temperature_c: 93,
    intent: "",
    origin: "manual",
    origin_analysis_id: null,
    created_at: "2026-04-02T00:00:00.000Z",
    shot_count: 2,
    ...overrides,
  };
}

export function judgement(overrides: Partial<ShotJudgement> = {}): ShotJudgement {
  return {
    shot_id: 1,
    rating: 4,
    balance: "sour",
    taste_tags: ["sour"],
    dose_in_g: 18,
    dose_out_g: 36,
    grind_setting: "22",
    notes: "sharp at the end",
    decision: "adjust",
    seeded_from_device_note: false,
    device_synced_at: null,
    updated_at: "2026-04-03T08:00:00.000Z",
    ratio: 2,
    ...overrides,
  };
}

export function setDetail(overrides: Partial<SetDetailData> = {}): SetDetailData {
  return {
    set: setRow(),
    versions: [
      {
        version: version({
          id: 22,
          version_no: 2,
          parent_version_id: 21,
          grind_setting: "21",
          grind_value: 21,
          intent: "one click finer, chasing the sourness out",
        }),
        changes: [{ field: "grind_setting", label: "Grind", before: "22", after: "21" }],
        shots: [],
      },
      { version: version(), changes: [], shots: [] },
    ],
    judgements: {},
    ...overrides,
  };
}

export function trends(overrides: Partial<SetTrends> = {}): SetTrends {
  return {
    set_id: 3,
    versions: [
      {
        set_version_id: 21,
        version_no: 1,
        intent: "",
        origin: "manual",
        created_at: "2026-04-02T00:00:00.000Z",
        shots: 2,
        avg_execution_score: 7,
        avg_duration_s: 27,
        avg_ratio: 2,
        avg_rating: 3,
      },
      {
        set_version_id: 22,
        version_no: 2,
        intent: "one click finer",
        origin: "manual",
        created_at: "2026-04-03T00:00:00.000Z",
        shots: 2,
        avg_execution_score: 9,
        avg_duration_s: 29,
        avg_ratio: 2,
        avg_rating: 5,
      },
    ],
    shots: [
      point(1, 21, 1, { execution_score: 6.5, rating: 3 }),
      point(2, 21, 1, { execution_score: 7.5, rating: null }),
      point(3, 22, 2, { execution_score: 9, rating: 5 }),
      point(4, 22, 2, { execution_score: 9, rating: 5 }),
    ],
    ...overrides,
  };
}

function point(
  id: number,
  versionId: number,
  versionNo: number,
  overrides: Partial<SetTrends["shots"][number]> = {},
): SetTrends["shots"][number] {
  return {
    shot_id: id,
    device_id: String(id).padStart(6, "0"),
    set_version_id: versionId,
    version_no: versionNo,
    started_at: "2026-04-03T08:00:00.000Z",
    execution_score: 8,
    duration_s: 28,
    ratio: 2,
    rating: 4,
    ...overrides,
  };
}
