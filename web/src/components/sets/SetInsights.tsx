import { Check, Undo2, X } from "lucide-react";
import { Link } from "react-router-dom";
import type { KnowledgeInsight } from "@/api/types";
import { evidenceShots, scopeLabel } from "@/components/knowledge/InsightCard";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAnswerInsight, useKnowledgeInsights } from "@/hooks/useKnowledge";
import { cn } from "@/lib/utils";

/** Where an insight learned before versions were recorded is filed. */
const UNVERSIONED = "Learned before versions were recorded";

/**
 * What this Set has learned, by the version it was learned at.
 *
 * An insight written in a Set's conversation belongs to that Set and reaches its
 * conversations only, so this is its home: grouped under the version the
 * conversation was about, **waiting** ones with Add and Dismiss (they are not
 * evidence until a person adds one), **added** ones with Take back. Beneath it the
 * general knowledge that applies to this bean and grinder, marked as general and
 * linking to the Knowledge page, which holds general knowledge only.
 *
 * Renders nothing when there is nothing — an empty card on every Set would be
 * noise on the page people look at most. One list serves the page and the chat:
 * the server's own selection, so what is shown here as added or general is what
 * this Set's conversations are told.
 */
export function SetInsights({
  setId,
  versionLabels,
}: {
  setId: number;
  /** This Set's version names, newest first: the order the groups are shown in. */
  versionLabels: string[];
}) {
  const insights = useKnowledgeInsights({ set_id: setId });
  const items = insights.data?.items ?? [];
  if (items.length === 0) return null;

  const own = items.filter((item) => !item.general);
  const general = items.filter((item) => item.general);
  const groups = new Map<string, KnowledgeInsight[]>();
  for (const item of own) {
    const key = item.set_version_label ?? UNVERSIONED;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  const order = [
    ...versionLabels.filter((label) => groups.has(label)),
    ...[...groups.keys()].filter(
      (label) => label !== UNVERSIONED && !versionLabels.includes(label),
    ),
    ...(groups.has(UNVERSIONED) ? [UNVERSIONED] : []),
  ];

  return (
    <SectionCard
      title="What this Set has learned"
      description="Insights from this Set's conversations reach this Set only. Added ones are told to its later conversations; a waiting one is not evidence until you add it."
    >
      <div className="space-y-4" data-testid="set-insights">
        {order.map((label) => (
          <section key={label} data-testid="set-insights-version" data-version={label}>
            <h3 className="mb-1.5 font-medium text-sm">
              {label === UNVERSIONED ? UNVERSIONED : `Learned at ${label}`}
            </h3>
            <ul className="space-y-2">
              {(groups.get(label) ?? []).map((item) => (
                <OwnInsight key={item.id} insight={item} />
              ))}
            </ul>
          </section>
        ))}

        {general.length > 0 ? (
          <section data-testid="general-insights">
            <h3 className="mb-1 font-medium text-sm">General knowledge that applies</h3>
            <p className="mb-1.5 text-muted-foreground text-xs">
              Confirmed on the{" "}
              <Link to="/knowledge?tab=insights" className="underline underline-offset-2">
                Knowledge page
              </Link>{" "}
              for any Set whose bean, grinder and roast match; this Set's conversations are told
              them too.
            </p>
            <ul className="space-y-2">
              {general.map((item) => (
                <li
                  key={item.id}
                  data-testid="general-insight"
                  className="rounded-lg border border-border p-3"
                >
                  <div className="flex flex-wrap items-baseline gap-1.5">
                    <Badge variant="secondary">general</Badge>
                    <Badge variant="outline">{scopeLabel(item)}</Badge>
                  </div>
                  <p className="mt-1.5 text-sm">{item.text}</p>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </SectionCard>
  );
}

function OwnInsight({ insight }: { insight: KnowledgeInsight }) {
  const answer = useAnswerInsight();
  const evidence = evidenceShots(insight);
  return (
    <li
      data-testid="own-insight"
      data-insight={insight.id}
      data-confirmed={insight.confirmed}
      className={cn(
        "rounded-lg border p-3",
        insight.confirmed ? "border-border" : "border-dashed border-border opacity-90",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-1.5">
          <Badge variant={insight.confirmed ? "secondary" : "outline"}>
            {insight.confirmed ? "added" : "waiting"}
          </Badge>
          {insight.confirmed ? null : (
            <span className="text-muted-foreground text-xs">not evidence until you add it</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {insight.confirmed ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={answer.isPending}
              onClick={() => answer.mutate({ id: insight.id, answer: "take_back" })}
            >
              <Undo2 className="size-3.5" aria-hidden="true" />
              Take back
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                disabled={answer.isPending}
                onClick={() => answer.mutate({ id: insight.id, answer: "add" })}
              >
                <Check className="size-3.5" aria-hidden="true" />
                Add
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={answer.isPending}
                onClick={() => answer.mutate({ id: insight.id, answer: "dismiss" })}
              >
                <X className="size-3.5" aria-hidden="true" />
                Dismiss
              </Button>
            </>
          )}
        </div>
      </div>
      <p className="mt-1.5 text-sm">{insight.text}</p>
      {evidence.length > 0 ? (
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-muted-foreground text-xs">
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
    </li>
  );
}
