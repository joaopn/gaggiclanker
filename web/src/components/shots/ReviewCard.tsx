import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Check, Quote, Sparkles } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ReadingBlock, ReviewClaim, ShotCheck, ShotReview, ShotWarning } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { ClaimItem, type TierLook } from "@/components/shots/ReadingClaims";
import { badgeView, TONE_CLASS, warningLine } from "@/components/shots/ReviewBadge";
import { Button } from "@/components/ui/button";
import { useAnswerClaim, useConfirmAllClaims, useRunReview } from "@/hooks/useReview";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import type { ClaimSpanControls } from "@/lib/claimSpan";
import { invalidateShots } from "@/lib/invalidate";
import { attempt } from "@/lib/mutations";
import { formatTime } from "@/lib/shots";
import { cn } from "@/lib/utils";

/** What the button does, said once before anybody presses it. */
export const REVIEW_EXPLAINED =
  "A model reads this shot's data, without your judgement, and writes claims about it: where in the curve something went wrong, with the numbers that say so. You confirm or reject each claim, and only a confirmed one reaches the chat. It does not guess how the cup tasted, and it changes nothing else.";

/** How often a card waiting on a running reading re-reads the shot. */
const RUNNING_REFRESH_MS = 5000;

/** The colour of an expectation's tier: the one a failed result is drawn in. */
function tierLook(check: ShotCheck | undefined): TierLook {
  if (!check?.tier) return null;
  if (check.tier === "critical") return "red";
  if (check.tier === "important") return "amber";
  return "grey";
}

/**
 * A model's reading of this shot, the claims it makes, and the button that asks for one.
 *
 * Placed under the Curves card because the claims point into the curve: hovering or focusing a
 * claim marks its span there, and pressing it pins the mark. The states are read from the rows
 * and the shot's `reading` block, never from the mutation, so a reading started in another tab
 * shows up here too:
 *
 * - none yet: the button and one line saying what it does;
 * - running: the newest row is `running`;
 * - failed: the newest row failed or was interrupted, its error and the button;
 * - read: the summary, the free-text expectations it checked, the prediction, the claims with
 *   Confirm and Reject on each and Confirm all, the rules and excerpts it cited, the model and
 *   the time, and Read again.
 *
 * **The reading in force** is the newest finished one (`reading.in_force_id`), whatever has been
 * started since: while a re-read runs or after one failed, its results stay below the state, and
 * its claims are answered through that id, never through `reading.review_id` (the newest attempt,
 * which answers 409 for a claim). A re-read replaces it only when it finishes.
 *
 * Read again from a reading with confirmed claims asks once, inline, and says how many it sets
 * aside; the old reading stays stored. The prediction's stance is held back until the shot has a
 * decision, like the prediction itself: it would give the prediction away.
 */
