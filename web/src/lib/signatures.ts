import type { SignatureExpectation, SignatureTier } from "@/api/types";

/** The tiers, most important first: the order the card lists them in. */
export const TIERS: readonly SignatureTier[] = ["critical", "important", "context"];

export const TIER_LABEL: Record<SignatureTier, string> = {
  critical: "Critical",
  important: "Important",
  context: "Context",
};

export const TIER_HINT: Record<SignatureTier, string> = {
  critical: "A miss is what the Review column shows in red.",
  important: "A miss shows in amber.",
  context: "Held or not, it is listed and never shown as a miss.",
};

/** What each kind of expectation is checked by, in a person's words. */
export const KIND_LABEL: Record<SignatureExpectation["kind"], string> = {
  measure: "computed",
  reached: "phase reached",
  expects_warning: "warning expected",
  free_text: "checked by the reading",
};

export const STATUS_LABEL: Record<SignatureExpectation["status"], string> = {
  proposed: "Proposed",
  confirmed: "Confirmed",
  rejected: "Rejected",
};

/** The expectations still waiting for an answer that can be given (not one that needs a phase). */
export function confirmable(expectations: SignatureExpectation[]): SignatureExpectation[] {
  return expectations.filter((e) => e.status === "proposed" && !e.needs_a_new_phase);
}

/** "3 confirmed · 2 waiting", or what there is to say when there is nothing yet. */
export function signatureSummary(counts: {
  confirmed: number;
  proposed: number;
  rejected: number;
}): string {
  const parts: string[] = [];
  if (counts.confirmed > 0) parts.push(`${counts.confirmed} confirmed`);
  if (counts.proposed > 0) parts.push(`${counts.proposed} waiting`);
  if (counts.rejected > 0) parts.push(`${counts.rejected} rejected`);
  return parts.length > 0 ? parts.join(" · ") : "none yet";
}

/** The Profiles page, on the version whose Signature card answers a "read without a signature". */
export function signatureHref(profileVersionId: number): string {
  return `/profiles#version-${profileVersionId}`;
}
