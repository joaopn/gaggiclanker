import { Lightbulb } from "lucide-react";
import { useState } from "react";
import type { KnowledgeInsightScope } from "@/api/types";
import { InsightCard } from "@/components/knowledge/InsightCard";
import { EmptyState } from "@/components/layout/EmptyState";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCreateKnowledgeInsight, useKnowledgeInsights } from "@/hooks/useKnowledge";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { attempt } from "@/lib/mutations";

/**
 * Tier 3: what this archive has learned about *this* kitchen.
 *
 * The proposals come first and unconfirmed, because that is the decision
 * waiting for somebody: an insight the analyzer proposed is a claim about your
 * setup that nothing will act on until you say so. Confirming one puts it in
 * front of every later analysis whose Set matches its scope — which is why the
 * scope badge and the evidence links are on the card rather than behind a
 * disclosure.
 */
export function InsightsTab() {
  const insights = useKnowledgeInsights();
  const create = useCreateKnowledgeInsight();
  const [text, setText] = useState("");
  const [scope, setScope] = useState("{}");
  const [problem, setProblem] = useState("");
  useQueryErrorToast(insights.error, "Could not load the insights");

  // Newest first, against the server's oldest-first order. That order is the
  // right one for a *prompt* — a later insight that qualifies an earlier one
  // only reads correctly after it — and the wrong one for a page, where the
  // proposal you have not seen yet is the one at the bottom.
  const items = [...(insights.data?.items ?? [])].reverse();
  const proposed = items.filter((insight) => !insight.confirmed);
  const confirmed = items.filter((insight) => insight.confirmed);
  const keys = insights.data?.scope_keys ?? [];

  return (
    <div className="space-y-4">
      <SectionCard
        title="Write one yourself"
        description="Scope it with any of the keys below; an insight applies to a Set when every key it names matches. An empty scope is a claim about every shot you pull, so it should be rare."
      >
        <form
          className="space-y-2"
          onSubmit={async (event) => {
            event.preventDefault();
            let parsed: KnowledgeInsightScope;
            try {
              parsed = JSON.parse(scope) as KnowledgeInsightScope;
            } catch (error) {
              // Parsed here rather than sent, so a stray comma is a message
              // beside the box instead of a 400 from three layers away.
              setProblem(error instanceof Error ? error.message : "that is not valid JSON");
              return;
            }
            setProblem("");
            const saved = await attempt(() =>
              // Confirmed on the way in: the person writing it is the person who
              // would confirm it, and a form that makes you save and then press
              // confirm is a form with a bug in it.
              create.mutateAsync({ scope: parsed, text, confirmed: true }),
            );
            if (saved) {
              setText("");
              setScope("{}");
            }
          }}
        >
          <textarea
            className="h-16 w-full rounded-md border border-input bg-background p-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="What you have learned"
            data-testid="insight-text"
            placeholder="The Niche needs two numbers finer for anything anaerobic."
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <textarea
            className="h-16 w-full rounded-md border border-input bg-background p-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Scope"
            data-testid="insight-scope-input"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
          />
          <p className="text-muted-foreground text-xs">Scope keys: {keys.join(", ")}</p>
          {problem ? <p className="text-status-bad-text text-xs">{problem}</p> : null}
          <Button type="submit" size="sm" disabled={create.isPending || !text.trim()}>
            Save
          </Button>
        </form>
      </SectionCard>

      {insights.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Lightbulb}
          title="Nothing learned yet"
          description="The analyser proposes at most two insights per shot, and they land here unconfirmed. Nothing is put in front of the model until you confirm it."
        />
      ) : (
        <>
          {proposed.length > 0 ? (
            <SectionCard
              title="Waiting for you"
              description="Proposed by an analysis or the chat. Open the shots they were drawn from before you confirm — a claim with unchecked evidence is an opinion."
            >
              <ul className="space-y-2" data-testid="proposed-insights">
                {proposed.map((insight) => (
                  <InsightCard key={insight.id} insight={insight} />
                ))}
              </ul>
            </SectionCard>
          ) : null}

          <SectionCard
            title="Confirmed"
            description="These are put in front of every later analysis whose Set matches the scope, above the general rules."
          >
            {confirmed.length === 0 ? (
              <p className="text-muted-foreground text-sm">Nothing confirmed yet.</p>
            ) : (
              <ul className="space-y-2" data-testid="confirmed-insights">
                {confirmed.map((insight) => (
                  <InsightCard key={insight.id} insight={insight} />
                ))}
              </ul>
            )}
          </SectionCard>
        </>
      )}
    </div>
  );
}
