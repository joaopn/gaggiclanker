import { ArrowRight, Check, X } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { ApiClientError } from "@/api/client";
import type { FieldChange, SetProposal } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDecideProposal } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { formatTime } from "@/lib/shots";

/**
 * A change an agent has proposed, and the two buttons that answer it.
 *
 * One component for both places it appears — the conversation it was argued in
 * and the Set page's log — because they are the same question, and a person who
 * reads it in one place and answers it in the other must not be shown two
 * different accounts of what is on offer.
 *
 * What it says, in order, is the order the decision is made in: what would
 * change, why, and what that is expected to do. The prediction is the point.
 * A proposal that has already been answered keeps its card and says how it was
 * answered, so scrolling back through a conversation is reading a record rather
 * than a set of live buttons.
 *
 * **A Set's first recipe** (`kind: "design"`) is the same question asked about
 * a Set that has no recipe yet, and it is drawn as a recipe rather than as a
 * diff: "not set → 18 g" five times over says less than "18 g in, 36 g out".
 * It has no prediction and nothing it is compared to — a version 1 is a
 * baseline, not a change to anything — and its profile is a draft of its own,
 * waiting on the Profiles page, so the card links there.
 */

export type ProposalCardProps = {
  setId: number;
  proposal: SetProposal;
  /** The Set page links into the chat; the chat's own card does not. */
  showThreadLink?: boolean;
};

/** What the log's diff rows say when a side is missing. The same words. */
function absent(fromProfile: boolean, side: "before" | "after"): string {
  if (fromProfile) return "no temperature stated";
  return side === "before" ? "not set" : "cleared";
}

/** How long a turn-down may be, as the route caps it. */
const NOTE_MAX = 500;

/** Where a draft waits for a person to approve and push it. */
const DRAFTS_HREF = "/profiles#staged";

/**
 * The recipe a first-recipe card would fill version 1 with, read off its diff.
 *
 * The server draws every proposal as a diff against the version it would
 * change, and for a design that version is empty, so each field's `after` side
 * *is* the recipe. Read from there rather than asked for separately, so the
 * card cannot say something the accept would not write. Once accepted, version
 * 1 holds the same values and the diff is empty: the card then says what
 * happened and the Set page shows the recipe.
 */
export function designRecipe(changes: FieldChange[]) {
  const after = (field: string) => changes.find((change) => change.field === field)?.after ?? null;
  const dose = after("dose_g");
  const target = after("target_yield_g");
  const doseG = dose === null ? Number.NaN : Number.parseFloat(dose);
  const yieldG = target === null ? Number.NaN : Number.parseFloat(target);
  return {
    profile: after("profile_version_id"),
    temperature: after("profile_temperature_c"),
    grind: after("grind_setting"),
    // A grind with no number behind it is words relative to the person's usual
    // setting: nothing anchored a position on this grinder's dial.
    grindIsAbsolute: after("grind_value") !== null,
    dose,
    target,
    ratio: doseG > 0 && yieldG > 0 ? `1:${(yieldG / doseG).toFixed(1)}` : null,
  };
}

/**
 * The first recipe, as the numbers it would put on version 1.
 *
 * The grind is the one figure rendered differently, for the same reason as on
 * the starting-point card: a grind the agent could not anchor on this grinder
 * is words relative to the usual setting, and reading "two finer" as a dial
 * position loses a bag finding out.
 */
function RecipeFigures({ proposal }: { proposal: SetProposal }) {
  const recipe = designRecipe(proposal.changes);
  if (proposal.changes.length === 0) return null;
  return (
    <dl className="mb-2 grid gap-x-3 gap-y-1 text-sm sm:grid-cols-2" data-testid="proposal-recipe">
      <Figure label="Profile">
        {recipe.profile ?? "none named"}
        {proposal.draft_id ? (
          <>
            {" · "}
            <Link
              to={DRAFTS_HREF}
              className="underline underline-offset-2"
              data-testid="proposal-draft-link"
            >
              a new draft
            </Link>
          </>
        ) : null}
        {recipe.temperature ? (
          <span className="text-muted-foreground"> · {recipe.temperature}</span>
        ) : null}
      </Figure>
      <Figure label="Grind">
        {recipe.grind ?? "not set"}
        {recipe.grind && !recipe.grindIsAbsolute ? (
          <span className="ml-1 text-muted-foreground" data-testid="proposal-grind-relative">
            (relative to your usual setting — nothing anchors a number on your dial)
          </span>
        ) : null}
      </Figure>
      <Figure label="Dose">{recipe.dose ?? "not set"}</Figure>
      <Figure label="Yield">
        {recipe.target ?? "not set"}
        {recipe.ratio ? <span className="text-muted-foreground"> ({recipe.ratio})</span> : null}
      </Figure>
    </dl>
  );
}

function Figure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 gap-1.5">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/**
 * How a first recipe was answered.
 *
 * Accepted is the one that has a next step, and it is the person's: the Set now
 * has its version 1, but the profile it names is a draft until somebody
 * approves and pushes it, and shots brewed on it only find the Set once it is
 * on the machine. The card says so in those words because nothing else in the
 * conversation will.
 */
