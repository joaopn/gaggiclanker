import { useEffect, useState } from "react";
import type { ShotListRow } from "@/api/types";
import { usePatchJudgement } from "@/hooks/useRecipes";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * Discard, set where the shot is listed.
 *
 * It means one thing: "this shot went wrong, do not count it". It is independent of the stars (a
 * discarded shot keeps them) and nothing is worked out for it. Until it is clicked the cell is a
 * quiet "Discard" button; discarded, it is a filled red "Discarded", and a click on that clears it, so a
 * mis-click is undone where it happened.
 *
 * The write merges into the verdict (`usePatchJudgement`), so it never takes the rating or the
 * notes with it, and it queues behind any other write to the same shot. Clearing it on a shot
 * nobody has judged would write an empty verdict, so it writes nothing, the same guard as the
 * stars and the other cells.
 */
export function DiscardCell({ shot, className }: { shot: ShotListRow; className?: string }) {
  const patch = usePatchJudgement(shot.id);
  const stored = shot.discarded ?? false;
  // The click shows at once. The row's own value takes over again when the list is re-read and
  // brings *this* click's value, or at once if its write failed (the same hold as ChoiceCell:
  // not on any new value, or a second quick click would flick back to the first).
  const [pending, setPending] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    setPending((current) => (current === stored ? undefined : current));
  }, [stored]);
  const discarded = pending !== undefined ? pending : stored;

  function toggle() {
    const next = !discarded;
    // A write in flight means a verdict exists even if the row has not heard yet.
    if (!next && !shot.has_judgement && pending === undefined) return;
    setPending(next);
    void attempt(() => patch.mutateAsync({ shotId: shot.id, patch: { discarded: next } })).then(
      (result) => {
        // Only if nothing newer has been clicked since: that one is still on its way.
        if (result === undefined) setPending((current) => (current === next ? undefined : current));
      },
    );
  }

  return (
    <button
      type="button"
      aria-pressed={discarded}
      aria-label={
        discarded ? `Discarded: clear it for shot ${shot.device_id}` : "Discard this shot"
      }
      title={discarded ? "Discarded: not counted. Click to clear." : "Discard this shot"}
      data-testid="discard-cell"
      onClick={(event) => {
        // The row is a toggle underneath; this click is the discard's.
        event.stopPropagation();
        toggle();
      }}
      className={cn(
        "rounded-md px-1.5 py-0.5 text-xs transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        discarded
          ? "bg-status-bad/20 font-medium text-status-bad-text"
          : "text-muted-foreground ring-1 ring-inset ring-border hover:bg-muted/60 hover:text-foreground",
        className,
      )}
    >
      {discarded ? "Discarded" : "Discard"}
    </button>
  );
}
