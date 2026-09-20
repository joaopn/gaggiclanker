import type {
  BeanRow,
  FlavorPicks,
  GrinderRow,
  MeasureSpread,
  SetDetailData,
  SetRow,
  SetTrends,
  SetVersionDetail,
  SetVersionRow,
  ShotJudgement,
  SimilarSet,
  SpreadMeasure,
  StartingPointOption,
  StartingPointRun,
  VersionEvidence,
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
    { value: "sour", label: "Sour" },
    { value: "balanced", label: "Balanced" },
    { value: "bitter", label: "Bitter" },
  ],
  decisions: [
    { value: "keep", label: "Keep" },
    { value: "improve", label: "Improve" },
    { value: "discard", label: "Discard" },
  ],
  origins: [
    { value: "manual", label: "You changed it" },
    { value: "analysis", label: "From an analysis" },
    { value: "chat", label: "From the chat" },
    { value: "starting_point", label: "From the starting-point wizard" },
  ],
  version_outcomes: [
    { value: "held", label: "Held" },
    { value: "partly_held", label: "Partly held" },
    { value: "failed", label: "Failed" },
    { value: "inconclusive", label: "Inconclusive" },
  ],
  // The six measures the spread and the evidence are reported per, with the
  // unit each one is read in — the server's list, not a copy typed here.
  spread_measures: [
    { value: "shot_time_s", label: "Shot time", unit: "s", decimals: 1, difference_decimals: 2 },
    {
      value: "first_drip_s",
      label: "Time to first drip",
      unit: "s",
      decimals: 1,
      difference_decimals: 2,
    },
    { value: "yield_g", label: "Yield", unit: "g", decimals: 1, difference_decimals: 2 },
    {
      value: "peak_pressure_bar",
      label: "Peak pressure",
      unit: "bar",
      decimals: 2,
      difference_decimals: 3,
    },
    {
      value: "brew_flow_ml_s",
      label: "Average brew flow",
      unit: "ml/s",
      decimals: 2,
      difference_decimals: 3,
    },
    { value: "rating", label: "Rating", unit: "", decimals: 1, difference_decimals: 2 },
  ],
  outcome_states: [
    { value: "no_prediction", label: "No prediction" },
    { value: "open", label: "Open" },
    { value: "held", label: "Held" },
    { value: "partly_held", label: "Partly held" },
    { value: "failed", label: "Failed" },
    { value: "inconclusive", label: "Inconclusive" },
  ],
  // The analyzer's own closed sets, served from the same endpoint for the same reason:
  // the suggestion cards and the Knowledge page render these words.
  shot_styles: [
    { value: "classic", label: "Classic" },
    { value: "bloom", label: "Bloom" },
    { value: "lever", label: "Lever" },
  ],
  suggestion_variables: [
    { value: "grind", label: "Grind" },
    { value: "dose", label: "Dose in" },
    { value: "yield", label: "Yield out" },
    { value: "temperature", label: "Temperature" },
    { value: "pressure", label: "Pressure" },
  ],
  // The three a Set version records. The server refuses an accept for anything
  // else, which is why the card reads this list rather than one of its own.
  actionable_variables: ["grind", "dose", "yield"],
  suggestion_directions: [
    { value: "finer", label: "finer" },
    { value: "coarser", label: "coarser" },
    { value: "increase", label: "increase" },
    { value: "decrease", label: "decrease" },
    { value: "hold", label: "hold" },
  ],
  suggestion_units: [
    { value: "grinder_steps", label: "grinder steps" },
    { value: "g", label: "g" },
    { value: "c", label: "°C" },
    { value: "bar", label: "bar" },
    { value: "none", label: "—" },
  ],
  suggestion_statuses: [
    { value: "open", label: "open" },
    { value: "accepted", label: "accepted" },
    { value: "rejected", label: "rejected" },
    { value: "superseded", label: "superseded" },
  ],
  rule_categories: [
    { value: "dial_in_order", label: "dial in order" },
    { value: "increments", label: "increments" },
  ],
  rule_confidences: [
    { value: "expert", label: "Expert heuristic" },
    { value: "calibrated", label: "Calibrated on real shots" },
  ],
  // A slice of the wheel, not all 110 notes: every tier, a category whose
  // group and category share a label, and a group with no notes outside it.
  flavor_wheel: [
    {
      value: "floral",
      label: "Floral",
      children: [
        { value: "floral.black_tea", label: "Black tea", children: [] },
        {
          value: "floral.floral",
          label: "Floral",
          children: [{ value: "floral.floral.jasmine", label: "Jasmine", children: [] }],
        },
      ],
    },
    {
      value: "fruity",
      label: "Fruity",
      children: [
        {
          value: "fruity.berry",
          label: "Berry",
          children: [
            { value: "fruity.berry.blackberry", label: "Blackberry", children: [] },
            { value: "fruity.berry.raspberry", label: "Raspberry", children: [] },
          ],
        },
        {
          value: "fruity.citrus_fruit",
          label: "Citrus fruit",
          children: [{ value: "fruity.citrus_fruit.lemon", label: "Lemon", children: [] }],
        },
      ],
    },
    {
      value: "sour_fermented",
      label: "Sour/Fermented",
      children: [
        {
          value: "sour_fermented.sour",
          label: "Sour",
          children: [
            { value: "sour_fermented.sour.citric_acid", label: "Citric acid", children: [] },
          ],
        },
      ],
    },
    {
      value: "other",
      label: "Other",
      children: [
        {
          value: "other.chemical",
          label: "Chemical",
          children: [
            { value: "other.chemical.bitter", label: "Bitter", children: [] },
            { value: "other.chemical.salty", label: "Salty", children: [] },
          ],
        },
      ],
    },
    {
      value: "nutty_cocoa",
      label: "Nutty/Cocoa",
      children: [
        {
          value: "nutty_cocoa.cocoa",
          label: "Cocoa",
          children: [{ value: "nutty_cocoa.cocoa.chocolate", label: "Chocolate", children: [] }],
        },
      ],
    },
    {
      value: "sweet",
      label: "Sweet",
      children: [
        {
          value: "sweet.brown_sugar",
          label: "Brown sugar",
          children: [{ value: "sweet.brown_sugar.honey", label: "Honey", children: [] }],
        },
        { value: "sweet.vanilla", label: "Vanilla", children: [] },
      ],
    },
  ],
};

