import { Check, X } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { Link } from "react-router-dom";
import { ApiClientError } from "@/api/client";
import type { OutcomeProposal, VersionOutcome } from "@/api/types";
import { useChatThreadId } from "@/components/chat/tellAgent";
import { humanisedOutcome, OutcomeBadge } from "@/components/recipes/OutcomeBadge";
import { OutcomePanel, outcomeButtonLabel, outcomeGate } from "@/components/recipes/OutcomePanel";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import {
  type OutcomeAnswer,
  useAnswerOutcomeProposal,
  useOutcomeProposals,
} from "@/hooks/useRecipes";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";
import { formatTime } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * A grade the agent proposed for a version, and the three buttons that answer it.
 *
 * What the agent writes at the end of its answer is words; the version's
 * **outcome** is what the track record, every later conversation and the
 * "no new change while the last prediction is ungraded" rule read, and it is
 * recorded by a person only. So the grade is a card here, with the outcome as
 * the same badge the Recipe page uses, the per-claim lines under it and how many
 * counted shots it rests on. **Accept** records it as written, **Record another
 * outcome** records the person's own choice of the four (inline, no overlay),
 * **Dismiss** records nothing and may say why.
 *
 * The card is read live from the Recipe's proposed grades rather than rendered from
 * the tool's output: the output is the moment it was called, and this card is
 * read again whenever the conversation is scrolled to, after the person may have
 * answered it here, on the Recipe page, or by accepting the next version. A grade
 * that is no longer waiting says how it ended, so scrolling back is reading a
 * record and not a set of live buttons. An answer is **not** sent to the agent
 * as a message: the agent is told what the person did at the start of its next
 * turn, from the record.
 */

export type OutcomeCardProps = {
  recipeId: number;
  proposalId: number;
  /** What the tool said, shown until the live row has been read. */
  fallback: { version: string; outcome: string; countedShots: number };
};

const NOTE_MAX = 500;

/** A failed press, said in words the person can act on. */
export function outcomeFailure(error: Error): string {
  if (error instanceof ApiClientError) {
    if (error.code === "OUTCOME_PROPOSAL_DECIDED") {
      return "This grade was already answered, here in another tab or on the Recipe page. The card will show how once it refreshes.";
    }
    if (error.code === "NOTHING_TO_GRADE") {
      return "No shot of this version is rated any more (a discarded one does not count), so there is nothing to grade. Rate a shot, or dismiss this grade.";
    }
  }
  return error.message;
}

function shotsWords(count: number): string {
  return `${count} counted ${count === 1 ? "shot" : "shots"}`;
}

export function OutcomeCard({ recipeId, proposalId, fallback }: OutcomeCardProps) {
  const proposals = useOutcomeProposals(recipeId);
  const row = proposals.data?.items.find((item) => item.id === proposalId);
  if (!row) {
    return (
      <div className="m-2 mt-0" data-testid="propose-card-outcome">
        <p className="rounded-md border border-primary/40 bg-primary/5 p-2 text-sm">
          A grade for {fallback.version}: {fallback.outcome.replace("_", " ")}
          <span className="block text-muted-foreground text-xs">
            on {shotsWords(fallback.countedShots)} · waiting for you
          </span>
        </p>
      </div>
    );
  }
  return (
    <div className="m-2 mt-0" data-testid="propose-card-outcome">
      <LiveOutcomeCard recipeId={recipeId} row={row} />
    </div>
  );
}

