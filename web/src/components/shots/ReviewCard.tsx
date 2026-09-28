import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Quote, Sparkles } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";
import type { ShotReview } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { useRunReview } from "@/hooks/useReview";
import { invalidateShots } from "@/lib/invalidate";
import { attempt } from "@/lib/mutations";
import { formatTime } from "@/lib/shots";
import { cn } from "@/lib/utils";

/** What the button does, said once before anybody presses it. */
export const REVIEW_EXPLAINED =
  "A model reads this shot's data, without your judgement, and writes what it expects the cup to taste like, a description and a one-line summary. It changes nothing else.";

/** How often a card waiting on a running review re-reads the shot. */
const RUNNING_REFRESH_MS = 5000;

/**
 * A model's reading of this shot, and the button that asks for one.
 *
 * A review writes three things about the shot and nothing else: a blind taste
 * prediction, a description and a one-sentence summary. It is shown here and
 * nowhere else in the interface (never in the shots table or on the Set page),
 * and the chat reads it as shot information.
 *
 * Four states, all read from the rows rather than from the mutation, so a
 * review started in another tab shows up here too:
 *
 * - none yet: the button and one line saying what it does;
 * - running: the newest row is `running`;
 * - read: the newest finished review — summary first, the taste prediction
 *   beside the person's own balance, the description, the rules and excerpts
 *   it cited, the model and the time, and Review again;
 * - failed: the newest row failed or was interrupted — its stored error and
 *   the button, with the last finished reading still below it if there is one.
 */
export function ReviewCard({
  shotId,
  reviews,
  balance,
}: {
  shotId: number;
  /** Every review of the shot, newest first, as the shot detail carries them. */
  reviews: ShotReview[];
  /** The person's own balance from their judgement, to set beside the prediction. */
  balance: string | null | undefined;
}) {
  const run = useRunReview();
  const queryClient = useQueryClient();
  const latest = reviews[0];
  const reading = reviews.find((review) => review.status === "ok");
  const running = latest?.status === "running";
  const failed = latest !== undefined && latest.status !== "ok" && latest.status !== "running";
  const busy = run.isPending || running;

  // The event stream says when a running review moves, but the bus is lossy:
  // while one is running the card also re-reads the shot now and then.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(
      () => void invalidateShots(queryClient, String(shotId)),
      RUNNING_REFRESH_MS,
    );
    return () => window.clearInterval(timer);
  }, [running, shotId, queryClient]);

  return (
    <SectionCard
      title="Review"
      description={reading || failed || running ? REVIEW_EXPLAINED : undefined}
      actions={
        latest !== undefined ? (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            data-testid="run-review"
            onClick={() => void attempt(() => run.mutateAsync({ shotId }))}
          >
            <Sparkles
              className={cn("size-3.5", busy ? "animate-pulse" : undefined)}
              aria-hidden="true"
            />
            {busy ? "Reviewing…" : "Review again"}
          </Button>
        ) : null
      }
    >
      <div data-testid="review-card" className="space-y-4">
        {latest === undefined ? (
          <div className="space-y-3" data-testid="review-empty">
            <p className="text-muted-foreground text-sm">{REVIEW_EXPLAINED}</p>
            <Button
              size="sm"
              disabled={busy}
              data-testid="run-review"
              onClick={() => void attempt(() => run.mutateAsync({ shotId }))}
            >
              <Sparkles
                className={cn("size-3.5", busy ? "animate-pulse" : undefined)}
                aria-hidden="true"
              />
              {busy ? "Reviewing…" : "Review"}
            </Button>
          </div>
        ) : null}

        {running ? (
          <p className="text-muted-foreground text-sm" data-testid="review-running" role="status">
            Reading this shot since {formatTime(latest.created_at)}
            {latest.model ? ` on ${latest.model}` : ""}.
          </p>
        ) : null}

        {failed ? <FailedReview review={latest} /> : null}

        {reading ? <Reading review={reading} balance={balance} /> : null}
      </div>
    </SectionCard>
  );
}

