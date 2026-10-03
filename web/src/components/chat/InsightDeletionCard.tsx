import { Check, Trash2 } from "lucide-react";
import { ApiClientError } from "@/api/client";
import type { InsightDeletion } from "@/api/types";
import { useChatThreadId } from "@/components/chat/tellAgent";
import { Button } from "@/components/ui/button";
import { useAnswerInsightDeletion, useInsightDeletions } from "@/hooks/useKnowledge";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";
import { formatTime } from "@/lib/shots";

/**
 * An insight the agent proposed deleting, and the two buttons that answer it.
 *
 * An added insight is told to every later conversation of its Set, so one the shots
 * no longer support is a standing mistake in the agent's prompt. The agent may say so
 * with its reason, but **removing is the person's**: this card changes nothing until
 * **Delete** is pressed, and then the insight is removed outright (nothing of it is
 * kept but the words on this card). **Keep** leaves it exactly as it is.
 *
 * Read live from the Set's deletion proposals rather than rendered from the tool's
 * output: the output is the moment it was called, and the card is read again whenever
 * the conversation is scrolled to, after the insight may have been deleted on the Set
 * page or the proposal replaced by a newer one. An answer is not sent to the agent as a
 * message; it is told at the start of its next turn, from the record.
 */

export type InsightDeletionCardProps = {
  setId: number;
  proposalId: number;
  /** What the tool said, shown until the live row has been read. */
  fallback: { insightId: number; text: string };
};

/** A failed press, said in words the person can act on. */
export function insightDeletionFailure(error: Error): string {
  if (error instanceof ApiClientError) {
    if (error.code === "INSIGHT_DELETION_DECIDED") {
      return "This proposal was already answered, here in another tab or by a newer proposal. The card will show how once it refreshes.";
    }
    if (error.code === "INSIGHT_NOT_ADDED") {
      return "That insight is not added any more (it was taken back), so there is nothing to delete. The card will show it once it refreshes.";
    }
  }
  return error.message;
}

export function InsightDeletionCard({ setId, proposalId, fallback }: InsightDeletionCardProps) {
  // By conversation, uncapped: the card finds its proposal however old it is.
  const deletions = useInsightDeletions(setId, useChatThreadId());
  const row = deletions.data?.items.find((item) => item.id === proposalId);
  if (!row) {
    return (
      <div className="m-2 mt-0" data-testid="propose-card-insight_deletion">
        <p className="rounded-md border border-primary/40 bg-primary/5 p-2 text-sm">
          Proposed deleting an insight: {fallback.text}
          <span className="block text-muted-foreground text-xs">waiting for you</span>
        </p>
      </div>
    );
  }
  return (
    <div className="m-2 mt-0" data-testid="propose-card-insight_deletion">
      <LiveInsightDeletionCard setId={setId} row={row} />
    </div>
  );
}

function LiveInsightDeletionCard({ setId, row }: { setId: number; row: InsightDeletion }) {
  const answer = useAnswerInsightDeletion();
  const threadId = useChatThreadId();
  const singleFlight = useSingleFlight();

  // What the server said to the last press on this card, before the lists it
  // invalidated have been read again.
  const answered =
    answer.data?.proposal.id === row.id && !answer.isPending ? answer.data.proposal : null;
  const shown = answered ?? row;
  const waiting = shown.status === "proposed";
  const failure = answer.variables?.proposalId === row.id && answer.error ? answer.error : null;

  function send(choice: "delete" | "keep") {
    singleFlight((release) => {
      void attempt(() =>
        answer.mutateAsync({ setId, proposalId: row.id, answer: choice, threadId }),
      ).finally(release);
    });
  }

  return (
    <div
      className="rounded-lg border border-primary/40 bg-primary/5 p-3"
      data-testid="insight-deletion-card"
      data-status={shown.status}
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium text-sm">
          {waiting
            ? "Deleting an insight is proposed, and waits for you"
            : "Deleting an insight was proposed"}
        </span>
        <span className="text-muted-foreground text-xs">{formatTime(row.created_at)}</span>
      </div>

      <p className="mb-2 text-sm" data-testid="insight-deletion-text">
        {shown.insight_text || "That insight has since been removed."}
      </p>
      <p className="mb-2 whitespace-pre-wrap text-muted-foreground text-sm">
        <span className="font-medium text-foreground">Why: </span>
        <span data-testid="insight-deletion-reason">{shown.reason}</span>
      </p>

      {waiting ? (
        <>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="destructive"
              disabled={answer.isPending}
              onClick={() => send("delete")}
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              Delete
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={answer.isPending}
              onClick={() => send("keep")}
            >
              <Check className="size-3.5" aria-hidden="true" />
              Keep
            </Button>
          </div>
          <p className="mt-2 text-muted-foreground text-xs">
            Nothing is deleted until you press Delete: until then the insight stays and is still
            told to this Set's conversations.
          </p>
        </>
      ) : (
        <p className="text-sm" data-testid="insight-deletion-decided">
          {decidedWords(shown)}
        </p>
      )}

      {failure ? (
        <p
          role="alert"
          className="mt-2 text-destructive text-sm"
          data-testid="insight-deletion-error"
        >
          {insightDeletionFailure(failure)}
        </p>
      ) : null}
    </div>
  );
}

function decidedWords(row: InsightDeletion): string {
  if (row.status === "deleted") {
    return "Deleted: the insight is gone from this Set. Only this card remembers what it said.";
  }
  if (row.status === "kept") {
    return "Kept: the insight stays as it was, and the agent is told you kept it.";
  }
  if (row.status === "stale") {
    return "Already gone: the insight was removed another way, so there was nothing to delete.";
  }
  return "A newer proposal for this insight replaced this one.";
}
