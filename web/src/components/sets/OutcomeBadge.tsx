import { Badge } from "@/components/ui/badge";
import { useVocabulary } from "@/hooks/useCatalog";
import { cn } from "@/lib/utils";

/** The badge's colour per state. Open is a question, not a warning. */
export const OUTCOME_TONE: Record<string, string> = {
  held: "border-status-good/40 bg-status-good/10 text-status-good-text",
  partly_held: "border-status-warn/40 bg-status-warn/10 text-status-warn-text",
  failed: "border-status-bad/40 bg-status-bad/10 text-status-bad-text",
  inconclusive: "border-border bg-muted text-muted-foreground",
  open: "border-border bg-background text-foreground",
  no_prediction: "border-transparent bg-transparent text-muted-foreground",
};

/** "partly_held" as "Partly held", for the moment before the vocabulary has been read. */
export function humanisedOutcome(value: string | null | undefined): string {
  const words = (value ?? "").replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * How a prediction turned out, as the one badge every screen uses.
 *
 * The Set page's outcome control, the chat's grade card and a version
 * proposal's "also records" line all say it the same way: the same words from
 * the server's vocabulary and the same colours, so a grade proposed in the chat
 * and the outcome recorded on the page cannot look like two different things.
 */
export function OutcomeBadge({
  state,
  className,
  testId,
}: {
  /** A recorded outcome, or one of the two states that are not an outcome. */
  state: string;
  className?: string;
  testId?: string;
}) {
  const vocab = useVocabulary();
  const label =
    vocab.data?.outcome_states.find((term) => term.value === state)?.label ??
    humanisedOutcome(state);
  return (
    <Badge
      variant="outline"
      className={cn("font-normal", OUTCOME_TONE[state], className)}
      data-testid={testId}
      data-state={state}
    >
      {label}
    </Badge>
  );
}