export function ReviewCard({
  shotId,
  reviews,
  reading,
  checks,
  span,
  decision,
  hasPrediction,
  badge,
  warnings,
}: {
  shotId: number;
  /** Every review of the shot, newest first, as the shot detail carries them. */
  reviews: ShotReview[];
  /** The shot's `reading` block: which reading is in force, and whether the shot can be read. */
  reading: ReadingBlock | undefined;
  /** The shot's checks, for the colour of the tier each free-text expectation sits in. */
  checks?: ShotCheck[];
  /** What hovering and pressing a claim does to the chart. */
  span: ClaimSpanControls;
  /** The shot's decision; the prediction is shown only once it has one. */
  decision?: string | null;
  /** Whether the shot's version predicted anything. */
  hasPrediction?: boolean;
  /** The badge's served text and entries: the card's first line says the same thing. */
  badge?: string | null;
  warnings?: ShotWarning[];
}) {
  const run = useRunReview();
  const answer = useAnswerClaim();
  const confirmAll = useConfirmAllClaims();
  const queryClient = useQueryClient();
  const once = useSingleFlight();
  const startOnce = useSingleFlight();
  const [asking, setAsking] = useState(false);
  // The stance is held back until the shot has a decision, like the version's prediction; "Show"
  // is the deliberate way past that. The reveal belongs to one reading of one shot: the page keys
  // this card by shot, and a new reading in force is a new secret.
  const [revealedFor, setRevealedFor] = useState<number | null>(null);
  const askId = useId();

  const latest = reviews[0];
  const inForce =
    reviews.find((review) => review.id === reading?.in_force_id) ??
    reviews.find((review) => review.status === "ok");
  const running = latest?.status === "running";
  const failed = latest !== undefined && latest.status !== "ok" && latest.status !== "running";
  const readable = reading?.state !== "not_readable";
  const busy = run.isPending || running;
  const claims = [...(inForce?.claims ?? [])].sort((a, b) => a.position - b.position);
  const confirmedCount = claims.filter((claim) => claim.status === "confirmed").length;
  const stanceHeld =
    Boolean(hasPrediction) && decision == null && revealedFor !== (inForce?.id ?? null);
  // What Confirm all will confirm: not a stance nobody has been shown.
  const waitingCount = claims.filter(
    (claim) => claim.status === "proposed" && !(stanceHeld && claim.kind === "prediction"),
  ).length;
  const answering = answer.isPending || confirmAll.isPending;

  // The event stream says when a running reading moves, but the bus is lossy:
  // while one is running the card also re-reads the shot now and then.
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
  // A reading with confirmed claims is not set aside by a stray press.
  const readAgain = () => (confirmedCount > 0 ? setAsking(true) : start());

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
        readAgain();
      }}
    >
      <Sparkles className={cn("size-3.5", busy ? "animate-pulse" : undefined)} aria-hidden="true" />
      {busy ? "Reading…" : label}
    </Button>
  );

  // Always rendered and toggled with `hidden`, so `aria-controls` resolves.
  const askNode = (
    <div
      id={askId}
      hidden={!asking}
      className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 p-3 text-sm"
      data-testid="read-again-ask"
    >
      <p>
        This sets aside {confirmedCount} confirmed {confirmedCount === 1 ? "claim" : "claims"}. The
        earlier reading stays stored.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          data-testid="read-again-confirm"
          aria-disabled={busy}
          className="aria-disabled:opacity-50"
          onClick={() => {
            if (busy) return;
            start();
          }}
        >
          Read again
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid="read-again-cancel"
          onClick={() => setAsking(false)}
        >
          Keep it
        </Button>
      </div>
    </div>
  );

  return (
    <SectionCard
      title="Reading"
      description={inForce || failed || running ? REVIEW_EXPLAINED : undefined}
    >
      <div data-testid="review-card" className="space-y-4">
        {latest === undefined && readable ? (
          <div className="space-y-3" data-testid="review-empty">
            <p className="text-muted-foreground text-sm">{REVIEW_EXPLAINED}</p>
            {button("Read this shot", "default")}
          </div>
        ) : null}

        {!readable && latest === undefined ? (
          <p className="text-muted-foreground text-sm" data-testid="review-unreadable">
            A discarded shot is not read.
          </p>
        ) : null}

        {running ? (
          <p className="text-muted-foreground text-sm" data-testid="review-running" role="status">
            Reading this shot since {formatTime(latest.created_at)}
            {latest.model ? ` on ${latest.model}` : ""}.
            {inForce ? " The earlier reading stays below until this one finishes." : ""}
          </p>
        ) : null}

        {failed ? (
          <div className="space-y-2">
            <FailedReview review={latest} />
            {readable && inForce ? askNode : null}
            {readable ? button("Read again", "default") : null}
          </div>
        ) : null}

        {inForce ? (
          <Reading
            review={inForce}
            claims={claims}
            checks={checks ?? []}
            span={span}
            busy={answering}
            predictionShown={!stanceHeld}
            onReveal={() => setRevealedFor(inForce.id)}
            verdict={{ badge, warnings, reading }}
            waiting={waitingCount}
            onConfirm={(claim) =>
              once((release) =>
                answer.mutate(
                  { reviewId: inForce.id, claimId: claim.id, status: "confirmed" },
                  { onSettled: release },
                ),
              )
            }
            onReject={(claim, reason) =>
              once((release) =>
                answer.mutate(
                  { reviewId: inForce.id, claimId: claim.id, status: "rejected", reason },
                  { onSettled: release },
                ),
              )
            }
            onConfirmAll={() =>
              once((release) =>
                confirmAll.mutate(
                  {
                    reviewId: inForce.id,
                    // A stance nobody has been shown is not confirmed for them.
                    ...(stanceHeld ? { exceptKinds: ["prediction"] } : {}),
                  },
                  { onSettled: release },
                ),
              )
            }
          />
        ) : null}

        {inForce && !failed && readable ? (
          <div className="space-y-2">
            {askNode}
            {button("Read again", "outline")}
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}

function Reading({
  review,
  claims,
  checks,
  span,
  busy,
  predictionShown,
  onReveal,
  verdict,
  waiting,
  onConfirm,
  onReject,
  onConfirmAll,
}: {
  review: ShotReview;
  claims: ReviewClaim[];
  checks: ShotCheck[];
  span: ClaimSpanControls;
  busy: boolean;
  predictionShown: boolean;
  onReveal: () => void;
  verdict: { badge?: string | null; warnings?: ShotWarning[]; reading: ReadingBlock | undefined };
  waiting: number;
  onConfirm: (claim: ReviewClaim) => void;
  onReject: (claim: ReviewClaim, reason: string) => void;
  onConfirmAll: () => void;
}) {
  // The Show button is replaced by what it revealed, so focus has to land on the section or it
  // falls to the body. Only after a reveal: a stance shown from the start takes no focus.
  const headingRef = useRef<HTMLHeadingElement>(null);
  const wasHeld = useRef(!predictionShown);
  useEffect(() => {
    if (predictionShown && wasHeld.current) headingRef.current?.focus();
    wasHeld.current = !predictionShown;
  }, [predictionShown]);
  const rules = (review.rules_used ?? []) as string[];
  const excerpts = (review.excerpts_used ?? []) as string[];
  const freeText = claims.filter((claim) => claim.kind === "free_text");
  const prediction = claims.filter((claim) => claim.kind === "prediction");
  const observations = claims.filter((claim) => claim.kind === "claim");
  const checkOf = (claim: ReviewClaim) =>
    checks.find((check) => check.expectation_id === claim.expectation_id);

  const item = (claim: ReviewClaim, extra: { tier?: TierLook; expectation?: string } = {}) => (
    <ClaimItem
      key={claim.id}
      claim={claim}
      reviewId={review.id}
      controls={span}
      busy={busy}
      onConfirm={onConfirm}
      onReject={onReject}
      {...extra}
    />
  );

  return (
    <div className="space-y-4" data-testid="review-reading">
      <VerdictLine
        {...verdict}
        waiting={waiting}
        stanceBehindShow={!predictionShown && prediction.some((c) => c.status === "proposed")}
      />

      {review.summary ? (
        <p className="text-sm" data-testid="review-summary-block">
          <span className="text-muted-foreground">The model's summary: </span>
          <span className="font-medium" data-testid="review-summary">
            {review.summary}
          </span>
        </p>
      ) : null}

      {freeText.length > 0 ? (
        <div data-testid="reading-expectations">
          <h4 className="mb-1.5 font-medium text-sm">Expectations it checked</h4>
          <ul className="space-y-2">
            {freeText.map((claim) =>
              item(claim, {
                tier: tierLook(checkOf(claim)),
                expectation: checkOf(claim)?.sentence,
              }),
            )}
          </ul>
        </div>
      ) : null}

      {prediction.length > 0 ? (
        <div data-testid="reading-prediction">
          <h4
            ref={headingRef}
            tabIndex={-1}
            className="mb-1.5 font-medium text-sm outline-none"
            data-testid="prediction-heading"
          >
            The version's prediction
          </h4>
          {predictionShown ? (
            <ul className="space-y-2">{prediction.map((claim) => item(claim))}</ul>
          ) : (
            <div className="space-y-2" data-testid="prediction-hidden">
              <p className="text-muted-foreground text-sm">
                This shot was compared with its version's prediction. It shows once you have
                recorded a decision, like the prediction itself, and Confirm all leaves it alone
                until then.
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="prediction-show"
                onClick={onReveal}
              >
                Show how the shot moved against it
              </Button>
            </div>
          )}
        </div>
      ) : null}

      <div data-testid="reading-claims">
        <h4 className="mb-1.5 font-medium text-sm">What it found</h4>
        {observations.length > 0 ? (
          <ul className="space-y-2">{observations.map((claim) => item(claim))}</ul>
        ) : (
          <p className="text-muted-foreground text-sm" data-testid="claims-none">
            It made no claim about this shot.
          </p>
        )}
        {waiting > 0 ? (
          <div className="mt-2">
            <Button
              type="button"
              size="sm"
              aria-disabled={busy}
              className="aria-disabled:opacity-50"
              data-testid="claims-confirm-all"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                if (busy) return;
                onConfirmAll();
              }}
            >
              <Check className="size-3.5" aria-hidden="true" />
              Confirm all ({waiting})
            </Button>
          </div>
        ) : null}
      </div>

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
          ? "Interrupted — the process stopped before this reading finished"
          : "That reading did not complete"}
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
  no_signature:
    "read without a confirmed signature, so nothing was checked against the profile's intent",
  as_intended: "every check of the confirmed signature held",
} as const;

