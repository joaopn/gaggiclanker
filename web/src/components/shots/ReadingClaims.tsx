import { Check, Crosshair, X } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { ClaimEvidence, ReviewClaim, ReviewClaimStatus } from "@/api/types";
import { ReasonForm } from "@/components/signatures/ReasonForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ClaimSpanControls } from "@/lib/claimSpan";
import { cn } from "@/lib/utils";

/**
 * One statement of a reading, with the numbers the server worked out for it and the person's
 * answer to it.
 *
 * The same row serves the three kinds a reading makes: an observation (a phase or a span, a
 * fault word and a sentence), a free-text expectation's result (held or failed, in the colour of
 * its tier) and the prediction's stance. A claim is **proposed** until a person confirms or
 * rejects it; the answer can be changed, and only a confirmed claim reaches the chat. A claim
 * whose numbers do not bear it out is kept and says so.
 *
 * Hovering or focusing the span button draws its span on the chart, and pressing it pins the
 * span (a phone has no hover). The row is `tabIndex={-1}` and takes focus before an answer goes
 * out, so Confirm going away leaves a keyboard user on the claim and not on the page.
 */

export const STATUS_LABEL: Record<ReviewClaimStatus, string> = {
  proposed: "unverified",
  confirmed: "confirmed",
  rejected: "rejected",
};

function statusClass(status: ReviewClaimStatus): string {
  if (status === "confirmed")
    return "border-status-good/40 bg-status-good/10 text-status-good-text";
  if (status === "rejected") return "border-border text-muted-foreground line-through";
  return "border-status-warn/40 bg-status-warn/10 text-status-warn-text";
}

/** A measured value as a person reads it: "6.1 bar", "117.2 %". */
function measured(item: ClaimEvidence): string | null {
  if (item.value === null || item.value === undefined) return null;
  const number = String(Number(item.value.toFixed(2)));
  const unit = item.unit === "%" ? " %" : item.unit ? ` ${item.unit}` : "";
  return `${number}${unit}`;
}

/** The language words a comparison at the end of a sentence ("…, at most 6", "…, between 4 and 6"). */
const COMPARE_TAIL = /, (?:under|at most|over|at least|between) [^,]*$/;

/**
 * The sentence and the limit of one evidence item, with the limit said once.
 *
 * The server's sentence carries the language's own comparison, and for a share it carries the
 * limit the way a check words it already ("…, at most 15 % of target"), but for a plain measure
 * its wording has no unit ("…, at most 6") beside `limit_text` ("at most 6 bar"). The limit with
 * its unit is the one a person reads, so the unit-less tail is cut from the sentence and
 * `limit_text` says it; when the sentence already holds `limit_text`, nothing is added.
 */
export function evidenceWords(item: ClaimEvidence): { sentence: string; limit: string } {
  if (!item.limit_text) return { sentence: item.sentence, limit: "" };
  if (item.sentence.includes(item.limit_text)) return { sentence: item.sentence, limit: "" };
  return { sentence: item.sentence.replace(COMPARE_TAIL, ""), limit: item.limit_text };
}

/** What the evidence says: the language's sentence, the value, and the limit once. */
export function EvidenceLine({ item }: { item: ClaimEvidence }) {
  const value = measured(item);
  const { sentence, limit } = evidenceWords(item);
  return (
    <li
      className="min-w-0 break-words text-sm"
      data-testid="claim-evidence"
      data-held={item.held === null ? "none" : item.held ? "yes" : "no"}
    >
      <span className="text-muted-foreground">{sentence}</span>
      {": "}
      {value !== null ? (
        <span className="font-medium tabular-nums" data-testid="claim-evidence-value">
          {value}
        </span>
      ) : (
        <span className="text-muted-foreground" data-testid="claim-evidence-absent">
          not measured{item.absent ? ` (${item.absent})` : ""}
        </span>
      )}
      {limit ? <span className="text-muted-foreground">{`, ${limit}`}</span> : null}
      {item.held === false ? (
        <span className="text-status-warn-text"> — outside the limit</span>
      ) : null}
    </li>
  );
}

export type TierLook = "red" | "amber" | "grey" | null;

const HELD_TEXT: Record<"held" | "failed", string> = { held: "held", failed: "failed" };

