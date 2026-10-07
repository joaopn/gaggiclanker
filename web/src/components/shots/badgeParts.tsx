import type { ReactNode } from "react";
import type { ReviewBlock, ShotEntry } from "@/api/types";

/**
 * What the Curve check badge and the Review badge share: the tone, the look of one, and how an
 * entry reads ("phase: fault") with the part that may be cut.
 *
 * The text of a badge is always the server's (built by code from the entries, never by a model);
 * this module only decides how it is drawn.
 */

export type Tone = "warn" | "bad" | "muted" | "good";

/** Filled is the tint a badge has; outlined keeps the border and drops the fill. */
export const TONE_CLASS: Record<Tone, { filled: string; outlined: string }> = {
  warn: {
    filled: "border-status-warn/40 bg-status-warn/10 text-status-warn-text",
    outlined: "border-status-warn/60 bg-transparent text-status-warn-text",
  },
  bad: {
    filled: "border-status-bad/40 bg-status-bad/10 text-status-bad-text",
    outlined: "border-status-bad/60 bg-transparent text-status-bad-text",
  },
  good: {
    filled: "border-status-good/40 bg-status-good/10 text-status-good-text",
    outlined: "border-status-good/60 bg-transparent text-status-good-text",
  },
  muted: {
    filled: "border-border bg-muted/50 text-muted-foreground",
    outlined: "border-border bg-transparent text-muted-foreground",
  },
};

/** The badge's colour: the first entry's severity, and amber for one the server did not name. */
export function toneOf(severity: string | undefined): Tone {
  if (severity === "red") return "bad";
  if (severity === "grey") return "muted";
  return "warn";
}

/**
 * The tone of the review's badge: the first fault's severity for a review that found faults,
 * green for "As intended", grey for "No faults" and for every state that is not a verdict.
 */
export function reviewTone(review: ReviewBlock): Tone {
  if (review.state !== "reviewed") return "muted";
  if (review.verdict === "entries") return toneOf(review.entries[0]?.severity);
  return review.verdict === "as_intended" ? "good" : "muted";
}

/** One entry as a line: "ramp: fast flow — the sentence with the numbers". */
export function entryLine(entry: ShotEntry): string {
  return `${entry.phase}: ${entry.fault} — ${entry.detail}`;
}

/** The lines as one text: each ends in a full stop, and one that already does gets no second. */
export function sentences(lines: string[]): string {
  return lines.map((line) => (/[.!?]$/.test(line) ? line : `${line}.`)).join(" ");
}

/**
 * The text of a badge made of entries: "phase: fault" and "+N" for the rest.
 *
 * The count sits outside the span that truncates, so a long phase name is cut and the count is
 * not; and the phase is the only part of "phase: fault" that may be cut, because the fault is what
 * the badge is for. One flexible box holds the phase and the fault, and the count stays outside
 * it. The phase is the only part that shrinks (`truncate`); the fault keeps its own width and is
 * clipped by the box only once the phase is gone, with an ellipsis of its own. Shrink factors are
 * not used: flex shares a shortfall out by factor times width, so a fault that is meant to stay
 * whole loses a fraction of a pixel while the phase is cut.
 */
export function EntriesText({
  text,
  entries,
  testIdPrefix,
}: {
  /** The server's text, "<phase>: <fault>" with " +N" when there are more. */
  text: string;
  entries: ShotEntry[];
  testIdPrefix: string;
}): ReactNode {
  const first = entries[0];
  const more = entries.length > 1 ? ` +${entries.length - 1}` : "";
  const lead = more && text.endsWith(more) ? text.slice(0, -more.length) : text;
  const phase = first?.phase ?? "";
  const split = phase !== "" && lead.startsWith(`${phase}: `);
  return (
    <>
      {split ? (
        <span className="flex min-w-0 flex-1 overflow-hidden" data-testid={`${testIdPrefix}-text`}>
          <span className="min-w-0 truncate" data-testid={`${testIdPrefix}-phase`}>
            {phase}
          </span>
          <span
            className="max-w-full shrink-0 truncate whitespace-pre"
            data-testid={`${testIdPrefix}-fault`}
          >
            {lead.slice(phase.length)}
          </span>
        </span>
      ) : (
        <span className="min-w-0 truncate">{lead}</span>
      )}
      {more ? <span className="shrink-0 whitespace-pre">{more}</span> : null}
    </>
  );
}
