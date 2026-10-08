import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Quote, Sparkles } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import type { ReviewBlock, ReviewClaim, ShotCheck, ShotReview } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { entryLine, reviewTone, TONE_CLASS } from "@/components/shots/badgeParts";
import { ClaimItem, type TierLook } from "@/components/shots/ReviewClaims";
import { Button } from "@/components/ui/button";
import { useAnswerClaim, useRunReview } from "@/hooks/useReview";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import type { ClaimSpanControls } from "@/lib/claimSpan";
import { invalidateShots } from "@/lib/invalidate";
import { attempt } from "@/lib/mutations";
import { formatTime } from "@/lib/shots";
import { cn } from "@/lib/utils";

/** What the button does, said once before anybody presses it. */
export const REVIEW_EXPLAINED =
  "A model reads this shot's data, without your judgement, and writes claims about it: where in the curve something went wrong, with the numbers that say so. Every claim is kept unless you reject it, and the chat is told the ones you kept. It does not guess how the cup tasted, and it changes nothing else.";

/** How often a box waiting on a running review re-reads the shot. */
const RUNNING_REFRESH_MS = 5000;

/** The claims of the review in force: what a Curve check line links to. */
export function inForceClaims(
  reviews: ShotReview[] | undefined,
  inForceId: number | null | undefined,
): ReviewClaim[] | undefined {
  const found = (reviews ?? []).find((row) =>
    inForceId != null ? row.id === inForceId : row.status === "ok",
  );
  return found?.claims;
}

/** The colour of an expectation's tier: the one a failed result is drawn in. */
function tierLook(check: ShotCheck | undefined): TierLook {
  if (!check?.tier) return null;
  if (check.tier === "critical") return "red";
  if (check.tier === "important") return "amber";
  return "grey";
}

/**
 * A model's review of this shot, the claims it makes, and the button that asks for one.
 *
 * The box says what the model wrote and nothing else: the deterministic checks are the Curve
 * check box's. Claims point into the curve, so hovering or focusing one marks its span on the
 * chart and pressing it pins the mark. The states are read from the rows and the shot's `review`
 * block, never from the mutation, so a review started in another tab shows up here too:
 *
 * - none yet: the button and one line saying what it does;
 * - running: the newest row is `running`;
 * - failed: the newest row failed or was interrupted, its error and the button;
 * - reviewed: the verdict, the model's summary, the free-text expectations it answered, the
 *   prediction stance, the claims with Reject on each (and the rejected ones behind "N rejected ·
 *   show", each with Restore), the rules and excerpts it cited, the model and the time, and
 *   Review again.
 *
 * **The review in force** is the newest finished one (`review.in_force_id`), whatever has been
 * started since: while a new one runs or after one failed, its results stay below the state, and
 * its claims are answered through that id, never through `review.review_id` (the newest attempt,
 * which answers 409 for a claim). A new review replaces it only when it finishes.
 *
 * Review again asks once, inline, that it replaces the current review; the old one stays stored.
 */