/**
 * The card's first line: the badge's verdict, in its words and colour, and why.
 *
 * Built by code from the served `reading` block and the badge's entries, the same inputs and the
 * same function (`badgeView`) as the badge, so the card can never look as if it disagrees with
 * the badge: a "No signature" shot says so here, whatever the model's own summary goes on to say
 * (that comes after it, labelled as the model's words). Failures are listed with their sentences.
 */
function VerdictLine({
  badge,
  warnings,
  reading,
  waiting,
  stanceBehindShow,
}: {
  badge?: string | null;
  warnings?: ShotWarning[];
  reading: ReadingBlock | undefined;
  /** The claims that wait for an answer and are on screen: not a stance held back behind Show. */
  waiting: number;
  /** A proposed stance that waits behind Show, said apart so nothing hidden is counted. */
  stanceBehindShow: boolean;
}) {
  const view = badgeView(badge, warnings, reading);
  if (!view) return null;
  const state = reading?.state;
  const verdict = reading?.verdict;
  const why =
    state === "failed"
      ? (reading?.reason ?? "")
      : state === "running"
        ? "a new reading is running; the earlier one stays below until it finishes"
        : verdict === "no_signature" || verdict === "as_intended"
          ? VERDICT_WORDS[verdict]
          : verdict === "entries" && !(warnings && warnings.length > 0)
            ? "its failures are still loading"
            : "";
  return (
    <div className="space-y-1" data-testid="reading-verdict" data-state={state}>
      <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span
          className={cn(
            "inline-flex max-w-full items-center rounded-full border px-2 py-0.5 font-medium text-xs",
            TONE_CLASS[view.tone][view.filled ? "filled" : "outlined"],
          )}
          data-testid="reading-verdict-badge"
          data-tone={view.tone}
          data-filled={view.filled ? "yes" : "no"}
        >
          <span className="min-w-0 truncate">{view.text}</span>
        </span>
        {why ? <span data-testid="reading-verdict-why">{why}</span> : null}
        {state === "read" && (waiting > 0 || stanceBehindShow) ? (
          <span className="text-muted-foreground text-xs" data-testid="reading-verdict-waiting">
            {waiting > 0
              ? `${waiting} ${waiting === 1 ? "claim waits" : "claims wait"} for your answer`
              : null}
            {waiting > 0 && stanceBehindShow ? ", and " : null}
            {stanceBehindShow ? "the prediction stance waits behind Show" : null}
          </span>
        ) : null}
      </p>
      {verdict === "entries" && warnings && warnings.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm" data-testid="reading-verdict-entries">
          {warnings.map((warning, index) => (
            <li
              // biome-ignore lint/suspicious/noArrayIndexKey: the server's order, never reordered here
              key={index}
            >
              {warningLine(warning)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
