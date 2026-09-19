import { useEffect, useState } from "react";
import type { JudgementWrite, ShotListRow } from "@/api/types";
import { useVocabulary } from "@/hooks/useCatalog";
import { usePatchJudgement } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * Keep, Improve or Discard, set where the shot is listed.
 *
 * The question every shot ends on — keep this recipe, improve on it, or throw
 * the shot away — so it is a click in the row, like the stars. Three toggle
 * buttons joined into one control; a click on the pressed one clears it,
 * because "not decided yet" is most shots and a mis-click must be undoable
 * where it happened.
 *
 * The write merges into the verdict (`usePatchJudgement`), so a decision never
 * takes the rating or the notes with it, and it queues behind any other write
 * to the same shot. Clearing a decision on a shot nobody has judged would
 * write an empty verdict, so it writes nothing — the same guard as the stars.
 *
 * The words come from the vocabulary; only the colour each one takes when
 * pressed is decided here, because that is presentation: keep is the good
 * status, improve the warning one, discard the bad one.
 */

const PRESSED: Record<string, string> = {
  keep: "bg-status-good/20 text-status-good-text",
  improve: "bg-status-warn/20 text-status-warn-text",
  discard: "bg-status-bad/20 text-status-bad-text",
};

export function DecisionCell({ shot, className }: { shot: ShotListRow; className?: string }) {
  const vocab = useVocabulary();
  const patch = usePatchJudgement(shot.id);
  // The click shows at once. The row's own value takes over again when the
  // list is re-read and brings *this* click's value, or at once if its write
  // failed. Not on any new value: Keep then Improve, quickly, re-reads the list
  // after Keep while Improve is still queued, and handing over then would
  // flick the cell back to Keep until Improve landed.
  const [pending, setPending] = useState<string | null | undefined>(undefined);
  const stored = shot.judgement_decision ?? null;
  useEffect(() => {
    setPending((current) => (current === stored ? undefined : current));
  }, [stored]);
  const current = pending !== undefined ? pending : stored;
  const decisions = vocab.data?.decisions ?? [];

  function decide(value: string) {
    const next = value === current ? null : value;
    // A write in flight means a verdict exists even if the row has not heard yet.
    if (next === null && !shot.has_judgement && pending === undefined) return;
    setPending(next);
    void attempt(() =>
      patch.mutateAsync({
        shotId: shot.id,
        patch: { decision: next as JudgementWrite["decision"] },
      }),
    ).then((result) => {
      // Only if nothing newer has been clicked since: that one is still on its way.
      if (result === undefined) setPending((current) => (current === next ? undefined : current));
    });
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: three toggles in a table cell; a fieldset would bring a legend and a border.
    <div
      role="group"
      aria-label={`Decision for shot ${shot.device_id}`}
      data-testid="decision-cell"
      className={cn(
        "inline-flex max-w-full overflow-hidden rounded-md border border-border",
        className,
      )}
    >
      {decisions.map((term, index) => {
        const on = current === term.value;
        return (
          <button
            key={term.value}
            type="button"
            aria-pressed={on}
            aria-label={`${term.label} shot ${shot.device_id}`}
            onClick={(event) => {
              // The row is a toggle underneath; this click is the decision's.
              event.stopPropagation();
              decide(term.value);
            }}
            className={cn(
              "px-1 py-0.5 text-xs transition-colors",
              "focus-visible:relative focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              index > 0 && "border-border border-l",
              on
                ? cn("font-medium", PRESSED[term.value] ?? "bg-muted")
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
