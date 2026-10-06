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
    summary: "Slow start, thin middle; the cup filled early.",
    prediction_given: "",
    claims: [],
    rules_used: ["hierarchy", "grind"],
    excerpts_used: ["ESPRESSO_TASTING_GUIDE#sour-vs-bitter"],
    usage: { prompt_tokens: 9000, completion_tokens: 400, total_tokens: 9400 },
    llm_call_id: "abc123",
    created_at: "2026-09-28T09:00:00.000Z",
    finished_at: "2026-09-28T09:00:41.000Z",
    ...overrides,
  };
}