/** What the shot panel offers, in wheel order: a short list per row. */
export function flavorPicks(overrides: Partial<FlavorPicks> = {}): FlavorPicks {
  return {
    taste: ["fruity.berry", "other.chemical.bitter", "nutty_cocoa.cocoa.chocolate"],
    aroma: ["floral", "fruity.berry"],
    ...overrides,
  };
}

export function bean(overrides: Partial<BeanRow> = {}): BeanRow {
  return {
    id: 1,
    name: "Ethiopia Guji",
    roaster: "Hasbean",
    origin: "Ethiopia",
    process: "natural",
    roast_level: "light",
    decaf: false,
    description: "blueberry, jasmine",
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
    profile_temperature_c: 93,
    intent: "",
    origin: "manual",
    origin_analysis_id: null,
    prediction: "",
    compares_to_version_id: null,
    compares_to_version_no: null,
    restores_version_id: null,
    restores_version_no: null,
    prediction_at: null,
    outcome: null,
    outcome_note: "",
    outcome_at: null,
    outcome_state: "no_prediction",
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
    taste_notes: ["sour_fermented.sour"],
    aroma_notes: ["fruity.berry"],
    dose_in_g: 18,
    dose_out_g: 36,
    grind_setting: "22",
    notes: "sharp at the end",
    decision: "improve",
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
        changes: [
          {
            field: "grind_setting",
            label: "Grind",
            before: "22",
            after: "21",
            from_profile: false,
          },
        ],
        shots: [],
        dead_end: false,
        labels: labelCounts(),
      },
      { version: version(), changes: [], shots: [], dead_end: false, labels: labelCounts() },
    ],
    judgements: {},
    track_record: trackRecord(),
    spread: spreadReport(),
    rollback_target_version_id: null,
    ...overrides,
  };
}