function LiveOutcomeCard({ recipeId, row }: { recipeId: number; row: OutcomeProposal }) {
  const vocab = useVocabulary();
  const answer = useAnswerOutcomeProposal();
  const threadId = useChatThreadId();
  const singleFlight = useSingleFlight();
  const noteId = useId();
  const fieldId = useId();
  const [choosing, setChoosing] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [note, setNote] = useState("");

  // What the server said to the last press on this card, before the lists it
  // invalidated have been read again.
  const answered =
    answer.data?.proposal.id === row.id && !answer.isPending ? answer.data.proposal : null;
  const shown = answered ?? row;
  const waiting = shown.status === "proposed";
  const failure = answer.variables?.proposalId === row.id && answer.error ? answer.error : null;
  const more = shown.counted_shots_now - shown.counted_shots;
  const disagrees =
    waiting && shown.version_outcome != null && shown.version_outcome !== shown.outcome;
  const labelOf = (value: string | null | undefined) =>
    vocab.data?.version_outcomes.find((term) => term.value === value)?.label ??
    humanisedOutcome(value);

  function send(body: OutcomeAnswer) {
    singleFlight((release) => {
      void attempt(() =>
        answer.mutateAsync({ recipeId, proposalId: row.id, answer: body, threadId }),
      ).finally(release);
    });
  }

  return (
    <div
      className="rounded-lg border border-primary/40 bg-primary/5 p-3"
      data-testid="outcome-card"
      data-status={shown.status}
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium text-sm">
          {waiting
            ? `A grade for ${shown.version_label} is waiting for you`
            : `A grade that was proposed for ${shown.version_label}`}
        </span>
        <span className="text-muted-foreground text-xs">{formatTime(row.created_at)}</span>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <OutcomeBadge state={shown.outcome} testId="outcome-card-badge" />
        <span className="text-muted-foreground text-xs" data-testid="outcome-card-shots">
          on {shotsWords(shown.counted_shots)}
          {waiting && more > 0 ? ` · ${more} more since` : ""}
        </span>
      </div>

      <p className="mb-2 whitespace-pre-wrap text-sm" data-testid="outcome-card-note">
        {shown.note}
      </p>

      {disagrees ? (
        // Recorded since by hand, or by an earlier grade: the card says both, so
        // an Accept is never a surprise about what it replaces.
        <p className="mb-2 text-sm" data-testid="outcome-card-differs">
          <span className="text-muted-foreground text-xs">
            {shown.version_label}'s outcome is recorded as{" "}
          </span>
          <OutcomeBadge state={shown.version_outcome as string} />
          <span className="text-muted-foreground text-xs"> now: accepting replaces it.</span>
        </p>
      ) : null}

      {waiting ? (
        <>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={answer.isPending} onClick={() => send({ kind: "accept" })}>
              <Check className="size-3.5" aria-hidden="true" />
              Accept as {labelOf(shown.outcome)}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={answer.isPending}
              aria-expanded={choosing}
              aria-controls={fieldId}
              onClick={() => {
                setChoosing((open) => !open);
                setDismissing(false);
              }}
            >
              Record another outcome
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={answer.isPending}
              aria-expanded={dismissing}
              aria-controls={noteId}
              onClick={() => {
                setDismissing((open) => !open);
                setChoosing(false);
              }}
            >
              <X className="size-3.5" aria-hidden="true" />
              Dismiss
            </Button>
          </div>

          {/* Rendered always and toggled with `hidden`, so `aria-controls`
              names something a reader can reach. The four choices are plain
              buttons: a popover would be a floating layer inside a card inside
              a scrolling transcript. */}
          <fieldset
            id={fieldId}
            hidden={!choosing}
            className="mt-2 border-0 p-0"
            data-testid="outcome-card-choices"
          >
            <legend className="mb-1 text-muted-foreground text-xs">
              Record this outcome instead. Your lines are the agent's, unchanged.
            </legend>
            <div className="flex flex-wrap items-center gap-1.5">
              {(vocab.data?.version_outcomes ?? []).map((term) => (
                <button
                  key={term.value}
                  type="button"
                  // The agent's own grade has its own button: recording it again
                  // here would be an Accept that calls itself something else.
                  disabled={answer.isPending || term.value === shown.outcome}
                  title={
                    term.value === shown.outcome
                      ? "This is the proposed grade: Accept it"
                      : undefined
                  }
                  onClick={() => send({ kind: "change", outcome: term.value as VersionOutcome })}
                  className={cn(
                    "rounded-full border border-border px-2.5 py-0.5 text-xs transition-colors",
                    "hover:bg-muted disabled:opacity-50",
                  )}
                >
                  Record {term.label}
                </button>
              ))}
            </div>
          </fieldset>

          <form
            id={noteId}
            hidden={!dismissing}
            className="mt-2 space-y-1"
            data-testid="outcome-card-dismiss"
            onSubmit={(event) => {
              event.preventDefault();
              send({ kind: "dismiss", note: note.trim() });
            }}
          >
            <label htmlFor={`${noteId}-note`} className="block text-muted-foreground text-xs">
              Why not? Optional, and the agent is told it.
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                id={`${noteId}-note`}
                maxLength={NOTE_MAX}
                value={note}
                placeholder="one shot is not a result"
                className="h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onChange={(event) => setNote(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    event.stopPropagation();
                    setDismissing(false);
                    setNote("");
                  }
                }}
              />
              <Button type="submit" size="sm" variant="outline" disabled={answer.isPending}>
                Dismiss it
              </Button>
            </div>
          </form>

          <p className="mt-2 text-muted-foreground text-xs">
            Nothing is recorded until you accept it.{" "}
            {shown.version_outcome == null
              ? "If the agent also proposes the next version, accepting that records this grade too."
              : "The outcome is already recorded, so accepting a version does not record this grade."}
          </p>
        </>
      ) : (
        <Decided
          row={shown}
          // After a press the answer in hand is the moment of the press; the live row is
          // what a later write from the strip below moves, so it is read once it has caught up.
          current={row.status === "proposed" ? shown : row}
          labelOf={labelOf}
          recipeId={recipeId}
        />
      )}

      {failure ? (
        <p role="alert" className="mt-2 text-destructive text-sm" data-testid="outcome-card-error">
          {outcomeFailure(failure)}
        </p>
      ) : null}
    </div>
  );
}

