import { useEffect, useId, useState } from "react";
import type { SetVersionRow, VersionOutcome } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { useSetVersionOutcome } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * How a version's prediction turned out, and the control that records it.
 *
 * The badge is always there — "No prediction" and "Open" are states worth
 * seeing, not gaps. The control beside it offers exactly what the server will
 * accept, which is not one condition but two: **clearing** a grade is allowed
 * at any time, while **recording or changing** one needs a prediction and a
 * shot somebody labelled Keep or Improve. A version whose shots were later
 * unfiled therefore still opens, and offers Clear alone — a panel that refused
 * to open would strand a grade nobody can take back.
 *
 * When there is nothing to do at all the button is disabled, and the reason is
 * a sentence on the page tied to it with `aria-describedby`, not a `title`: a
 * disabled button is not focusable in most browsers, so a tooltip on it is a
 * reason a keyboard or a screen reader never reaches.
 *
 * No overlay: the panel is a strip under the badge, rendered always and toggled
 * with `hidden` so `aria-controls` resolves to something a reader can reach.
 */

/** The badge's colour per state. Open is a question, not a warning. */
const TONE: Record<string, string> = {
  held: "border-status-good/40 bg-status-good/10 text-status-good-text",
  partly_held: "border-status-warn/40 bg-status-warn/10 text-status-warn-text",
  failed: "border-status-bad/40 bg-status-bad/10 text-status-bad-text",
  inconclusive: "border-border bg-muted text-muted-foreground",
  open: "border-border bg-background text-foreground",
  no_prediction: "border-transparent bg-transparent text-muted-foreground",
};

export function VersionOutcomeControl({
  setId,
  version,
  gradable,
}: {
  setId: number;
  version: SetVersionRow;
  /** Has a shot somebody labelled Keep or Improve. */
  gradable: boolean;
}) {
  const vocab = useVocabulary();
  const save = useSetVersionOutcome();
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<string>(version.outcome ?? "");
  const [note, setNote] = useState(version.outcome_note ?? "");
  const panelId = useId();
  const noteId = useId();
  const reasonId = useId();

  // The panel is seeded from the row, and re-seeded when the row changes under
  // it: somebody grading the same version in another tab, or the write's own
  // answer coming back.
  useEffect(() => {
    setChoice(version.outcome ?? "");
    setNote(version.outcome_note ?? "");
  }, [version.outcome, version.outcome_note]);

  const state = version.outcome_state;
  const label = vocab.data?.outcome_states.find((term) => term.value === state)?.label ?? state;
  const recorded = version.outcome != null;
  // Exactly the server's rule for `PUT .../outcome`.
  const canRecord = Boolean(version.prediction) && gradable;
  const reason = canRecord
    ? undefined
    : !version.prediction
      ? "This version states no prediction, so there is nothing to grade."
      : "Label a shot Keep or Improve first — there is nothing to grade yet.";

  async function write(body: { outcome: VersionOutcome; note: string } | null) {
    const result = await attempt(() => save.mutateAsync({ setId, versionId: version.id, body }));
    if (result !== undefined) setOpen(false);
  }

  return (
    <div className="space-y-1.5" data-testid="version-outcome" data-state={state}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-xs">Outcome</span>
        <Badge variant="outline" className={cn("font-normal", TONE[state])}>
          {label}
        </Badge>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-expanded={open}
          aria-controls={panelId}
          // Open whenever there is something the server would accept: a grade
          // to record or change, or one to clear.
          disabled={!canRecord && !recorded}
          aria-describedby={reason ? reasonId : undefined}
          onClick={() => setOpen((shown) => !shown)}
        >
          {/* Named for what the panel will offer. "Change" on a version whose
              shots are gone would promise a control the server refuses. */}
          {recorded
            ? canRecord
              ? "Change the outcome"
              : "Clear the outcome"
            : "Record the outcome"}
        </Button>
      </div>

      {reason ? (
        <p id={reasonId} className="text-muted-foreground text-xs" data-testid="outcome-reason">
          {reason}
        </p>
      ) : null}

      {version.outcome_note ? (
        <p className="text-sm" data-testid="version-outcome-note">
          {version.outcome_note}
        </p>
      ) : null}

      <div
        id={panelId}
        hidden={!open}
        className="space-y-2 rounded-md border border-border bg-muted/30 p-2"
      >
        {open ? (
          <>
            {canRecord ? (
              <>
                {/* A fieldset rather than a div with `role="group"`: the four
                    buttons are one question, and the legend is what names it
                    for a reader landing on the first of them. */}
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
              <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