function DecidedDesign({ proposal }: { proposal: SetProposal }) {
  if (proposal.status === "accepted") {
    return (
      <p className="text-sm" data-testid="proposal-decided">
        Accepted:{" "}
        <Link to={`/sets/${proposal.set_id}`} className="font-medium underline underline-offset-2">
          version 1
        </Link>{" "}
        is set. Its profile is{" "}
        <Link to={DRAFTS_HREF} className="underline underline-offset-2">
          a draft on the Profiles page
        </Link>{" "}
        for you to approve and push; once it is on the machine, shots brewed on it are filed here.{" "}
        <span className="text-muted-foreground">{formatTime(proposal.decided_at ?? null)}</span>
      </p>
    );
  }
  if (proposal.status === "declined") {
    return (
      <p className="text-sm" data-testid="proposal-decided">
        Declined{proposal.decline_note ? `: “${proposal.decline_note}”` : "."} Its draft was
        discarded, unless it had already been pushed.
      </p>
    );
  }
  return (
    <p className="text-sm" data-testid="proposal-decided">
      A newer card replaced this one, or version 1 was recorded another way first, so it was never
      applied. Its draft was discarded, unless it had already been pushed.
    </p>
  );
}

/**
 * Whether an accept was refused because the card's draft is gone.
 *
 * The one refusal a first recipe has of its own: somebody discarded the draft
 * on the Profiles page, or it was replaced. The card cannot be accepted any
 * more, and the way on is in words — decline it and ask for another — rather
 * than in a toast that has faded by the time anybody reads the card again.
 */
function draftClosed(error: Error | null): boolean {
  return error instanceof ApiClientError && error.code === "PROPOSAL_DRAFT_CLOSED";
}

function Decided({ proposal }: { proposal: SetProposal }) {
  if (proposal.status === "accepted") {
    return (
      <p className="text-sm" data-testid="proposal-decided">
        Accepted as{" "}
        <Link to={`/sets/${proposal.set_id}`} className="font-medium underline underline-offset-2">
          v{proposal.resulting_version_no}
        </Link>{" "}
        <span className="text-muted-foreground">on {formatTime(proposal.decided_at ?? null)}</span>
      </p>
    );
  }
  if (proposal.status === "declined") {
    return (
      <p className="text-sm" data-testid="proposal-decided">
        Declined{proposal.decline_note ? `: “${proposal.decline_note}”` : "."}
      </p>
    );
  }
  return (
    <p className="text-sm" data-testid="proposal-decided">
      Nobody answered it: the Set moved on to another version first, so it was never applied.
    </p>
  );
}

