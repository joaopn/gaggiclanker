import type { ChecksBlock, ClaimEvidence, ReviewBlock, ReviewClaim } from "@/api/types";

/** A shot's `review` block, as the list, the detail and the fields serve it. */
export function reviewBlock(overrides: Partial<ReviewBlock> = {}): ReviewBlock {
  return { state: "unreviewed", entries: [], ...overrides };
}

/** A shot's `checks` block (the Curve check), as the list and the detail serve it. */
export function checksBlock(overrides: Partial<ChecksBlock> = {}): ChecksBlock {
  const entries = overrides.entries ?? [];
  return { entries, state: entries.length > 0 ? "entries" : "unchecked", ...overrides };
}

/** One evidence item of a claim, as the server evaluated it. */
export function evidence(overrides: Partial<ClaimEvidence> = {}): ClaimEvidence {
  return {
    sentence: "Pressure averaged 6.1 bar over the decline",
    value: 6.1,
    unit: "bar",
    kind: "measure",
    absent: null,
    held: null,
    limit_text: "",
    ...overrides,
  };
}

/** One claim of a review. Hand-written: nothing here is a derived number. */
export function claim(overrides: Partial<ReviewClaim> = {}): ReviewClaim {
  return {
    id: 10,
    review_id: 1,
    position: 0,
    kind: "claim",
    phase: "decline",
    window_text: "decline",
    start_s: 12,
    end_s: 24,
    fault: "unstable",
    text: "Pressure wanders through the decline.",
    evidence: [evidence()],
    supported: true,
    expectation_id: null,
    held: null,
    stance: null,
    status: "confirmed",
    answered_at: null,
    ...overrides,
  };
}