export function ClaimItem({
  claim,
  reviewId,
  controls,
  busy,
  tier,
  expectation,
  onConfirm,
  onReject,
}: {
  claim: ReviewClaim;
  reviewId: number;
  controls: ClaimSpanControls;
  /** An answer is out: no second one starts. */
  busy: boolean;
  /** The colour of the expectation's tier, for a free-text result that failed. */
  tier?: TierLook;
  /** The expectation's own sentence, for a free-text result. */
  expectation?: string;
  onConfirm: (claim: ReviewClaim) => void;
  onReject: (claim: ReviewClaim, reason: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const itemRef = useRef<HTMLLIElement>(null);
  const keepFocus = () => itemRef.current?.focus();
  const noteId = useId();
  const hasSpan = claim.start_s != null && claim.end_s != null;
  const span = hasSpan
    ? {
        reviewId,
        claimId: claim.id,
        start: claim.start_s as number,
        end: claim.end_s as number,
      }
    : null;
  const pinned = controls.pinnedClaimId === claim.id;
  const label =
    claim.kind === "prediction"
      ? "Compared with the prediction"
      : claim.window_text || claim.phase || "the whole shot";
  const result = claim.kind === "free_text" ? (claim.held ? "held" : "failed") : null;
  const failedLook = result === "failed" ? (tier ?? "amber") : null;

  return (
    // The pointer handlers are a mouse convenience on top of the span button's own hover, focus
    // and press, which are the keyboard and phone paths.
    <li
      ref={itemRef}
      id={`claim-${claim.id}`}
      tabIndex={-1}
      className={cn(
        "min-w-0 scroll-mt-24 space-y-1.5 rounded-md border bg-background px-2.5 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
        failedLook === "red" && "border-status-bad/40",
        failedLook === "amber" && "border-status-warn/40",
        failedLook === "grey" && "border-border",
        failedLook === null && "border-border",
      )}
      data-testid="claim"
      data-claim-id={claim.id}
      data-kind={claim.kind}
      data-status={claim.status}
      data-supported={claim.supported ? "yes" : "no"}
      onMouseEnter={span ? () => controls.look(span) : undefined}
      onMouseLeave={span ? () => controls.look(null) : undefined}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        {span ? (
          <button
            type="button"
            className={cn(
              "inline-flex min-w-0 max-w-full items-center gap-1 rounded-md px-1 py-0.5 font-medium",
              "hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              pinned && "bg-muted",
            )}
            aria-pressed={pinned}
            aria-describedby={noteId}
            data-testid="claim-span"
            title="Show this span on the curve"
            onFocus={() => controls.look(span)}
            onBlur={() => controls.look(null)}
            onClick={() => controls.pin(span)}
          >
            <Crosshair className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 truncate" data-testid="claim-window">
              {label}
            </span>
          </button>
        ) : (
          <span className="min-w-0 max-w-full truncate px-1 font-medium" data-testid="claim-window">
            {label}
          </span>
        )}
        <span id={noteId} className="sr-only">
          {span
            ? `Marks ${span.start.toFixed(1)} to ${span.end.toFixed(1)} seconds on the curve`
            : "This claim has no span on the curve"}
        </span>
        {claim.fault ? (
          <Badge variant="outline" data-testid="claim-fault">
            {claim.fault}
          </Badge>
        ) : null}
        {result ? (
          <Badge
            variant="outline"
            data-testid="claim-result"
            data-result={result}
            className={cn(
              result === "held"
                ? "border-status-good/40 bg-status-good/10 text-status-good-text"
                : failedLook === "red"
                  ? "border-status-bad/40 bg-status-bad/10 text-status-bad-text"
                  : failedLook === "grey"
                    ? "text-muted-foreground"
                    : "border-status-warn/40 bg-status-warn/10 text-status-warn-text",
            )}
          >
            {HELD_TEXT[result]}
          </Badge>
        ) : null}
        {claim.kind === "prediction" && claim.stance ? (
          <Badge variant="outline" data-testid="claim-stance">
            {STANCE_LABEL[claim.stance]}
          </Badge>
        ) : null}
        <Badge variant="outline" className={statusClass(claim.status)} data-testid="claim-status">
          {STATUS_LABEL[claim.status]}
        </Badge>
        {claim.supported ? null : (
          <Badge
            variant="outline"
            className="border-status-warn/40 bg-status-warn/10 text-status-warn-text"
            data-testid="claim-unsupported"
          >
            the numbers don't bear this out
          </Badge>
        )}
      </div>

      {expectation ? (
        <p className="break-words text-muted-foreground text-xs" data-testid="claim-expectation">
          {expectation}
        </p>
      ) : null}
      <p className="break-words" data-testid="claim-text">
        {claim.text}
      </p>
      {claim.evidence && claim.evidence.length > 0 ? (
        <ul className="space-y-0.5" data-testid="claim-evidence-list">
          {claim.evidence.map((item, index) => (
            <EvidenceLine
              // biome-ignore lint/suspicious/noArrayIndexKey: the evidence is the server's list and never reordered here
              key={index}
              item={item}
            />
          ))}
        </ul>
      ) : null}
      {claim.status === "rejected" && claim.reason ? (
        <p className="break-words text-muted-foreground text-xs" data-testid="claim-reason">
          Rejected: {claim.reason}
        </p>
      ) : null}

      {rejecting ? (
        <ReasonForm
          testId="claim-reject-form"
          label={`Why reject the claim about ${label}`}
          busy={busy}
          onCancel={() => {
            keepFocus();
            setRejecting(false);
          }}
          onSubmit={(reason) => {
            keepFocus();
            onReject(claim, reason);
            setRejecting(false);
          }}
        />
      ) : (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {claim.status !== "confirmed" ? (
            <Button
              type="button"
              size="sm"
              variant={claim.status === "proposed" ? "default" : "outline"}
              // Not `disabled`: see the Signature card, a second press must not drop focus.
              aria-disabled={busy}
              className="aria-disabled:opacity-50"
              data-testid="claim-confirm"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                if (busy) return;
                keepFocus();
                onConfirm(claim);
              }}
            >
              <Check className="size-3.5" aria-hidden="true" />
              {claim.status === "rejected" ? "Change to confirm" : "Confirm"}
            </Button>
          ) : null}
          {claim.status !== "rejected" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-disabled={busy}
              className="aria-disabled:opacity-50"
              data-testid="claim-reject"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                if (busy) return;
                setRejecting(true);
              }}
            >
              <X className="size-3.5" aria-hidden="true" />
              {claim.status === "confirmed" ? "Change to reject" : "Reject"}
            </Button>
          ) : null}
        </div>
      )}
    </li>
  );
}

const STANCE_LABEL: Record<NonNullable<ReviewClaim["stance"]>, string> = {
  as_predicted: "as predicted",
  partly: "partly as predicted",
  against: "against the prediction",
  not_shown: "not shown by this shot",
};
