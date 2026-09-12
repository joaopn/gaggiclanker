import { Check, Pencil, Trash2, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { KnowledgeInsight } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDeleteKnowledgeInsight, usePatchKnowledgeInsight } from "@/hooks/useKnowledge";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * One learned insight, with the three things a reader needs before believing it.
 *
 * The scope, because "two clicks finer" is meaningless without "for naturals on
 * this grinder". The evidence, as links to the shots it was drawn from — a
 * claim with nothing to check is an opinion, and confirming one without opening
 * a shot is how a model's guess becomes this archive's fact. And the
 * confirmation state, because that is the whole safety property: an unconfirmed
 * insight is never put in front of the model, and the button is the only thing
 * that changes it.
 */
export function InsightCard({
  insight,
  compact = false,
}: {
  insight: KnowledgeInsight;
  /** On the shot panel and the Set page: no editor, no delete. */
  compact?: boolean;
}) {
  const patch = usePatchKnowledgeInsight();
  const remove = useDeleteKnowledgeInsight();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(insight.text);

  // `evidence_shot_ids_json` is a JSON column, so OpenAPI types it as unknown[];
  // it is always a list of shot ids, and a row from an older build that is not
  // is skipped rather than rendered as "[object Object]".
  const evidence = ((insight.evidence_shot_ids ?? []) as unknown[]).filter(
    (value): value is number => typeof value === "number",
  );

  return (
    <li
      data-testid="insight"
      data-insight={insight.id}
      data-confirmed={insight.confirmed}
      className={cn(
        "rounded-lg border p-3",
        insight.confirmed ? "border-border" : "border-dashed border-border opacity-90",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-1.5">
          <Badge variant="outline" data-testid="insight-scope">
            {scopeLabel(insight)}
          </Badge>
          <Badge variant="secondary">{insight.source}</Badge>
          {insight.confirmed ? null : (
            <span className="text-muted-foreground text-xs">proposed, not confirmed</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant={insight.confirmed ? "ghost" : "default"}
            disabled={patch.isPending}
            data-testid="toggle-insight"
            onClick={() =>
              patch.mutate({ id: insight.id, patch: { confirmed: !insight.confirmed } })
            }
          >
            {insight.confirmed ? (
              <>
                <Undo2 className="size-3.5" aria-hidden="true" />
                Unconfirm
              </>
            ) : (
              <>
                <Check className="size-3.5" aria-hidden="true" />
                Confirm
              </>
            )}
          </Button>
          {compact ? null : (
            <>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Edit insight ${insight.id}`}
                onClick={() => {
                  setDraft(insight.text);
                  setEditing((open) => !open);
                }}
              >
                <Pencil className="size-3.5" aria-hidden="true" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={remove.isPending}
                aria-label={`Delete insight ${insight.id}`}
                data-testid="delete-insight"
                onClick={() => remove.mutate(insight.id)}
              >
                <Trash2 className="size-3.5" aria-hidden="true" />
              </Button>
            </>
          )}
        </div>
      </div>

      {editing ? (
        <form
          className="mt-2 space-y-2"
          onSubmit={async (event) => {
            event.preventDefault();
            const saved = await attempt(() =>
              patch.mutateAsync({ id: insight.id, patch: { text: draft } }),
            );
            if (saved) setEditing(false);
          }}
        >
          <textarea
            className="h-20 w-full rounded-md border border-input bg-background p-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Text of insight ${insight.id}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button type="submit" size="sm" disabled={patch.isPending}>
            Save
          </Button>
        </form>
      ) : (
        <p className="mt-1.5 text-sm">{insight.text}</p>
      )}

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

/**
 * The scope as a badge.
 *
 * Rendered here rather than taken from the server: the row carries the scope as
 * structured fields precisely so a client can show them its own way, and a
 * pre-rendered string would be one more thing to keep in step.
 */
function scopeLabel(insight: KnowledgeInsight): string {
  const scope = (insight.scope ?? {}) as Record<string, unknown>;
  const parts = Object.entries(scope)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key.replace(/_id$/, "")}=${String(value)}`);
  return parts.length ? parts.join(" · ") : "any shot";
}
