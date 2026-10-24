import type { ShotListRow } from "@/api/types";
import { ChoiceCell } from "@/components/shots/ChoiceCell";
import { useVocabulary } from "@/hooks/useCatalog";
import { derivedDecisionTitle } from "@/lib/decisionTitle";

/**
 * Good, Bad or Discard, set where the shot is listed.
 *
 * The question every shot ends on — is this what the Recipe should make, a shot that should be
 * better, or one to throw away — so it is a click in the row, like the stars. A **filled** button
 * is the person's choice. An **outlined** Good or Bad is what the rating says when nothing was
 * chosen (`derived_decision`, which the server works out: 3 stars or more is Good, 1 or 2 is
 * Bad); a click on it stores it as the person's own, and a click on a filled one clears it, as
 * ever. Discard is never outlined: a rating cannot throw a shot away.
 *
 * The words come from the vocabulary; only the colour each one takes is decided here, because
 * that is presentation: keep (Good) is the good status, improve (Bad) the warning one, discard the
 * bad one.
 */

const PRESSED: Record<string, string> = {
  keep: "bg-status-good/20 text-status-good-text",
  improve: "bg-status-warn/20 text-status-warn-text",
  discard: "bg-status-bad/20 text-status-bad-text",
};

/** The same colours as an outline: the rating's word, not yet the person's. The title says which
 * rating it was (`derivedDecisionTitle`). */
const OUTLINED: Record<string, string> = {
  keep: "text-status-good-text ring-1 ring-inset ring-status-good/70",
  improve: "text-status-warn-text ring-1 ring-inset ring-status-warn/70",
  discard: "text-status-bad-text ring-1 ring-inset ring-status-bad/70",
};

export function DecisionCell({ shot, className }: { shot: ShotListRow; className?: string }) {
  const vocab = useVocabulary();
  return (
    <ChoiceCell
      shot={shot}
      field="decision"
      terms={vocab.data?.decisions ?? []}
      stored={shot.judgement_decision ?? null}
      hint={shot.derived_decision ?? null}
      hintTitle={derivedDecisionTitle(shot)}
      heading="Decision"
      testId="decision-cell"
      filled={PRESSED}
      hinted={OUTLINED}
      className={className}
    />
  );
}
