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
  free_text: "checked by the review",
};

/** "3 in force · 1 not in force · 1 rejected", or what there is to say when there is nothing. */
export function signatureSummary(counts: {
  confirmed: number;
  not_in_force: number;
  rejected: number;
}): string {
  const parts: string[] = [];
  if (counts.confirmed > 0) parts.push(`${counts.confirmed} in force`);
  if (counts.not_in_force > 0) parts.push(`${counts.not_in_force} not in force`);
  if (counts.rejected > 0) parts.push(`${counts.rejected} rejected`);
  return parts.length > 0 ? parts.join(" · ") : "none yet";
}

/** The Profiles page, on the version whose Signature card answers a "read without a signature". */
export function signatureHref(profileVersionId: number): string {
  return `/profiles#version-${profileVersionId}`;
}