export function labelCounts(
  overrides: Partial<SetVersionDetail["labels"]> = {},
): SetVersionDetail["labels"] {
  return { keep: 0, improve: 0, discard: 0, unlabelled: 0, ...overrides };
}

export function trackRecord(
  overrides: Partial<SetDetailData["track_record"]> = {},
): SetDetailData["track_record"] {
  return {
    no_prediction: 0,
    open: 0,
    held: 0,
    partly_held: 0,
    failed: 0,
    inconclusive: 0,
    graded: 0,
    ...overrides,
  };
}

/** One line of the Spread block. Nothing recorded and nothing repeated by default. */
export function measureSpread(
  measure: SpreadMeasure,
  floor: number,
  overrides: Partial<MeasureSpread> = {},
): MeasureSpread {
  return {
    measure,
    value: null,
    measured: false,
    shots: 0,
    degrees_of_freedom: 0,
    recorded: 0,
    floor,
    ...overrides,
  };
}

/**
 * A Set's spread: shot time measured, first drip recorded but never repeated,
 * and the three the archive holds nothing for.
 *
 * The three states a line can be in, in one fixture, because the page's job is
 * to tell them apart: a measured number with its basis, "not measured yet" with
 * the floor that stands in for it, and a measure that is left out entirely.
 */
export function spreadReport(
  overrides: Partial<Record<SpreadMeasure, Partial<MeasureSpread>>> = {},
) {
  const base: Array<[SpreadMeasure, number, Partial<MeasureSpread>]> = [
    [
      "shot_time_s",
      2,
      { value: 1.8, measured: true, shots: 9, degrees_of_freedom: 5, recorded: 9 },
    ],
    ["first_drip_s", 1, { recorded: 4 }],
    ["yield_g", 1, { value: 0.4, shots: 4, degrees_of_freedom: 2, recorded: 4 }],
    ["peak_pressure_bar", 0.3, {}],
    ["brew_flow_ml_s", 0.2, {}],
    ["rating", 0.5, { value: 0.5, measured: true, shots: 9, degrees_of_freedom: 5, recorded: 9 }],
  ];
  return base.map(([measure, floor, preset]) =>
    measureSpread(measure, floor, { ...preset, ...(overrides[measure] ?? {}) }),
  );
}

