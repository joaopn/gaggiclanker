import { ArrowRight, Check, X } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { SetProposal } from "@/api/types";
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
  const waiting = proposal.status === "proposed";

  return (
    <div
      className="rounded-lg border border-primary/40 bg-primary/5 p-3"
      data-testid="proposal-card"
      data-status={proposal.status}
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium text-sm">
          {waiting ? "A change is waiting for you" : "A change that was proposed"}
        </span>
        <div className="flex items-center gap-2">
          {waiting && !proposal.base_is_current ? (
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

      {proposal.changes.length > 0 ? (
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
                    decide.mutateAsync({ setId, proposalId: proposal.id, decision: "accept" }),
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

          <p className="mt-2 text-muted-foreground text-xs">
            {proposal.readable
              ? "Accepting records a new version of this Set. Nothing is sent to the machine either way."
              : "The change stored with this one is damaged and cannot be read, so there is nothing to accept. Decline it and ask in the conversation again."}
          </p>
        </>
      ) : (
        <Decided proposal={proposal} />
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