export function ReviewBox({
  shotId,
  reviews,
  review,
  checks,
  span,
  id,
  open,
  onOpenChange,
}: {
  shotId: number;
  /** Every review of the shot, newest first, as the shot detail carries them. */
  reviews: ShotReview[];
  /** The shot's `review` block: which review is in force, and whether the shot can be reviewed. */
  review: ReviewBlock | undefined;
  /** The shot's checks, for the colour of the tier each free-text expectation sits in. */
  checks?: ShotCheck[];
  /** What hovering and pressing a claim does to the chart. */
  span: ClaimSpanControls;
  /** The element id the page links to (`#review`); the open row has none. */
  id?: string;
  /** When the box can be folded: whether it is open, and the way to change that. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const run = useRunReview();
  const answer = useAnswerClaim();
  const queryClient = useQueryClient();
  const once = useSingleFlight();
  const startOnce = useSingleFlight();
  const [asking, setAsking] = useState(false);
  const askId = useId();

  const latest = reviews[0];
  const inForce =
    reviews.find((row) => row.id === review?.in_force_id) ??
    reviews.find((row) => row.status === "ok");
  const running = latest?.status === "running";
  const failed = latest !== undefined && latest.status !== "ok" && latest.status !== "running";
  const reviewable = review?.state !== "not_reviewable";
  const busy = run.isPending || running;
  const claims = [...(inForce?.claims ?? [])].sort((a, b) => a.position - b.position);

  // The event stream says when a running review moves, but the bus is lossy:
  // while one is running the box also re-reads the shot now and then.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(
      () => void invalidateShots(queryClient, String(shotId)),
      RUNNING_REFRESH_MS,
    );
    return () => window.clearInterval(timer);
  }, [running, shotId, queryClient]);

  const start = () =>
    startOnce((release) => {
      setAsking(false);
      void attempt(() => run.mutateAsync({ shotId })).finally(release);
    });
  // A review in force is not set aside by a stray press.
  const reviewAgain = () => (inForce ? setAsking(true) : start());

  const button = (label: string, variant: "default" | "outline") => (
    <Button
      size="sm"
      variant={variant}
      // Not `disabled`: a second press of a double click must not drop focus to the page.
      aria-disabled={busy}
      className="aria-disabled:opacity-50"
      data-testid="run-review"
      aria-controls={inForce ? askId : undefined}
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        if (busy) return;
        reviewAgain();
      }}
    >
      <Sparkles className={cn("size-3.5", busy ? "animate-pulse" : undefined)} aria-hidden="true" />
      {busy ? "Reviewing…" : label}
    </Button>
  );

  // Always rendered and toggled with `hidden`, so `aria-controls` resolves.
  const askNode = (
    <div
      id={askId}
      hidden={!asking}
      className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 p-3 text-sm"
      data-testid="review-again-ask"
    >
      <p>This replaces the current review. The earlier one stays stored.</p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          data-testid="review-again-confirm"
          aria-disabled={busy}
          className="aria-disabled:opacity-50"
          onClick={() => {
            if (busy) return;
            start();
          }}
        >
          Review again
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid="review-again-cancel"
          onClick={() => setAsking(false)}
        >
          Keep it
        </Button>
      </div>
    </div>
  );

  return (
    <SectionCard
      id={id}
      className="scroll-mt-20"
      title="Review"
      collapsible={onOpenChange !== undefined}
      open={open}
      onOpenChange={onOpenChange}
      description={inForce || failed || running ? REVIEW_EXPLAINED : undefined}
    >
      <div data-testid="review-box" className="space-y-4">
        {latest === undefined && reviewable ? (
          <div className="space-y-3" data-testid="review-empty">
            <p className="text-muted-foreground text-sm">{REVIEW_EXPLAINED}</p>
            {button("Review this shot", "default")}
          </div>
        ) : null}

        {!reviewable && latest === undefined ? (
          <p className="text-muted-foreground text-sm" data-testid="review-unreviewable">
            A discarded shot is not reviewed.
          </p>
        ) : null}

        {running ? (
          <p className="text-muted-foreground text-sm" data-testid="review-running" role="status">
            Reviewing this shot since {formatTime(latest.created_at)}
            {latest.model ? ` on ${latest.model}` : ""}.
            {inForce ? " The earlier review stays below until this one finishes." : ""}
          </p>
        ) : null}

        {failed ? (
          <div className="space-y-2">
            <FailedReview review={latest} />
            {reviewable && inForce ? askNode : null}
            {reviewable ? button("Review again", "default") : null}
          </div>
        ) : null}

        {inForce ? (
          <Reviewed
            review={inForce}
            claims={claims}
            checks={checks ?? []}
            span={span}
            busy={answer.isPending}
            block={review}
            onRestore={(claim) =>
              once((release) =>
                answer.mutate(
                  { reviewId: inForce.id, claimId: claim.id, status: "confirmed" },
                  { onSettled: release },
                ),
              )
            }
            onReject={(claim) =>
              once((release) =>
                answer.mutate(
                  { reviewId: inForce.id, claimId: claim.id, status: "rejected" },
                  { onSettled: release },
                ),
              )
            }
          />
        ) : null}

        {inForce && !failed && reviewable ? (
          <div className="space-y-2">
            {askNode}
            {button("Review again", "outline")}
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}

function Reviewed({
  review,
  claims,
  checks,
  span,
  busy,
  block,
  onRestore,
  onReject,
}: {
  review: ShotReview;
  claims: ReviewClaim[];
  checks: ShotCheck[];
  span: ClaimSpanControls;
  busy: boolean;
  block: ReviewBlock | undefined;
  onRestore: (claim: ReviewClaim) => void;
  onReject: (claim: ReviewClaim) => void;
}) {
  const [showRejected, setShowRejected] = useState(false);
  const rejectedId = useId();
  const rules = (review.rules_used ?? []) as string[];
  const excerpts = (review.excerpts_used ?? []) as string[];
  const kept = claims.filter((claim) => claim.status !== "rejected");
  const rejected = claims.filter((claim) => claim.status === "rejected");
  const freeText = kept.filter((claim) => claim.kind === "free_text");
  const prediction = kept.filter((claim) => claim.kind === "prediction");
  const observations = kept.filter((claim) => claim.kind === "claim");
  const checkOf = (claim: ReviewClaim) =>
    checks.find((check) => check.expectation_id === claim.expectation_id);

  const item = (claim: ReviewClaim) => (
    <ClaimItem
      key={claim.id}
      claim={claim}
      reviewId={review.id}
      controls={span}
      busy={busy}
      onRestore={onRestore}
      onReject={onReject}
      tier={claim.kind === "free_text" ? tierLook(checkOf(claim)) : undefined}
      expectation={claim.kind === "free_text" ? checkOf(claim)?.sentence : undefined}
    />
  );

  return (
    <div className="space-y-4" data-testid="review-reviewed">
      <VerdictLine block={block} />

      {review.summary ? (
        <p className="text-sm" data-testid="review-summary-block">
          <span className="text-muted-foreground">The model's summary: </span>
          <span className="font-medium" data-testid="review-summary">
            {review.summary}
          </span>
        </p>
      ) : null}

      {freeText.length > 0 ? (
        <div data-testid="review-expectations">
          <h4 className="mb-1.5 font-medium text-sm">Expectations it checked</h4>
          <ul className="space-y-2">{freeText.map(item)}</ul>
        </div>
      ) : null}

      {prediction.length > 0 ? (
        <div data-testid="review-prediction">
          <h4 className="mb-1.5 font-medium text-sm" data-testid="prediction-heading">
            The version's prediction
          </h4>
          <ul className="space-y-2">{prediction.map(item)}</ul>
        </div>
      ) : null}

      <div data-testid="review-claims">
        <h4 className="mb-1.5 font-medium text-sm">What it found</h4>
        {observations.length > 0 ? (
          <ul className="space-y-2">{observations.map(item)}</ul>
        ) : (
          <p className="text-muted-foreground text-sm" data-testid="claims-none">
            {rejected.some((claim) => claim.kind === "claim")
              ? "Every claim about this shot was rejected."
              : "It made no claim about this shot."}
          </p>
        )}
      </div>

      {rejected.length > 0 ? (
        <div data-testid="review-rejected">
          <button
            type="button"
            className="text-muted-foreground text-sm underline underline-offset-2 hover:text-foreground"
            aria-expanded={showRejected}
            aria-controls={rejectedId}
            data-testid="review-rejected-toggle"
            onClick={() => setShowRejected((value) => !value)}
          >
            {rejected.length} rejected · {showRejected ? "hide" : "show"}
          </button>
          <ul id={rejectedId} hidden={!showRejected} className="mt-2 space-y-2">
            {rejected.map(item)}
          </ul>
        </div>
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
        {review.model ? ` by ${review.model}` : ""}. A model's review of this shot's data, not a
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

/** What a verdict means, in a sentence: why the badge says what it says. */
const VERDICT_WORDS = {
  no_faults: "the model found no fault; the profile has no signature in force to hold the shot to",
  as_intended: "the model found no fault against the profile's signature in force",
} as const;

/**
 * The box's first line: the Review badge's verdict, in its words and colour, and why.
 *
 * Built by code from the served `review` block, the same inputs and the same tone function as the
 * Review column's badge, so the box can never look as if it disagrees with the badge: a "No
 * faults" shot says so here, whatever the model's own summary goes on to say (that comes after
 * it, labelled as the model's words). Faults are listed with their sentences.
 */
function VerdictLine({ block }: { block: ReviewBlock | undefined }) {
  if (!block || block.state === "unreviewed" || block.state === "not_reviewable") return null;
  const tone = reviewTone(block);
  const verdict = block.verdict;
  const why =
    block.state === "failed"
      ? (block.reason ?? "")
      : block.state === "running"
        ? "a new review is running; the earlier one stays below until it finishes"
        : verdict === "no_faults" || verdict === "as_intended"
          ? VERDICT_WORDS[verdict]
          : "";
  return (
    <div className="space-y-1" data-testid="review-verdict" data-state={block.state}>
      <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span
          className={cn(
            "inline-flex max-w-full items-center rounded-full border px-2 py-0.5 font-medium text-xs",
            TONE_CLASS[tone][block.state === "running" ? "outlined" : "filled"],
          )}
          data-testid="review-verdict-badge"
          data-tone={tone}
        >
          <span className="min-w-0 truncate">{block.badge}</span>
        </span>
        {why ? <span data-testid="review-verdict-why">{why}</span> : null}
      </p>
      {block.entries.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm" data-testid="review-verdict-entries">
          {block.entries.map((entry) => (
            <li key={`${entry.claim_id ?? entry.expectation_id}-${entry.fault}`}>
              {entryLine(entry)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