/** A version's evidence: this version's shots against the compared version's. */
export function evidence(overrides: Partial<VersionEvidence> = {}): VersionEvidence {
  return {
    version_id: 22,
    compares_to_version_id: 21,
    measures: [
      {
        measure: "shot_time_s",
        this: { mean: 34.2, n: 3 },
        other: { mean: 29.1, n: 4 },
        difference: 5.13,
        verdict: "beyond",
        yardstick: 2.41,
      },
      {
        measure: "first_drip_s",
        this: { mean: 6.4, n: 3 },
        other: { mean: 6.1, n: 4 },
        difference: 0.28,
        verdict: "inside",
        yardstick: 1,
      },
      {
        measure: "yield_g",
        this: { mean: 36.2, n: 3 },
        other: { mean: 36, n: 4 },
        difference: 0.17,
        verdict: "inside",
        yardstick: 1,
      },
      {
        measure: "peak_pressure_bar",
        this: { mean: null, n: 0 },
        other: { mean: null, n: 0 },
        difference: null,
        verdict: "no_data",
        yardstick: null,
      },
      {
        measure: "brew_flow_ml_s",
        this: { mean: null, n: 0 },
        other: { mean: null, n: 0 },
        difference: null,
        verdict: "no_data",
        yardstick: null,
      },
      {
        measure: "rating",
        this: { mean: 4.3, n: 3 },
        other: { mean: 3, n: 4 },
        difference: 1.33,
        verdict: "beyond",
        yardstick: 0.5,
      },
    ],
    this: {
      version_id: 22,
      version_no: 2,
      shots: 3,
      sour: 0,
      balanced: 2,
      bitter: 1,
      keep: 2,
      improve: 1,
      unlabelled: 0,
    },
    other: {
      version_id: 21,
      version_no: 1,
      shots: 4,
      sour: 3,
      balanced: 1,
      bitter: 0,
      keep: 0,
      improve: 3,
      unlabelled: 1,
    },
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

// ── the starting-point wizard ───────────────────────────────────────

export function similarSet(overrides: Partial<SimilarSet> = {}): SimilarSet {
  return {
    set_id: 3,
    set_name: "Kenya AA on the Niche",
    set_version_id: 21,
    version_no: 1,
    created_at: "2026-03-01T00:00:00.000Z",
    score: 8.2,
    attribute_score: 6,
    outcome_score: 2.2,
    roast_match: "same",
    process_match: true,
    origin_match: true,
    decaf_match: true,
    bean_id: 2,
    bean_name: "Kenya AA",
    roast_level: "light",
    process: "washed",
    origin: "Kenya",
    decaf: false,
    grinder_id: 1,
    grinder_name: "Niche Zero",
    grind_setting: "21",
    grind_value: 21,
    dose_g: 18,
    target_yield_g: 45,
    profile_temperature_c: 94,
    ratio: 2.5,
    profile_version_id: 7,
    profile_label: "9 Bar Espresso",
    outcome: {
      shots: 5,
      mean_rating: 4.4,
      mean_execution_score: 8.9,
      mean_ratio: 2.5,
      mean_duration_s: 28,
    },
    ...overrides,
  };
}

/** One option, `recommended` unless told otherwise. */
export function startingPointOption(
  overrides: Partial<StartingPointOption> = {},
): StartingPointOption {
  return {
    option: "recommended",
    headline: "Start here: 1:2.5 at 94 °C, two finer",
    grind_setting: "20",
    grind_is_absolute: true,
    grind_note: "Two numbers finer than your usual 22.",
    dose_g: 18,
    yield_g: 45,
    ratio: 2.5,
    temperature_c: 94,
    profile_version_id: null,
    profile: null,
    profile_note: "",
    rationale: "Light roasts run 93-96 °C and 1:2 to 1:3.",
    rules_used: ["light"],
    excerpts_used: [],
    similar_set_version_ids: [21],
    ...overrides,
  };
}

/**
 * A finished run. `status` and `output` are the two fields every test touches:
 * the wizard polls the first and renders the second.
 */
export function startingPointRun(overrides: Partial<StartingPointRun> = {}): StartingPointRun {
  return {
    id: 11,
    bean_id: 1,
    bean_name: "Ethiopia Guji",
    grinder_id: 1,
    grinder_name: "Niche Zero",
    usual_grind: "22",
    dose_hint_g: null,
    provider: "fake",
    model: "fixture-model",
    prompt_name: "starting_point",
    prompt_version: "1",
    input: {},
    output: {
      summary: "A light washed Kenyan.",
      questions_for_user: ["What do you normally grind espresso at?"],
      options: [
        startingPointOption({ option: "conservative", headline: "Safe: 1:2 at 93 °C" }),
        startingPointOption(),
        startingPointOption({ option: "adventurous", headline: "Push it: 1:3 at 95 °C" }),
      ],
    },
    usage: null,
    status: "ok",
    error: null,
    llm_call_id: null,
    accepted_option: null,
    accepted_set_id: null,
    accepted_set_version_id: null,
    accepted_draft_id: null,
    accepted_at: null,
    created_at: "2026-04-02T00:00:00.000Z",
    finished_at: "2026-04-02T00:00:30.000Z",
    ...overrides,
  };
}
