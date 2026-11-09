import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { OutcomeProposal, RecipeVersionRow } from "@/api/types";
import { OutcomeBadge } from "@/components/recipes/OutcomeBadge";
import { OutcomePanel, outcomeButtonLabel, outcomeGate } from "@/components/recipes/OutcomePanel";
import { Button } from "@/components/ui/button";

/**
 * How a version's prediction turned out, and the control that records it.
 *
 * The badge is always there — "No prediction" and "Open" are states worth
 * seeing, not gaps. The control beside it offers exactly what the server will
 * accept, which is not one condition but two: **clearing** a grade is allowed
 * at any time, while **recording or changing** one needs a prediction and a
 * rated shot (a discarded one does not count). A version whose shots were later
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

export function VersionOutcomeControl({
  recipeId,
  version,
  countedNow,
  proposed = null,
}: {
  recipeId: number;
  version: RecipeVersionRow;
  /**
   * How many shots are counted now (rated, not discarded): the number a recorded outcome's own
   * count is compared with, and whether there is anything to grade at all.
   */
  countedNow: number;
  /**
   * The grade an agent proposed for this version that nobody has answered. Shown
   * beside the recorded outcome and never as one: it is words until a person
   * accepts it, in the conversation it came from.
   */
  proposed?: OutcomeProposal | null;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const reasonId = useId();

  const state = version.outcome_state;
  const recorded = version.outcome != null;
  // Exactly the server's rule for `PUT .../outcome`.
  const { canRecord, reason } = outcomeGate(Boolean(version.prediction), countedNow);
  // How many shots were counted after the outcome was recorded; only known when the outcome
  // says how many it was recorded on (an older one, or one typed before the count was kept,
  // does not).
  const recordedOn = recorded ? (version.outcome_counted_shots ?? null) : null;
  const moreSince = recordedOn !== null ? Math.max(0, countedNow - recordedOn) : 0;

  return (
    <div className="space-y-1.5" data-testid="version-outcome" data-state={state}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-xs">Outcome</span>
        <OutcomeBadge state={state} />
        {recordedOn !== null ? (
          <span className="text-muted-foreground text-xs" data-testid="outcome-count">
            on {recordedOn} counted {recordedOn === 1 ? "shot" : "shots"}
            {moreSince > 0 ? ` · ${moreSince} more counted since` : ""}
          </span>
        ) : null}
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
              shots are gone would promise a control the server refuses; "Grade again" says
              that shots were counted after the recorded outcome. */}
          {outcomeButtonLabel({ recorded, canRecord, moreSince })}
        </Button>
      </div>

      {proposed ? (
        <p className="text-sm" data-testid="version-outcome-proposed">
          <span className="text-muted-foreground text-xs">The agent proposed </span>
          <OutcomeBadge state={proposed.outcome} />
          <span className="text-muted-foreground text-xs">
            {" "}
            on {proposed.counted_shots} counted {proposed.counted_shots === 1 ? "shot" : "shots"}.
            {recorded && proposed.outcome !== version.outcome
              ? " It differs from what is recorded."
              : ""}{" "}
            Nothing is recorded until you accept it
            {proposed.thread_id ? (
              <>
                {" "}
                —{" "}
                <Link
                  to={`/chat?thread=${proposed.thread_id}`}
                  className="underline underline-offset-2"
                  data-testid="version-outcome-proposed-link"
                >
                  answer it in the conversation
                </Link>
              </>
            ) : null}
            .
          </span>
        </p>
      ) : null}

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
          <OutcomePanel
            recipeId={recipeId}
            versionId={version.id}
            outcome={version.outcome ?? null}
            outcomeNote={version.outcome_note ?? ""}
            canRecord={canRecord}
            reason={reason}
            onClose={() => setOpen(false)}
          />
        ) : null}
      </div>
    </div>
  );
}
