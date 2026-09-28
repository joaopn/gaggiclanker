import type { ShotReview } from "@/api/types";

/**
 * A shot's review, as the shot detail carries it.
 *
 * Hand-written like the other fixtures and for the same reason: nothing here
 * is a derived number. The generated type keeps it honest against the pydantic
 * row.
 */
export function review(overrides: Partial<ShotReview> = {}): ShotReview {
  return {
    id: 1,
    shot_id: 129,
    status: "ok",
    error: null,
    provider: "anthropic",
    model: "claude-careful",
    prompt_name: "review",
    prompt_version: "2026-09-01T00:00:00.000Z+2026-09-01T00:00:00.000Z",
    taste_balance: "sour",
    taste_body: "thin",
    taste_confidence: "medium",
    description:
      "The shot ran 54.6 s but the puck never loaded: flow ran 0.41 ml/s off its target and the score lost 0.07 to it.",
    summary: "Slow start, thin middle; likely sour.",
    rules_used: ["hierarchy", "grind"],
    excerpts_used: ["ESPRESSO_TASTING_GUIDE#sour-vs-bitter"],
    usage: { prompt_tokens: 9000, completion_tokens: 400, total_tokens: 9400 },
    llm_call_id: "abc123",
    created_at: "2026-09-28T09:00:00.000Z",
    finished_at: "2026-09-28T09:00:41.000Z",
    ...overrides,
  };
}
