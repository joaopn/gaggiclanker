import { useEffect, useId, useRef, useState } from "react";
import type { JudgementWrite, SetVersionRow } from "@/api/types";
import { Button } from "@/components/ui/button";

/**
 * What this shot's version predicted — hidden until you have judged the shot.
 *
 * The hiding is the point. A prediction read before tasting is a suggestion:
 * "less bitter" in front of you while you decide whether it is bitter is how a
 * record of what you expected quietly becomes a record of what you were told to
 * expect, and the track record on the Set page stops meaning anything. So the
 * text is not merely styled away — it is not rendered at all — until the shot
 * carries a decision, and "Show prediction" is there for the times somebody
 * genuinely wants it (checking a note, showing somebody else) at the cost of
 * one deliberate click.
 *
 * **The reveal belongs to one shot and must not outlive it.** React reuses a
 * component across a prop change, and the routes this sits on are reused too:
 * `/shots/:shotId` keeps the same element when the id changes, and with a warm
 * query cache the next shot's data is there before anything unmounts. Without
 * the reset below, revealing a prediction on one shot and then pressing Back
 * would show the next shot's prediction unasked — exactly the thing this
 * component exists to prevent.
 *
 * The reset holds the previous identity in **state**, not in a ref, and that is
 * the whole of the fix. A ref written during render is written by renders React
 * throws away: StrictMode renders twice in development, and a navigation inside
 * `startTransition` — which react-router uses — can discard a pass as well. The
 * first pass would record the new identity, the second would find it already
 * equal, and `revealed` would stay true. Setting state during render is the
 * documented way to adjust state to a prop change: React re-runs this component
 * immediately with the new state and nothing else re-renders, and a discarded
 * pass discards the state write with it.
 *
 * The shot page also keys this component by shot id. That is a second guard,
 * not an independent one: it closes the same hole on that one caller, and the
 * reset here is what makes every caller safe.
 *
 * Renders nothing when the shot has no Set, or when its version predicted
 * nothing — most versions, and an empty row saying so would be noise on every
 * shot in the archive.
 */
export function VersionPrediction({
  shotId,
  version,
  decision,
}: {
  /** Which shot this is about. Identity for the reveal, and nothing else. */
  shotId: number;
  version: SetVersionRow | null | undefined;
  decision: JudgementWrite["decision"];
}) {
  const [revealed, setRevealed] = useState(false);
  const textId = useId();
  const textRef = useRef<HTMLParagraphElement>(null);

  // The identity of what is being hidden. A different shot, or the same shot
  // moved to a different version, is a different secret.
  const identity = `${shotId}:${version?.id ?? 0}`;
  const [shownFor, setShownFor] = useState(identity);
  if (shownFor !== identity) {
    // During render rather than in an effect: an effect would let one frame
    // paint the new shot's prediction before hiding it again. In state rather
    // than in a ref: see the note above about renders React discards.
    setShownFor(identity);
    setRevealed(false);
  }

  const shown = decision != null || revealed;

  // The button that asked for it is gone — it is replaced by what it revealed —
  // so focus has to land on the text or it falls back to the body. In an effect,
  // after the paragraph has been rendered un-hidden: jsdom would allow focusing
  // a hidden element, and a real browser would not.
  useEffect(() => {
    if (revealed) textRef.current?.focus();
  }, [revealed]);

  if (!version?.prediction) return null;

  return (
    <div
      className="rounded-md border border-border bg-muted/30 px-2 py-1.5"
      data-testid="version-prediction-row"
      data-shown={shown ? "yes" : "no"}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-muted-foreground text-xs">
          Version prediction for v{version.version_no}
          {version.compares_to_version_no ? `, compared to v${version.compares_to_version_no}` : ""}
        </span>
        {shown ? null : (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-expanded={shown}
            aria-controls={textId}
            onClick={() => setRevealed(true)}
          >
            Show prediction
          </Button>
        )}
      </div>
      {/* Rendered always so `aria-controls` resolves, and empty until it is
          shown so the text is genuinely not there to be read. */}
      <p id={textId} ref={textRef} tabIndex={-1} hidden={!shown} className="text-sm">
        {shown ? version.prediction : ""}
      </p>
      {shown ? null : (
        <p className="text-muted-foreground text-xs">
          Hidden until you have decided about this shot, so it does not tell you what to taste.
        </p>
      )}
    </div>
  );
}
