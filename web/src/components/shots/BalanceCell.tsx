import type { ShotListRow } from "@/api/types";
import { ChoiceCell } from "@/components/shots/ChoiceCell";
import { useVocabulary } from "@/hooks/useCatalog";

/**
 * Sour, Balanced or Bitter, set where the shot is listed.
 *
 * Where the cup sits on the one axis that maps onto extraction. A **filled** button is the
 * person's verdict (`judgement_balance`). An **outlined** one is the balance on the machine's notes
 * card (`balance`), shown only when the person gave none, drawn outlined
 * (outlined is not your choice); a click on it keeps it as their own, a click on a filled one
 * clears it. It merges into the verdict like the stars, so
 * it never carries the rating with it.
 *
 * The three words are the GaggiMate's own, from the vocabulary. Balance is not good or bad, so
 * every word takes the same neutral colour.
 */

const FILLED: Record<string, string> = {
  sour: "bg-muted text-foreground",
  balanced: "bg-muted text-foreground",
  bitter: "bg-muted text-foreground",
};

/** Outlined means "not your choice yet". */
const OUTLINED: Record<string, string> = {
  sour: "text-foreground ring-1 ring-inset ring-foreground/50",
  balanced: "text-foreground ring-1 ring-inset ring-foreground/50",
  bitter: "text-foreground ring-1 ring-inset ring-foreground/50",
};

export const MACHINE_BALANCE_TITLE = "From the machine's notes card. Click to confirm or change.";

export function BalanceCell({ shot, className }: { shot: ShotListRow; className?: string }) {
  const vocab = useVocabulary();
  return (
    <ChoiceCell
      shot={shot}
      field="balance"
      terms={vocab.data?.balances ?? []}
      stored={shot.judgement_balance ?? null}
      hint={shot.balance ?? null}
      hintTitle={MACHINE_BALANCE_TITLE}
      heading="Balance"
      testId="balance-cell"
      filled={FILLED}
      hinted={OUTLINED}
      className={className}
    />
  );
}