function Decided({
  row,
  current,
  labelOf,
  recipeId,
}: {
  row: OutcomeProposal;
  /** The same card read as late as possible: what the version says now. */
  current: OutcomeProposal;
  labelOf: (value: string | null | undefined) => string;
  recipeId: number;
}) {
  const when = formatTime(row.decided_at ?? null);
  const page = (
    <Link to={`/recipes/${recipeId}`} className="underline underline-offset-2">
      Recipe page
    </Link>
  );
  let record: ReactNode;
  if (row.status === "accepted") {
    record = (
      <p className="text-sm" data-testid="outcome-card-decided">
        Accepted: {row.version_label}'s outcome is recorded as {labelOf(row.outcome)}.{" "}
        <span className="text-muted-foreground">{when}</span>
      </p>
    );
  } else if (row.status === "changed") {
    record = (
      <p className="text-sm" data-testid="outcome-card-decided">
        You recorded {labelOf(row.recorded_outcome)} instead of {labelOf(row.outcome)} for{" "}
        {row.version_label}. <span className="text-muted-foreground">{when}</span>
      </p>
    );
  } else if (row.status === "dismissed") {
    record = (
      <p className="text-sm" data-testid="outcome-card-decided">
        Dismissed{row.decision_note ? `: “${row.decision_note}”.` : "."} Nothing was recorded; the
        outcome is still whatever the {page} says.
      </p>
    );
  } else {
    record = (
      <p className="text-sm" data-testid="outcome-card-decided">
        A newer grade replaced this one, so it was never applied.
      </p>
    );
  }
  // Older cards of a version stay plain records: only the newest says what the version is now
  // and offers to change it, or two cards of one version would offer the same write.
  if (!current.newest_for_version) return record;
  return (
    <>
      {record}
      <VersionNow row={current} recordedByThisCard={recordedBy(row)} />
    </>
  );
}

/** What this card says it recorded, or undefined when it recorded nothing. */
function recordedBy(row: OutcomeProposal): string | undefined {
  if (row.status === "accepted") return row.outcome;
  if (row.status === "changed") return row.recorded_outcome ?? row.outcome;
  return undefined;
}

/**
 * The version's outcome now, beside an answered card: when it differs from what the card says it
 * recorded, how many shots were counted since, and the strip to change it from here.
 *
 * It writes through the Recipe page's route, so a grade recorded here is the same write as one
 * recorded there, and the card never turns into a second way to answer the proposal.
 */
function VersionNow({
  row,
  recordedByThisCard,
}: {
  row: OutcomeProposal;
  recordedByThisCard: string | undefined;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const reasonId = useId();
  const recorded = row.version_outcome != null;
  const differs =
    recordedByThisCard !== undefined && (row.version_outcome ?? null) !== recordedByThisCard;
  const recordedOn = recorded ? (row.version_outcome_counted_shots ?? null) : null;
  const more = recordedOn !== null ? row.counted_shots_now - recordedOn : 0;
  // The same gate as the Recipe page's row: the version's prediction and the counted shots now.
  const { canRecord, reason } = outcomeGate(row.version_has_prediction, row.counted_shots_now);

  return (
    <div className="mt-2 space-y-1.5" data-testid="outcome-card-now">
      {differs ? (
        <p className="text-sm" data-testid="outcome-card-now-differs">
          <span className="text-muted-foreground text-xs">
            {row.version_label}'s outcome is {recorded ? "now recorded as " : "open again"}
          </span>
          {recorded ? <OutcomeBadge state={row.version_outcome as string} /> : null}
          <span className="text-muted-foreground text-xs">.</span>
        </p>
      ) : null}
      {more > 0 ? (
        <p className="text-muted-foreground text-xs" data-testid="outcome-card-since">
          {more} more counted {more === 1 ? "shot" : "shots"} since the outcome was recorded on{" "}
          {recordedOn}.
        </p>
      ) : null}
      {canRecord || recorded ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((shown) => !shown)}
        >
          {outcomeButtonLabel({ recorded, canRecord, moreSince: 0 })}
        </Button>
      ) : (
        <p
          id={reasonId}
          className="text-muted-foreground text-xs"
          data-testid="outcome-card-reason"
        >
          {reason}
        </p>
      )}
      <div
        id={panelId}
        hidden={!open}
        className="space-y-2 rounded-md border border-border bg-muted/30 p-2"
        data-testid="outcome-card-strip"
      >
        {open ? (
          <OutcomePanel
            recipeId={row.recipe_id}
            versionId={row.recipe_version_id}
            outcome={row.version_outcome ?? null}
            outcomeNote={row.version_outcome_note}
            canRecord={canRecord}
            reason={reason}
            onClose={() => setOpen(false)}
          />
        ) : null}
      </div>
    </div>
  );
}