export function ProposalCard({ setId, proposal, showThreadLink = false }: ProposalCardProps) {
  const decide = useDecideProposal();
  const noteId = useId();
  const fieldId = useId();
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  // The card is re-used for whatever proposal it is handed, and a half-typed
  // turn-down must not follow one proposal onto the next. Held in state and
  // reset during render rather than in a ref: StrictMode's double render and a
  // discarded transition both write a ref and then see it equal.
  const [shownFor, setShownFor] = useState(proposal.id);
  if (shownFor !== proposal.id) {
    setShownFor(proposal.id);
    setDeclining(false);
    setNote("");
  }
  // What the server said to the last press on this card, before the lists it
  // invalidated have been read again. Shown straight away: on the Set page the
  // waiting card leaves the page with that refetch, and the answer — above all
  // "version 1 is set, and the profile is a draft" — would go with it.
  const answered =
    decide.data?.proposal.id === proposal.id && !decide.isPending ? decide.data.proposal : null;
  const shown = answered ?? proposal;
  const waiting = shown.status === "proposed";
  const design = shown.kind === "design";
  const refusedForDraft =
    design && decide.variables?.proposalId === proposal.id && draftClosed(decide.error);

  return (
    <div
      className="rounded-lg border border-primary/40 bg-primary/5 p-3"
      data-testid="proposal-card"
      data-status={shown.status}
      data-kind={shown.kind}
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium text-sm">
          {design
            ? waiting
              ? "The first recipe is waiting for you"
              : "A first recipe that was proposed"
            : waiting
              ? "A change is waiting for you"
              : "A change that was proposed"}
        </span>
        <div className="flex items-center gap-2">
          {waiting && !shown.base_is_current ? (
            // The accept will be refused, and saying so before the press is
            // kinder than the toast that follows it.
            <Badge variant="outline" data-testid="proposal-stale">
              the Set has moved on since
            </Badge>
          ) : null}
          <span className="text-muted-foreground text-xs">{formatTime(proposal.created_at)}</span>
        </div>
      </div>

      {!proposal.readable ? (
        <p className="mb-2 text-sm" data-testid="proposal-unreadable">
          The change stored with this proposal cannot be read, so there is nothing to show and
          nothing to accept.
        </p>
      ) : null}

      {design ? (
        <RecipeFigures proposal={shown} />
      ) : proposal.changes.length > 0 ? (
        <ul className="mb-2 flex flex-wrap gap-2" data-testid="proposal-changes">
          {proposal.changes.map((change) => (
            <li
              key={change.field}
              data-field={change.field}
              className="inline-flex items-center gap-1 rounded-md bg-background/70 px-2 py-0.5 text-xs"
            >
              <span className="text-muted-foreground">{change.label}</span>
              <span className="tabular-nums line-through opacity-60">
                {change.before ?? absent(change.from_profile, "before")}
              </span>
              <ArrowRight className="size-3" aria-hidden="true" />
              <span className="font-medium tabular-nums">
                {change.after ?? absent(change.from_profile, "after")}
              </span>
              {change.from_profile ? (
                <span className="text-muted-foreground">· from the profile</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {proposal.reason ? <p className="mb-2 text-sm">{proposal.reason}</p> : null}

      {/* No prediction on a first recipe: a version 1 is the baseline later
          versions are predicted against, and "compared to nothing" would be
          a line saying so at length. */}
      {design ? null : (
        <p className="mb-2 text-sm" data-testid="proposal-prediction">
          <span className="text-muted-foreground text-xs">
            Prediction
            {proposal.compares_to_version_no
              ? ` · compared to v${proposal.compares_to_version_no}`
              : " · compared to nothing"}
          </span>
          <br />
          {proposal.prediction}
        </p>
      )}

      {proposal.combined_reason ? (
        <p className="mb-2 text-muted-foreground text-xs" data-testid="proposal-combined">
          Two things move together: {proposal.combined_reason} The prediction cannot say which of
          them did anything.
        </p>
      ) : null}

      {waiting ? (
        <>
          <div className="flex flex-wrap gap-2">
            {/* No Accept on a proposal nobody can read: there is no change to
                record, and the route refuses it. Declining still works, which
                is how it stops being in the way. */}
            {proposal.readable ? (
              <Button
                size="sm"
                disabled={decide.isPending}
                onClick={() =>
                  void attempt(() =>
                    decide.mutateAsync({
                      setId,
                      proposalId: proposal.id,
                      decision: "accept",
                      kind: proposal.kind,
                      threadId: proposal.thread_id,
                    }),
                  )
                }
              >
                <Check className="size-3.5" aria-hidden="true" />
                Accept
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              disabled={decide.isPending}
              aria-expanded={declining}
              aria-controls={noteId}
              onClick={() => setDeclining((open) => !open)}
            >
              <X className="size-3.5" aria-hidden="true" />
              Decline
            </Button>
          </div>

          {/* Rendered always and toggled with `hidden`, so `aria-controls`
              names something a reader can reach. The note is optional and it
              is the useful half of a decline: it is what the next conversation
              is told about what you did not want. */}
          <form
            id={noteId}
            hidden={!declining}
            className="mt-2 space-y-1"
            data-testid="decline-note"
            onSubmit={(event) => {
              event.preventDefault();
              void attempt(() =>
                decide.mutateAsync({
                  setId,
                  proposalId: proposal.id,
                  decision: "decline",
                  note: note.trim(),
                  kind: proposal.kind,
                  threadId: proposal.thread_id,
                }),
              );
            }}
          >
            <label htmlFor={fieldId} className="block text-muted-foreground text-xs">
              Why not? Optional, and the next conversation is told it.
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                id={fieldId}
                maxLength={NOTE_MAX}
                value={note}
                placeholder="the dose is not the problem"
                className="h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onChange={(event) => setNote(event.target.value)}
                onKeyDown={(event) => {
                  // Escape closes the field and declines nothing. It stops
                  // here rather than travelling: a card inside a page with its
                  // own Escape handler must not lose a half-typed note to one.
                  if (event.key === "Escape") {
                    event.stopPropagation();
                    setDeclining(false);
                    setNote("");
                  }
                }}
              />
              <Button type="submit" size="sm" variant="outline" disabled={decide.isPending}>
                Decline it
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setDeclining(false);
                  setNote("");
                }}
              >
                Cancel
              </Button>
            </div>
          </form>

          {refusedForDraft ? (
            <p className="mt-2 text-destructive text-sm" data-testid="proposal-draft-closed">
              This card's profile draft was discarded or replaced on the Profiles page, so it can no
              longer be accepted. Decline this card and ask the agent for a new one.
            </p>
          ) : null}

          <p className="mt-2 text-muted-foreground text-xs">
            {!proposal.readable
              ? "The change stored with this one is damaged and cannot be read, so there is nothing to accept. Decline it and ask in the conversation again."
              : design
                ? "Accepting makes this the Set's version 1. The profile stays a draft on the Profiles page until you approve and push it: nothing is sent to the machine either way."
                : "Accepting records a new version of this Set. Nothing is sent to the machine either way."}
          </p>
        </>
      ) : design ? (
        <DecidedDesign proposal={shown} />
      ) : (
        <Decided proposal={shown} />
      )}

      {showThreadLink && proposal.thread_id ? (
        <p className="mt-2 text-xs" data-testid="proposal-thread">
          <Link
            to={`/chat?thread=${proposal.thread_id}`}
            className="text-muted-foreground underline underline-offset-2"
          >
            Read the conversation this came from
          </Link>
        </p>
      ) : null}
    </div>
  );
}
