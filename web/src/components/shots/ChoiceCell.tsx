import { useEffect, useState } from "react";
import type { JudgementWrite, ShotListRow } from "@/api/types";
import { usePatchJudgement } from "@/hooks/useRecipes";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * One choice out of a few words, set where the shot is listed: the Decision and Balance columns.
 *
 * Joined toggle buttons. A button is **filled** when it is what the person chose for this shot,
 * and a click on the filled one clears it, because "not chosen yet" is most shots and a
 * mis-click must be undoable where it happened. A button can also show a **hint**, a value the
 * server worked out or read from the machine when nothing was chosen (the decision the rating
 * gives, the balance on the machine's notes card): it is drawn apart from a choice, and a click on
 * it stores that value as the person's own. The hint is served, never worked out here.
 *
 * The write merges into the verdict (`usePatchJudgement`), so a choice never takes the rating or
 * the notes with it, and it queues behind any other write to the same shot. Clearing a choice on
 * a shot nobody has judged would write an empty verdict, so it writes nothing — the same guard as
 * the stars.
 *
 * The words come from the vocabulary; the colours are presentation and the caller's.
 */
export type ChoiceTerm = { value: string; label: string };

export function ChoiceCell({
  shot,
  field,
  terms,
  stored,
  hint,
  hintTitle,
  heading,
  testId,
  filled,
  hinted,
  className,
}: {
  shot: ShotListRow;
  /** The verdict's field this cell writes. */
  field: "decision" | "balance";
  terms: readonly ChoiceTerm[];
  /** What the person chose, from the verdict. */
  stored: string | null;
  /** What shows when nothing was chosen, from the server. */
  hint: string | null;
  /** What the hint's button says on hover, and why it looks different. */
  hintTitle: string;
  /** The group's name, which the buttons' names build on: "Decision", "Balance". */
  heading: string;
  testId: string;
  /** The classes of a chosen button, by value. */
  filled: Record<string, string>;
  /** The classes of a hinted button, by value. */
  hinted: Record<string, string>;
  className?: string;
}) {
  const patch = usePatchJudgement(shot.id);
  // The click shows at once. The row's own value takes over again when the
  // list is re-read and brings *this* click's value, or at once if its write
  // failed. Not on any new value: two clicks in a row re-read the list after
  // the first while the second is still queued, and handing over then would
  // flick the cell back to the first until the second landed.
  const [pending, setPending] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    setPending((current) => (current === stored ? undefined : current));
  }, [stored]);
  const current = pending !== undefined ? pending : stored;
  // A hint stands in only for a choice nobody made, on the row as the server last read it.
  const hinting = current === null ? hint : null;

  function choose(value: string) {
    const next = value === current ? null : value;
    // A write in flight means a verdict exists even if the row has not heard yet.
    if (next === null && !shot.has_judgement && pending === undefined) return;
    setPending(next);
    void attempt(() =>
      patch.mutateAsync({
        shotId: shot.id,
        patch: { [field]: next } as Partial<JudgementWrite>,
      }),
    ).then((result) => {
      // Only if nothing newer has been clicked since: that one is still on its way.
      if (result === undefined) setPending((current) => (current === next ? undefined : current));
    });
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: toggles in a table cell; a fieldset would bring a legend and a border.
    <div
      role="group"
      aria-label={`${heading} for shot ${shot.device_id}`}
      data-testid={testId}
      className={cn(
        "inline-flex max-w-full overflow-hidden rounded-md border border-border",
        className,
      )}
    >
      {terms.map((term, index) => {
        const on = current === term.value;
        const outlined = hinting === term.value;
        return (
          <button
            key={term.value}
            type="button"
            aria-pressed={on}
            aria-label={`${term.label} shot ${shot.device_id}`}
            data-hint={outlined ? "true" : undefined}
            title={outlined ? hintTitle : undefined}
            onClick={(event) => {
              // The row is a toggle underneath; this click is the choice's.
              event.stopPropagation();
              choose(term.value);
            }}
            className={cn(
              "px-1 py-0.5 text-xs transition-colors",
              "focus-visible:relative focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              index > 0 && "border-border border-l",
              on
                ? cn("font-medium", filled[term.value] ?? "bg-muted")
                : outlined
                  ? hinted[term.value]
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            {term.label}
          </button>
        );
      })}
    </div>
  );
}
