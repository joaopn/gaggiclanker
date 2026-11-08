import { Check, Globe, Trash2 } from "lucide-react";
import { ApiClientError } from "@/api/client";
import type { GeneralInsightDeletion } from "@/api/types";
import { InlineMarkdown } from "@/components/chat/markdown";
import { useChatThreadId } from "@/components/chat/tellAgent";
import { recipesWords } from "@/components/knowledge/ScopePicker";
import { Button } from "@/components/ui/button";
import {
  useAnswerGeneralInsightDeletion,
  useGeneralInsightDeletion,
  useKnowledgeInsight,
} from "@/hooks/useKnowledge";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { staleWords } from "@/lib/insightDeletionWords";
import { attempt } from "@/lib/mutations";

/** What the tool said when it asked: shown until the live proposal is read. */
export type GeneralDeletionFallback = {
  insightId: number;
  text: string;
  said: string;
  scopeLabels: string[];
  reach: number | null;
};

/** What a stale proposal says when its insight was deleted another way. */
const GONE = "It was deleted another way since the agent asked: nothing was left to delete.";

/**
 * An agent's proposal to delete a general insight, and the two buttons that answer it.
 *
 * A general insight is told to every matching Recipe's conversation, so deleting one changes
 * what all of them are told. The agent may ask only because **you** said so ("You said"), and
 * this card changes nothing until **Delete** is pressed; **Keep** leaves it exactly as it is.
 * Neither sends a chat message: the agent hears the answer at its next turn, from the record.
 *
 * Read live from the proposal (and the insight it names), like the other cards; the tool's own
 * output is shown until that read arrives. A press that fails is said in words, and a ref
 * (`useSingleFlight`) lets only one press through however fast the second click comes.
 */
export function GeneralInsightDeletionCard({
  proposalId,
  fallback,
}: {
  proposalId: number;
  fallback: GeneralDeletionFallback;
}) {
  const proposal = useGeneralInsightDeletion(proposalId);

  if (!proposal.data) {
    return (
      <Shell status="loading">
        <Heading>{`Delete general insight #${fallback.insightId}?`}</Heading>
        <Text text={fallback.text} />
        <Where labels={fallback.scopeLabels} reach={fallback.reach} source={null} />
        <Said said={fallback.said} />
        <p className="mt-1 text-muted-foreground text-xs">waiting for you</p>
      </Shell>
    );
  }
  return <Live row={proposal.data} fallback={fallback} />;
}

function Shell({ status, children }: { status: string; children: React.ReactNode }) {
  return (
    <div className="m-2 mt-0 min-w-0" data-testid="propose-card-general_insight_deletion">
      <div
        className="min-w-0 rounded-lg border border-primary/40 bg-primary/5 p-3"
        data-testid="general-deletion-card"
        data-status={status}
      >
        {children}
      </div>
    </div>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 flex flex-wrap items-center gap-2 text-sm">
      <Globe className="size-4 shrink-0 text-primary" aria-hidden="true" />
      <span className="min-w-0 break-words font-medium" data-testid="general-deletion-title">
        {children}
      </span>
    </div>
  );
}

function Text({ text }: { text: string }) {
  return (
    <p className="break-words text-sm" data-testid="general-deletion-text">
      {text ? <InlineMarkdown text={text} /> : "That insight has since been removed."}
    </p>
  );
}

function Where({
  labels,
  reach,
  source,
}: {
  labels: string[];
  reach: number | null;
  source: string | null;
}) {
  const parts = [
    ...(source ? [source] : []),
    labels.length > 0 ? labels.join(" · ") : "every Recipe",
    ...(reach !== null && reach > 0 ? [`told to ${recipesWords(reach)} now`] : []),
  ];
  return (
    <p
      className="mt-1 break-words text-muted-foreground text-xs"
      data-testid="general-deletion-where"
    >
      {parts.join(" · ")}
    </p>
  );
}

function Said({ said }: { said: string }) {
  if (!said) return null;
  return (
    <p className="mt-2 break-words text-sm" data-testid="general-deletion-said">
      <span className="text-muted-foreground">You said: </span>“{said}”
    </p>
  );
}

