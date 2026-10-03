import { Check, Lightbulb, Undo2, X } from "lucide-react";
import { Link } from "react-router-dom";
import { useChatThreadId } from "@/components/chat/tellAgent";
import { evidenceShots } from "@/components/knowledge/InsightCard";
import { Button } from "@/components/ui/button";
import { useAnswerInsight, useKnowledgeInsight } from "@/hooks/useKnowledge";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";

/**
 * Something the agent learned about this Set, and the two buttons that answer it.
 *
 * An insight written in a Set's conversation is about **that Set**: it is stored
 * with the Set and the version the conversation is about, and if the person adds
 * it, only this Set's later conversations are told it — never another Set's, even
 * one on the same bean and grinder. Until they do it is not evidence, and the
 * card says so. **Add** is the confirmation (the only thing that makes it known),
 * **Dismiss** turns it down and keeps it for this conversation alone.
 *
 * Read live from the insight itself, like the other cards: the tool's output is
 * the moment it was called. The answer is not sent to the agent as a message; it
 * is told at the start of its next turn, from the record.
 */
export function ChatInsightCard({ insightId, text }: { insightId: number; text: string }) {
  const insight = useKnowledgeInsight(insightId);
  const answer = useAnswerInsight();
  const threadId = useChatThreadId();
  const singleFlight = useSingleFlight();

  if (!insight.data) {
    return (
      <div className="m-2 mt-0" data-testid="propose-card-insight">
        <p className="rounded-md border border-primary/40 bg-primary/5 p-2 text-sm">
          {text}
          <span className="block text-muted-foreground text-xs">
            {insight.isError
              ? "This insight no longer exists."
              : "waiting for you to add or dismiss it"}
          </span>
        </p>
      </div>
    );
  }

  const row = insight.data;
  const evidence = evidenceShots(row);
  const state = row.dismissed ? "dismissed" : row.confirmed ? "added" : "waiting";
  const failure = answer.variables?.id === insightId && answer.error ? answer.error : null;

  function send(choice: "add" | "dismiss" | "take_back") {
    singleFlight((release) => {
      void attempt(() => answer.mutateAsync({ id: insightId, answer: choice, threadId })).finally(
        release,
      );
    });
  }

  return (
    <div className="m-2 mt-0" data-testid="propose-card-insight">
      <div
        className="rounded-lg border border-primary/40 bg-primary/5 p-3"
        data-testid="chat-insight"
        data-state={state}
      >
        <div className="mb-1.5 flex flex-wrap items-center gap-2 text-sm">
          <Lightbulb className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <span className="font-medium">
            {state === "waiting" ? "An insight is waiting for you" : "An insight that was proposed"}
          </span>
        </div>

        <p className="mb-2 text-sm" data-testid="chat-insight-text">
          {row.text}
        </p>

        <p className="mb-2 text-muted-foreground text-xs" data-testid="chat-insight-about">
          About this Set only
          {row.set_version_label
            ? `, learned at ${row.set_version_label}`
            : ", version not recorded"}
          .
        </p>

        {evidence.length > 0 ? (
          <p className="mb-2 flex flex-wrap items-center gap-1.5 text-muted-foreground text-xs">
            <span>from</span>
            {evidence.map((shotId) => (
              <Link
                key={shotId}
                to={`/shots/${shotId}`}
                className="rounded-full border border-border px-1.5 font-mono hover:bg-accent"
              >
                shot {shotId}
              </Link>
            ))}
          </p>
        ) : null}

        {state === "waiting" ? (
          <>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={answer.isPending} onClick={() => send("add")}>
                <Check className="size-3.5" aria-hidden="true" />
                Add
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={answer.isPending}
                onClick={() => send("dismiss")}
              >
                <X className="size-3.5" aria-hidden="true" />
                Dismiss
              </Button>
            </div>
            <p className="mt-2 text-muted-foreground text-xs">
              Not evidence until you add it. Added, it is told to this Set's later conversations and
              no other Set's.
            </p>
          </>
        ) : state === "added" ? (
          <div className="flex flex-wrap items-center gap-2" data-testid="chat-insight-decided">
            <p className="text-sm">
              Added: this Set's later conversations will be told it. It is on{" "}
              <Link to={`/sets/${row.set_id}`} className="underline underline-offset-2">
                the Set's page
              </Link>
              .
            </p>
            <Button
              size="sm"
              variant="ghost"
              disabled={answer.isPending}
              onClick={() => send("take_back")}
            >
              <Undo2 className="size-3.5" aria-hidden="true" />
              Take back
            </Button>
          </div>
        ) : (
          <p className="text-sm" data-testid="chat-insight-decided">
            Dismissed: it will not be put in front of the model. It stays here, so the agent is told
            you turned it down.
          </p>
        )}

        {failure ? (
          <p
            role="alert"
            className="mt-2 text-destructive text-sm"
            data-testid="chat-insight-error"
          >
            {failure.message}
          </p>
        ) : null}
      </div>
    </div>
  );
}
