import { Loader2 } from "lucide-react";
import { useId } from "react";
import type { ReviewBlock } from "@/api/types";
import {
  EntriesText,
  entryLine,
  reviewTone,
  sentences,
  TONE_CLASS,
} from "@/components/shots/badgeParts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * What the shots table's Review column says: only what the model wrote, never a deterministic
 * check (that is the Curve check column's).
 *
 * One component for every state, so a state is added here and not beside it. The text is the
 * server's (`review.badge`, built by code from the review's faults or its state, never by a
 * model) and what this adds is the look and the controls:
 *
 * - **Not reviewed:** a **Review** button (a button, not a badge) when the table gives it
 *   something to start, nothing otherwise.
 * - **Reviewing…:** a grey badge with a spinner, inert (`aria-disabled`).
 * - **Failed:** a grey `Failed to run`, the reason in the tooltip, and a **Retry** button.
 * - **Reviewed:** the model's faults in "phase: fault +N" form, tinted by the first entry's
 *   severity, or a green `As intended`, or a grey `No faults`. Pressing it opens the shot's row
 *   with its Review box expanded.
 * - **Discarded or quarantined:** nothing at all.
 *
 * The buttons stop propagation, so the row behind never toggles. The full list of faults is the
 * hover (`title` on the wrapper, which is not the described element; the review's summary last,
 * labelled as the model's words)
 * and, for a screen reader, one sentence the badge `aria-describedby`s, so it is read once. As
 * a button the description is `hidden`, not `sr-only`: `aria-describedby` still reads hidden
 * content, whereas an `sr-only` sentence is also in the reading order and would be announced on
 * the button and again when reading on.
 */
export function ReviewBadge({
  review,
  onStart,
  onOpen,
  className,
}: {
  review: ReviewBlock | undefined;
  /** Starts a review (the Review and Retry buttons). Given only by the shots table. */
  onStart?: () => void;
  /** Opens the review (pressing a reviewed badge). Given only by the shots table. */
  onOpen?: () => void;
  className?: string;
}) {
  const listId = useId();
  if (!review || review.state === "not_reviewable") return null;

  if (review.state === "unreviewed") {
    return onStart ? (
      <span className={cn("inline-flex", className)} data-testid="review-badge-wrap">
        <ControlButton label="Review" onPress={onStart} testId="review-start" />
      </span>
    ) : null;
  }

  const entries = review.entries;
  const lines = [
    ...(review.state === "failed"
      ? [review.reason ? `Failed to run: ${review.reason}` : "Failed to run"]
      : entries.map(entryLine)),
    // Labelled as the box labels it: the model's words, and under "Failed to run" the earlier
    // review still in force, never the failed attempt.
    ...(review.summary
      ? [
          review.state === "failed"
            ? `Last review: ${review.summary}`
            : `The model's summary: ${review.summary}`,
        ]
      : []),
  ];
  const showsEntries = review.state === "reviewed" && review.verdict === "entries";
  const tone = reviewTone(review);
  const text = review.badge ?? "";
  const running = review.state === "running";
  const press = review.state === "reviewed" && onOpen !== undefined;
  const body = (
    <>
      {running ? (
        <Loader2
          className="mr-1 size-3 shrink-0 animate-spin"
          aria-hidden="true"
          data-testid="review-badge-spinner"
        />
      ) : null}
      {showsEntries ? (
        <EntriesText text={text} entries={entries} testIdPrefix="review-badge" />
      ) : (
        <span className="min-w-0 truncate">{text}</span>
      )}
    </>
  );
  const common = {
    "data-testid": "review-badge",
    "data-tone": tone,
    "data-state": review.state,
    "aria-describedby": listId,
  } as const;
  const look = cn(
    "min-w-0 max-w-full shrink gap-0",
    TONE_CLASS[tone][running ? "outlined" : "filled"],
  );

  return (
    <span
      className={cn("inline-flex min-w-0 max-w-full items-center gap-1 overflow-hidden", className)}
      title={lines.join("\n") || undefined}
      data-testid="review-badge-wrap"
    >
      {press ? (
        <Badge variant="outline" asChild className={cn(look, "cursor-pointer")}>
          <button
            type="button"
            {...common}
            onClick={(event) => {
              // The row behind is a toggle: a press on the badge is never the row's.
              event.stopPropagation();
              onOpen();
            }}
          >
            {body}
          </button>
        </Badge>
      ) : (
        <Badge
          variant="outline"
          {...common}
          aria-disabled={running ? true : undefined}
          className={cn(look, running && "cursor-progress")}
        >
          {body}
        </Badge>
      )}
      {review.state === "failed" && onStart ? (
        <ControlButton label="Retry" onPress={onStart} testId="review-retry" />
      ) : null}
      <span id={listId} className={press ? "hidden" : "sr-only"} data-testid="review-badge-list">
        {sentences(lines)}
      </span>
    </span>
  );
}

/** A small outlined button that starts a review and never toggles the row behind it. */
function ControlButton({
  label,
  onPress,
  testId,
}: {
  label: string;
  onPress: () => void;
  testId: string;
}) {
  return (
    <Button
      type="button"
      size="xs"
      variant="outline"
      data-testid={testId}
      onClick={(event) => {
        event.stopPropagation();
        onPress();
      }}
    >
      {label}
    </Button>
  );
}
