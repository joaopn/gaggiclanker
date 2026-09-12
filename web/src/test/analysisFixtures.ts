import type { Analysis, KnowledgeRule, Suggestion } from "@/api/types";

/**
 * What the analysis components are handed.
 *
 * Hand-written like the Set fixtures and for the same reason: nothing here is a
 * derived number, so a recording would prove nothing a literal does not. The
 * generated types are what keep them honest against the pydantic models.
 */

export function suggestion(overrides: Partial<Suggestion> = {}): Suggestion {
  return {
    id: 1,
    analysis_id: 1,
    variable: "grind",
    direction: "finer",
    magnitude: 2,
    unit: "grinder_steps",
    reason: "28 s target, 24 s actual, and flow overshot the commanded curve.",
    confidence: "high",
    priority: 1,
    status: "open",
    resulting_set_version_id: null,
    created_at: "2026-03-03T09:00:00.000Z",
    shot_id: 6,
    set_version_id: 1,
    ...overrides,
  };
}

export function analysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    id: 1,
    shot_id: 6,
    set_version_id: 1,
    provider: "anthropic",
    model: "claude-haiku",
    prompt_name: "analysis",
    prompt_version: "2026-03-01T00:00:00.000Z+2026-03-01T00:00:00.000Z",
    input: null,
    output: {
      shot_style: "bloom",
      execution: {
        summary: "Clean extraction with a short bloom and no channeling.",
        issues: [
          {
            signal: "flow_adherence",
            severity: "minor",
            evidence: "flow RMSE 0.44 ml/s against the commanded curve",
          },
        ],
      },
      taste_prediction: { balance: "sour", body: "thin", confidence: "medium" },
      diagnosis: "The shot ran four seconds fast and the puck never loaded.",
      profile_patch: [
        {
          phase_index: 1,
          field: "duration",
          from: "7",
          to: "10",
          reason: "A longer bloom for a bean four days off roast.",
        },
      ],
      questions_for_user: ["What did the last shot taste like at the same grind?"],
      rules_used: ["hierarchy", "natural.light"],
    },
    usage: { prompt_tokens: 4200, completion_tokens: 610, total_tokens: 4810 },
    cost_estimate: null,
    status: "ok",
    error: null,
    llm_call_id: "abc123",
    created_at: "2026-03-03T09:00:00.000Z",
    finished_at: "2026-03-03T09:00:41.000Z",
    suggestions: [suggestion()],
    ...overrides,
  };
}

export function rule(overrides: Partial<KnowledgeRule> = {}): KnowledgeRule {
  return {
    id: 1,
    category: "dial_in_order",
    key: "hierarchy",
    applies: {},
    value: { text: "Grind first, then yield, then temperature." },
    unit: "none",
    confidence: "expert",
    source: "gaggimate-barista",
    source_ref: "gaggimate-mcp.md §7",
    enabled: true,
    updated_at: "2026-03-01T00:00:00.000Z",
    edited: false,
    ...overrides,
  };
}
