import type { KnowledgeChunk, KnowledgeDoc, KnowledgeInsight, KnowledgeRule } from "@/api/types";

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