function Reading({ review, balance }: { review: ShotReview; balance: string | null | undefined }) {
  const vocab = useVocabulary();
  const balanceLabel = (value: string | null | undefined) =>
    value ? (vocab.data?.balances.find((term) => term.value === value)?.label ?? value) : null;
  const predicted = balanceLabel(review.taste_balance);
  const yours = balanceLabel(balance);
  const rules = (review.rules_used ?? []) as string[];
  const excerpts = (review.excerpts_used ?? []) as string[];

  return (
    <div className="space-y-4" data-testid="review-reading">
      {review.summary ? (
        <p className="font-medium text-sm" data-testid="review-summary">
          {review.summary}
        </p>
      ) : null}

      <dl className="grid gap-2 text-sm sm:grid-cols-2" data-testid="review-taste">
        <div className="rounded-md border border-border p-2">
          <dt className="text-muted-foreground text-xs">It predicts, without your judgement</dt>
          <dd data-testid="review-predicted">
            {predicted ?? "no balance"}
            {review.taste_body ? ` · ${review.taste_body} body` : ""}
            {review.taste_confidence ? ` · ${review.taste_confidence} confidence` : ""}
          </dd>
        </div>
        <div className="rounded-md border border-border p-2">
          <dt className="text-muted-foreground text-xs">You said</dt>
          <dd data-testid="review-yours">{yours ?? "no balance recorded yet"}</dd>
        </div>
      </dl>

      {review.description ? (
        <p className="text-sm" data-testid="review-description">
          {review.description}
        </p>
      ) : null}

      {rules.length > 0 ? (
        <div>
          <h4 className="mb-1 flex items-center gap-1.5 font-medium text-sm">
            <BookOpen className="size-3.5" aria-hidden="true" />
            Rules it leaned on
          </h4>
          {/* Every key here was checked against the rules this shot was given;
              an invented one is dropped server-side. The links are how a rule
              that misleads gets found and turned off. */}
          <ul className="flex flex-wrap gap-1.5" data-testid="review-rules">
            {rules.map((key) => (
              <li key={key}>
                <Link
                  to={`/knowledge?rule=${encodeURIComponent(key)}`}
                  className="inline-flex rounded-full border border-border px-2 py-0.5 font-mono text-xs hover:bg-accent"
                >
                  {key}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {excerpts.length > 0 ? (
        <div>
          <h4 className="mb-1 flex items-center gap-1.5 font-medium text-sm">
            <Quote className="size-3.5" aria-hidden="true" />
            Reference excerpts it quoted
          </h4>
          <ul className="flex flex-wrap gap-1.5" data-testid="review-excerpts">
            {excerpts.map((path) => (
              <li key={path}>
                <Link
                  to={`/knowledge?tab=docs&doc=${encodeURIComponent(
                    path.split("#")[0],
                  )}&chunk=${encodeURIComponent(path)}`}
                  className="inline-flex rounded-full border border-border px-2 py-0.5 font-mono text-xs hover:bg-accent"
                >
                  {path}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="text-muted-foreground text-xs" data-testid="review-provenance">
        Written {formatTime(review.finished_at ?? review.created_at)}
        {review.model ? ` by ${review.model}` : ""}. A model's reading of this shot's data, not a
        measurement.
      </p>
    </div>
  );
}

function FailedReview({ review }: { review: ShotReview }) {
  return (
    <div className="rounded-md border border-border bg-muted/50 p-3" data-testid="review-failed">
      <p className="flex items-center gap-1.5 font-medium text-sm">
        <AlertTriangle className="size-3.5 text-status-warn-text" aria-hidden="true" />
        {review.status === "interrupted"
          ? "Interrupted — the process stopped before this review finished"
          : "That review did not complete"}
      </p>
      {review.error ? (
        <p className="mt-1 font-mono text-muted-foreground text-xs" data-testid="review-error">
          {review.error}
        </p>
      ) : null}
    </div>
  );
}
