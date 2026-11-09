import { useEffect, useId, useState } from "react";
import type { VersionOutcome } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { useSetVersionOutcome } from "@/hooks/useRecipes";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * Whether the server will accept a recorded or changed outcome, and the sentence for when it
 * will not. Exactly the rule of `PUT .../outcome`: the version has a prediction and at least
 * one counted shot (rated, not discarded). **Clearing** is always allowed, so a version that
 * fails this still opens its panel to take a grade back.
 */
export function outcomeGate(
  hasPrediction: boolean,
  countedNow: number,
): { canRecord: boolean; reason: string | undefined } {
  const canRecord = hasPrediction && countedNow > 0;
  return {
    canRecord,
    reason: canRecord
      ? undefined
      : !hasPrediction
        ? "This version states no prediction, so there is nothing to grade."
        : "No rated shot yet — a discarded one does not count. There is nothing to grade yet.",
  };
}

/** The label of the button that opens the panel, named for what the panel will offer. */
export function outcomeButtonLabel(opts: {
  recorded: boolean;
  canRecord: boolean;
  moreSince: number;
}): string {
  if (!opts.recorded) return "Record the outcome";
  if (!opts.canRecord) return "Clear the outcome";
  return opts.moreSince > 0 ? "Grade again" : "Change the outcome";
}

/**
 * The four choices, the Why box and Save / Clear / Cancel for one version's outcome.
 *
 * One panel for the two places that write a version's outcome by hand: the Recipe page's row
 * and the answered card in the chat. Both write through the Recipe page's own route
 * (`useSetVersionOutcome`), never through a proposal route, so a grade recorded from a card is
 * the same write as one recorded on the page. No overlay: it is a strip the caller shows and
 * hides.
 */
export function OutcomePanel({
  recipeId,
  versionId,
  outcome,
  outcomeNote,
  canRecord,
  reason,
  onClose,
}: {
  recipeId: number;
  versionId: number;
  /** The recorded outcome, or null when it is open. */
  outcome: string | null;
  outcomeNote: string;
  canRecord: boolean;
  reason?: string;
  onClose: () => void;
}) {
  const vocab = useVocabulary();
  const save = useSetVersionOutcome();
  const [choice, setChoice] = useState<string>(outcome ?? "");
  const [note, setNote] = useState(outcomeNote);
  const noteId = useId();
  const recorded = outcome != null;

  // Seeded from the row, and seeded again when the row changes under the panel: somebody
  // grading the same version in another tab, or the write's own answer coming back.
  useEffect(() => {
    setChoice(outcome ?? "");
    setNote(outcomeNote);
  }, [outcome, outcomeNote]);

  async function write(body: { outcome: VersionOutcome; note: string } | null) {
    const result = await attempt(() => save.mutateAsync({ recipeId, versionId, body }));
    if (result !== undefined) onClose();
  }

  return (
    <>
      {canRecord ? (
        <>
          {/* A fieldset rather than a div with `role="group"`: the four buttons are one
              question, and the legend is what names it for a reader landing on the first
              of them. */}
          <fieldset className="border-0 p-0" data-testid="outcome-choices">
            <legend className="mb-1 text-muted-foreground text-xs">
              How did the prediction turn out?
            </legend>
            <div className="flex flex-wrap items-center gap-1.5">
              {(vocab.data?.version_outcomes ?? []).map((term) => (
                <button
                  key={term.value}
                  type="button"
                  aria-pressed={choice === term.value}
                  onClick={() => setChoice(term.value)}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs transition-colors",
                    choice === term.value
                      ? "border-foreground/30 bg-muted"
                      : "border-border text-muted-foreground",
                  )}
                >
                  {term.label}
                </button>
              ))}
            </div>
          </fieldset>
          <div>
            <label htmlFor={noteId} className="mb-1 block text-muted-foreground text-xs">
              Why
            </label>
            <textarea
              id={noteId}
              rows={2}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="shorter, but still sharp at the end"
              className={cn(
                "w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            />
          </div>
        </>
      ) : (
        <p className="text-muted-foreground text-xs" data-testid="outcome-clear-only">
          {reason} This grade can still be taken back.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {canRecord ? (
          <Button
            type="button"
            size="sm"
            disabled={!choice || save.isPending}
            onClick={() => void write({ outcome: choice as VersionOutcome, note })}
          >
            Save the outcome
          </Button>
        ) : null}
        {recorded ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={save.isPending}
            onClick={() => void write(null)}
          >
            Clear
          </Button>
        ) : null}
        <Button type="button" size="sm" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </>
  );
}
