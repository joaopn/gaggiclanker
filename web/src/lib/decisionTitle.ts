import type { ShotListRow } from "@/api/types";

/**
 * What the outlined Good or Bad says on hover: which rating it came from, and the rule.
 *
 * The server picks the rating (`decision_rating`, and where it sits, `decision_rating_source`)
 * beside the decision it gives (`derived_decision`); this only puts them into words and never
 * works out which rating applies.
 */
export function derivedDecisionTitle(
  shot: Pick<ShotListRow, "derived_decision" | "decision_rating" | "decision_rating_source">,
): string {
  const rule = shot.derived_decision === "improve" ? "below 3★ is Bad" : "3★ or more is Good";
  const stars = shot.decision_rating;
  if (stars == null) return `From the rating (${rule}). Click to confirm or change.`;
  const from =
    shot.decision_rating_source === "judgement"
      ? `your ${stars}★ rating`
      : shot.decision_rating_source === "machine_notes"
        ? `the ${stars}★ rating on the machine's notes card`
        : shot.decision_rating_source === "machine_index"
          ? `the ${stars}★ rating on the machine's index`
          : `the ${stars}★ rating`;
  return `From ${from} (${rule}). Click to confirm or change.`;
}
