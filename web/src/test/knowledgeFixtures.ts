import type {
  InsightDeletion,
  KnowledgeChunk,
  KnowledgeDoc,
  KnowledgeInsight,
  KnowledgeRule,
  PatternProposal,
  PatternRun,
  PatternsData,
} from "@/api/types";

/**
 * What the knowledge components are handed.
 *
 * Hand-written like the Set fixtures and for the same reason: nothing here is a
 * derived number, so a recording would prove nothing a literal does not. The
 * generated types are what keep them honest against the pydantic models.
 */

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

export function knowledgeDoc(overrides: Partial<KnowledgeDoc> = {}): KnowledgeDoc {
  return {
    id: 1,
    slug: "ESPRESSO_TASTING_GUIDE",
    title: "Espresso Tasting Guide",
    source: "gaggimate-mcp",
    licence: "MIT",
    attribution: "gaggimate-mcp (julianleopold, MIT), adapting gaggimate-barista (Charlie Hall)",
    body: "# Espresso Tasting Guide\n\n## Sour vs Bitter\n\nSour hits fast and fades.\n",
    content_hash: "abc",
    edited: false,
    created_at: "2026-03-01T00:00:00.000Z",
    updated_at: "2026-03-01T00:00:00.000Z",
    chunk_count: 2,
    tokens_estimate: 640,
    ...overrides,
  };
}

export function knowledgeChunk(overrides: Partial<KnowledgeChunk> = {}): KnowledgeChunk {
  return {
    id: 1,
    doc_id: 1,
    doc_slug: "ESPRESSO_TASTING_GUIDE",
    doc_title: "Espresso Tasting Guide",
    heading_path: "ESPRESSO_TASTING_GUIDE#sour-vs-bitter",
    heading: "Sour vs Bitter",
    ordinal: 0,
    body: "Sour hits fast and fades; bitter creeps up and dries the mouth.",
    tokens_estimate: 320,
    ...overrides,
  };
}

export function knowledgeInsight(overrides: Partial<KnowledgeInsight> = {}): KnowledgeInsight {
  return {
    id: 1,
    scope: { grinder_id: 2, process: "natural" },
    text: "Naturals on this grinder want two numbers finer than a washed bean.",
    evidence_shot_ids: [4, 6],
    source: "chat",
    analysis_id: null,
    confirmed: false,
    created_at: "2026-03-03T09:00:00.000Z",
    updated_at: "2026-03-03T09:00:00.000Z",
    confirmed_at: null,
    set_id: null,
    set_version_id: null,
    set_version_label: null,
    dismissed: false,
    rests_on: [],
    replaces_id: null,
    replaces_text: "",
    replaced: null,
    general: true,
    ...overrides,
  };
}

/** An agent's proposal to delete an added insight of Set 3, waiting for the person. */
export function insightDeletion(overrides: Partial<InsightDeletion> = {}): InsightDeletion {
  return {
    id: 9,
    set_id: 3,
    thread_id: 14,
    insight_id: 7,
    insight_text: "Two clicks finer on the Niche.",
    reason: "The last two shots contradict it on both measures.",
    status: "proposed",
    created_at: "2026-10-03T09:00:00.000Z",
    decided_at: null,
    ...overrides,
  };
}

/** A finished run of Find patterns across Sets that read four insights in three Sets. */
export function patternRun(overrides: Partial<PatternRun> = {}): PatternRun {
  return {
    id: 5,
    status: "done",
    error: null,
    provider: "openrouter",
    model: "careful-model",
    prompt_name: "patterns",
    prompt_version: "1+1",
    insights_read: 4,
    sets_read: 3,
    proposals_kept: 1,
    proposals_dropped: 0,
    dropped: {},
    usage: null,
    llm_call_id: null,
    created_at: "2026-10-03T09:00:00.000Z",
    finished_at: "2026-10-03T09:01:00.000Z",
    ...overrides,
  };
}

/** A waiting proposal made from two Sets' insights about one grinder. */
export function patternProposal(overrides: Partial<PatternProposal> = {}): PatternProposal {
  return {
    id: 11,
    run_id: 5,
    text: "The Niche channels below 9 clicks with light roasts.",
    scope: { roast_level: "light", grinder_id: 1 },
    sources: [
      { insight_id: 21, set_id: 3, set_name: "Guji daily", text: "Below 9 clicks it channels." },
      { insight_id: 22, set_id: 4, set_name: "Yirg daily", text: "It gushes under 9." },
    ],
    replaces_id: null,
    replaces_text: "",
    status: "proposed",
    insight_id: null,
    skipped: [],
    created_at: "2026-10-03T09:01:00.000Z",
    decided_at: null,
    ...overrides,
  };
}

/** What the section reads: one finished run with one waiting proposal. */
export function patternsData(overrides: Partial<PatternsData> = {}): PatternsData {
  return {
    run: patternRun(),
    proposals: [patternProposal()],
    new_since_last_run: 2,
    counted_from: "2026-10-03T09:00:00.000Z",
    sets_with_insights: 3,
    min_sets: 2,
    ...overrides,
  };
}
