import { Check, Globe, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiClientError } from "@/api/client";
import type { KnowledgeInsight, KnowledgeInsightScope } from "@/api/types";
import { InlineMarkdown } from "@/components/chat/markdown";
import { useChatRecipeId, useChatThreadId } from "@/components/chat/tellAgent";
import {
  recipesWords,
  ScopePicker,
  scopeLabels,
  statedScope,
} from "@/components/knowledge/ScopePicker";
import { Button } from "@/components/ui/button";
import {
  useAnswerInsight,
  useKnowledgeInsight,
  useScopeOptions,
  useScopeReach,
} from "@/hooks/useKnowledge";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";

/** What the tool said when it asked: shown until the live row is read. */
export type GeneralRequestFallback = {
  text: string;
  said: string;
  scopeLabels: string[];
  reach: number | null;
};

/**
 * A sentence the agent asks you to put in front of every matching conversation.
 *
 * One meaning: **the agent asks, you decide**. "You said" quotes your own message it came
 * from, so you can check its wording against yours. The Text and what it applies to are the
 * agent's proposal and editable here; **Add** sends them with the confirmation in one request
 * (the only thing that makes it known, from your next message on, to every Recipe's
 * conversation it matches), **Dismiss** turns it down and writes nothing that teaches. Neither
 * sends a chat message: the agent hears the answer at its next turn, from the record.
 *
 * Read live from the insight itself, like the other cards: edited or deleted on the Knowledge
 * page, the card shows what is there now. The tool's output is shown until that read arrives.
 */
export function GeneralInsightRequestCard({
  insightId,
  fallback,
}: {
  insightId: number;
  fallback: GeneralRequestFallback;
}) {
  const insight = useKnowledgeInsight(insightId);

  if (!insight.data) {
    const gone = insight.error instanceof ApiClientError && insight.error.status === 404;
    if (gone) {
      return (
        <Shell
          state="deleted"
          heading={`General insight #${insightId} · deleted on the Knowledge page`}
        />
      );
    }
    return (
      <Shell state="loading" heading="General insight · waiting for you">
        <Said said={fallback.said} />
        <p className="mt-2 break-words text-sm">{fallback.text}</p>
        <p className="mt-1 break-words text-muted-foreground text-xs">
          Applies to{" "}
          {fallback.scopeLabels.length > 0 ? fallback.scopeLabels.join(" · ") : "every Recipe"}
          {fallback.reach !== null ? ` · matches ${recipesWords(fallback.reach)} now` : ""}
        </p>
        {insight.isError ? (
          <p className="mt-2 text-muted-foreground text-xs">Could not read where it stands now.</p>
        ) : null}
      </Shell>
    );
  }

  const row = insight.data;
  if (row.dismissed) {
    return (
      <Shell state="dismissed" heading="General insight · dismissed">
        <p className="break-words text-sm" data-testid="general-request-dismissed">
          Not saved. “{row.text}”
        </p>
      </Shell>
    );
  }
  if (row.confirmed) return <Added row={row} named={fallback.scopeLabels} />;
  return <Waiting row={row} said={fallback.said} named={fallback.scopeLabels} />;
}

function Shell({
  state,
  heading,
  children,
}: {
  state: "loading" | "waiting" | "added" | "dismissed" | "deleted";
  heading: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="m-2 mt-0 min-w-0" data-testid="propose-card-general_insight_request">
      <div
        className="min-w-0 rounded-lg border border-primary/40 bg-primary/5 p-3"
        data-testid="general-request"
        data-state={state}
      >
        <div className="mb-1.5 flex flex-wrap items-center gap-2 text-sm">
          <Globe className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <span className="min-w-0 break-words font-medium">{heading}</span>
        </div>
        {children}
      </div>
    </div>
  );
}

function Said({ said }: { said: string }) {
  return (
    <p className="break-words text-sm" data-testid="general-request-said">
      <span className="text-muted-foreground">You said: </span>“{said}”
    </p>
  );
}

function sentenceOf(error: Error): string {
  return error instanceof ApiClientError ? error.sentence : error.message;
}

function Waiting({ row, said, named }: { row: KnowledgeInsight; said: string; named: string[] }) {
  const answer = useAnswerInsight();
  const threadId = useChatThreadId();
  const recipeId = useChatRecipeId();
  const singleFlight = useSingleFlight();
  // The agent's wording and scope, as a starting point: edited here, sent with the Add.
  const [text, setText] = useState(row.text);
  const [scope, setScope] = useState<KnowledgeInsightScope>(statedScope(row.scope));
  const failure = answer.variables?.id === row.id && answer.error ? answer.error : null;
  const failedAdd = failure !== null && answer.variables?.answer === "add";

  function send(choice: "add" | "dismiss") {
    singleFlight((release) => {
      void attempt(() =>
        answer.mutateAsync({
          id: row.id,
          answer: choice,
          threadId,
          edit: choice === "add" ? { text: text.trim(), scope: statedScope(scope) } : undefined,
        }),
      ).finally(release);
    });
  }

  return (
    <Shell state="waiting" heading="General insight · waiting for you">
      <Said said={said} />
      <label className="mt-2 block text-sm">
        <span className="text-muted-foreground text-xs">Text</span>
        <textarea
          className="mt-1 h-20 w-full rounded-md border border-input bg-background p-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          data-testid="general-request-text"
          value={text}
          maxLength={2000}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <div className="mt-2">
        <ScopePicker recipeId={recipeId} value={scope} onChange={setScope} named={named} />
      </div>
      <p className="mt-2 text-muted-foreground text-xs" data-testid="general-request-note">
        Adding it tells every matching Recipe's conversation, from its next message.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="ghost"
          aria-disabled={answer.isPending}
          disabled={answer.isPending}
          onClick={() => send("dismiss")}
        >
          <X className="size-3.5" aria-hidden="true" />
          Dismiss
        </Button>
        <Button size="sm" disabled={answer.isPending || !text.trim()} onClick={() => send("add")}>
          <Check className="size-3.5" aria-hidden="true" />
          Add
        </Button>
      </div>
      {failure ? (
        <p
          role="alert"
          className="mt-2 break-words text-destructive text-sm"
          data-testid="general-request-error"
        >
          {sentenceOf(failure)} {failedAdd ? "It was not added." : "It was not dismissed."}
        </p>
      ) : null}
    </Shell>
  );
}

function Added({ row, named }: { row: KnowledgeInsight; named: string[] }) {
  const recipeId = useChatRecipeId();
  const options = useScopeOptions(recipeId);
  const scope = statedScope(row.scope);
  const reach = useScopeReach(scope);
  // Named only once the names are known: the row is what stands now, and the words the agent
  // asked with may no longer be its scope.
  const labels = options.data ? scopeLabels(scope, options.data, named) : null;
  return (
    <Shell state="added" heading={`General insight #${row.id} · added`}>
      <p className="break-words text-sm" data-testid="general-request-text-live">
        <InlineMarkdown text={row.text} />
      </p>
      {labels ? (
        <p
          className="mt-1 break-words text-muted-foreground text-xs"
          data-testid="general-request-applies"
        >
          Applies to {labels.length > 0 ? labels.join(" · ") : "every Recipe"}
          {reach.data ? ` · matches ${recipesWords(reach.data.recipes)} now` : ""}
        </p>
      ) : null}
      <p className="mt-2 text-sm">
        <Link to="/knowledge?tab=insights" className="underline underline-offset-2">
          Edit or delete it on the Knowledge page →
        </Link>
      </p>
    </Shell>
  );
}