/** A failed press, in words: the server's own sentence for a refusal, never an id or a code. */
export function generalDeletionFailure(error: Error): string {
  if (error instanceof ApiClientError) {
    const details = error.details;
    if (error.code === "INSIGHT_DELETION_DECIDED" && details && typeof details === "object") {
      const message = (details as { message?: unknown }).message;
      if (typeof message === "string") return `${message} Your press changed nothing.`;
    }
    return `${error.sentence} Your press changed nothing.`;
  }
  return `${error.message} Your press changed nothing.`;
}

function Live({
  row,
  fallback,
}: {
  row: GeneralInsightDeletion;
  fallback: GeneralDeletionFallback;
}) {
  const answer = useAnswerGeneralInsightDeletion();
  const threadId = useChatThreadId();
  const singleFlight = useSingleFlight();
  // Who the insight is from, read from the insight itself while it exists.
  const insight = useKnowledgeInsight(row.insight_id ?? undefined);

  // What the server said to the last press on this card, before the reads it invalidated land.
  const answered = answer.data?.id === row.id && !answer.isPending ? answer.data : null;
  const shown = answered ?? row;
  const waiting = shown.status === "proposed";
  const failure = answer.variables?.proposalId === row.id && answer.error ? answer.error : null;
  const insightId = shown.insight_id ?? fallback.insightId;
  const reach = shown.reach ?? null;
  const source = insight.data
    ? insight.data.pattern_run_id
      ? "from Find patterns"
      : insight.data.source === "user"
        ? "your note"
        : null
    : null;

  function send(choice: "delete" | "keep") {
    singleFlight((release) => {
      void attempt(() =>
        answer.mutateAsync({ proposalId: row.id, answer: choice, threadId }),
      ).finally(release);
    });
  }

  return (
    <Shell status={shown.status}>
      <Heading>
        {waiting ? `Delete general insight #${insightId}?` : `General insight #${insightId}`}
      </Heading>
      <Text text={shown.insight_text} />
      {shown.insight_id !== null ? (
        <Where labels={shown.scope_labels ?? []} reach={reach} source={source} />
      ) : null}
      <Said said={fallback.said} />
      <p className="mt-2 whitespace-pre-wrap break-words text-muted-foreground text-sm">
        <span className="font-medium text-foreground">Why: </span>
        <span data-testid="general-deletion-reason">{shown.reason}</span>
      </p>

      {waiting ? (
        <>
          <p className="mt-2 text-muted-foreground text-xs" data-testid="general-deletion-note">
            {changesWords(reach)} Nothing is deleted until you press Delete.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={answer.isPending}
              onClick={() => send("keep")}
            >
              <Check className="size-3.5" aria-hidden="true" />
              Keep
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={answer.isPending}
              onClick={() => send("delete")}
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              Delete
            </Button>
          </div>
        </>
      ) : (
        <p className="mt-2 text-sm" data-testid="general-deletion-decided">
          {decidedWords(shown)}
        </p>
      )}

      {failure ? (
        <p
          role="alert"
          className="mt-2 break-words text-destructive text-sm"
          data-testid="general-deletion-error"
        >
          {generalDeletionFailure(failure)}
        </p>
      ) : null}
    </Shell>
  );
}

/** What deleting it changes, by how many Recipes are told it now; nothing said when unknown. */
export function changesWords(reach: number | null): string {
  if (reach === null) return "";
  if (reach === 0) return "No Recipe is told it now.";
  if (reach === 1) return "Deleting it changes what 1 Recipe's conversations are told.";
  return `Deleting it changes what all ${reach} Recipes' conversations are told.`;
}

function decidedWords(row: GeneralInsightDeletion): string {
  if (row.status === "deleted") return "Deleted.";
  if (row.status === "kept") return "Kept.";
  if (row.status === "stale") return staleWords(row.stale_cause, GONE);
  return "A newer proposal for this insight replaced this one.";
}
