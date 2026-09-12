import { Check, X } from "lucide-react";
import type { Suggestion } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAcceptSuggestion, useRejectSuggestion } from "@/hooks/useAnalysis";
import { useVocabulary } from "@/hooks/useCatalog";
import { cn } from "@/lib/utils";

/** The four a Set version can record. Everything else is a profile edit. */
const ACTIONABLE = new Set(["grind", "dose", "yield", "temperature"]);

/**
 * One suggestion, with the two buttons that resolve it.
 *
 * Accept is only offered for the four variables a Set version can record, and
 * the rest say why rather than showing a button that answers 409 — the advice
 * is still worth reading, and "make this change on the machine yourself" is a
 * useful instruction, while a greyed-out button with no explanation is not.
 *
 * The words come from `GET /api/vocab` like every other closed vocabulary on
 * these pages: nothing in `src/` types a coffee word (web/README.md).
 */
export function SuggestionCard({ suggestion }: { suggestion: Suggestion }) {
  const accept = useAcceptSuggestion();
  const reject = useRejectSuggestion();
  const vocab = useVocabulary();

  const label = (list: "suggestion_variables" | "suggestion_units", value: string) =>
    vocab.data?.[list].find((term) => term.value === value)?.label ?? value;

  const actionable = ACTIONABLE.has(suggestion.variable);
  const open = suggestion.status === "open";
  const busy = accept.isPending || reject.isPending;

  return (
    <li
      data-testid="suggestion"
      data-variable={suggestion.variable}
      data-status={suggestion.status}
      className={cn(
        "rounded-lg border border-border p-3",
        suggestion.status === "rejected" || suggestion.status === "superseded"
          ? "opacity-60"
          : undefined,
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-medium text-sm">
            {label("suggestion_variables", suggestion.variable)} {suggestion.direction}
            {suggestion.magnitude == null
              ? ""
              : ` ${suggestion.magnitude}${
                  suggestion.unit === "none" ? "" : ` ${label("suggestion_units", suggestion.unit)}`
                }`}
          </span>
          <Badge variant="ghost" className="text-muted-foreground">
            priority {suggestion.priority}
          </Badge>
          {suggestion.confidence ? (
            <Badge variant="outline">{suggestion.confidence} confidence</Badge>
          ) : null}
        </div>
        <StatusBadge status={suggestion.status} />
      </div>

      {suggestion.reason ? <p className="mt-1.5 text-sm">{suggestion.reason}</p> : null}

      {open ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {actionable ? (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => accept.mutate(suggestion.id)}
              data-testid="accept-suggestion"
            >
              <Check className="size-3.5" aria-hidden="true" />
              Record it as a new version
            </Button>
          ) : (
            <p className="text-muted-foreground text-xs">
              This is a change to the brew profile rather than to the recipe, so there is no Set
              field to record it in. Use "Draft profile" above: it turns the advice into a new
              profile you can review, and pushes it as a new file on the machine rather than over
              the one you are brewing with.
            </p>
          )}
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => reject.mutate(suggestion.id)}
            data-testid="reject-suggestion"
          >
            <X className="size-3.5" aria-hidden="true" />
            No
          </Button>
        </div>
      ) : suggestion.resulting_set_version_id ? (
        <p className="mt-2 text-muted-foreground text-xs">
          Recorded as a new Set version (#{suggestion.resulting_set_version_id}).
        </p>
      ) : null}
    </li>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "open") return null;
  if (status === "accepted") return <Badge variant="secondary">accepted</Badge>;
  if (status === "rejected") return <Badge variant="outline">turned down</Badge>;
  // Superseded is not rejected: nobody disagreed with it, a sibling for the
  // same variable was taken instead.
  return <Badge variant="outline">overtaken</Badge>;
}
